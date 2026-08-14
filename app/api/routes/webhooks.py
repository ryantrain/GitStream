from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.api.routes.repos import REPOSITORY_STORE
from app.core.config import settings
from app.services.merge_prediction_model import MergeDelayModel

router = APIRouter()
PULL_REQUEST_STORE: list[dict[str, Any]] = []


def _find_repository_secret(repository_full_name: str) -> str | None:
    for record in REPOSITORY_STORE:
        if record.get("full_name") == repository_full_name:
            return str(record.get("webhook_secret") or "")
    return None


async def _save_pull_request_record(record: dict[str, Any]) -> dict[str, Any]:
    if settings.supabase_url and settings.supabase_service_role_key:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/pull_requests",
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                json=record,
            )
            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            payload = response.json()
            if isinstance(payload, list) and payload:
                return payload[0]
            return record

    PULL_REQUEST_STORE.append(record)
    return record


async def _post_pr_comment(owner: str, repo_name: str, pr_number: int, token: str, comment: str) -> None:
    if not token:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            json={"body": comment},
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    repository = payload.get("repository") or {}
    repo_full_name = repository.get("full_name")
    if not repo_full_name:
        raise HTTPException(status_code=400, detail="Repository metadata is missing")

    webhook_secret = _find_repository_secret(repo_full_name)
    if not webhook_secret:
        raise HTTPException(status_code=404, detail=f"Repository {repo_full_name} is not registered")

    expected_signature = "sha256=" + hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=403, detail="GitHub webhook signature mismatch")

    action = payload.get("action")
    if action not in {"opened", "synchronize", "reopened"}:
        return {"status": "ignored", "action": action, "message": "Webhook action is not part of the PR prediction pipeline"}

    pr = payload.get("pull_request") or {}
    feature_payload = {
        "additions": int(pr.get("additions", 0) or 0),
        "deletions": int(pr.get("deletions", 0) or 0),
        "changed_files": int(pr.get("changed_files", 0) or 0),
        "requested_reviewers_count": len(pr.get("requested_reviewers") or []) + len(pr.get("requested_teams") or []),
        "author_merge_hours": 16.0,
    }
    model = MergeDelayModel()
    predicted_hours = model.predict(feature_payload)
    risk_status = "green" if predicted_hours < 24.0 else "red"

    repository_name = repo_full_name.split("/", 1)[1]
    owner_name = repo_full_name.split("/", 1)[0]
    pr_number = int(pr.get("number", 0) or 0)
    author_username = (pr.get("user") or {}).get("login") or "unknown" 
    title = pr.get("title") or "Untitled PR"

    if token_record := next((rec for rec in REPOSITORY_STORE if rec.get("full_name") == repo_full_name), None):
        github_token = str(token_record.get("github_token") or "")
    else:
        github_token = ""

    comment_text = (
        f"GitStream prediction: {predicted_hours:.1f} hours to merge. "
        f"Risk status: {risk_status.upper()}. "
        f"Bottlenecks: review queue, change size, and author turnaround."
    )
    await _post_pr_comment(owner_name, repository_name, pr_number, github_token, comment_text)

    row = {
        "repository_full_name": repo_full_name,
        "repository_owner": owner_name,
        "repository_name": repository_name,
        "pr_number": pr_number,
        "pr_title": title,
        "author_username": author_username,
        "risk_status": risk_status,
        "predicted_merge_hours": round(predicted_hours, 2),
        "additions": feature_payload["additions"],
        "deletions": feature_payload["deletions"],
        "changed_files": feature_payload["changed_files"],
        "requested_reviewers_count": feature_payload["requested_reviewers_count"],
        "payload": payload,
        "source": "github-webhook",
    }
    await _save_pull_request_record(row)

    return {
        "status": "accepted",
        "repository": repo_full_name,
        "pr_number": pr_number,
        "predicted_merge_hours": round(predicted_hours, 2),
        "risk_status": risk_status,
        "message": "PR metrics captured and prediction recorded",
    }
