from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter()
REPOSITORY_STORE: list[dict[str, Any]] = []


class RepositoryRegistrationRequest(BaseModel):
    repository_url: str = Field(..., description="GitHub repo URL, e.g. https://github.com/owner/repo")
    github_token: str | None = Field(default=None, description="Optional PAT for auto-installing the GitHub webhook")
    org_id: str = Field(..., min_length=1, description="Tenant or organization identifier")
    auto_install_webhook: bool = Field(default=True, description="Whether to auto-register the GitHub webhook")


class RepositorySummary(BaseModel):
    owner: str
    name: str
    url: str


class RepositoryRegistrationResponse(BaseModel):
    repository: RepositorySummary
    org_id: str
    webhook_url: str
    webhook_secret: str
    auto_install_webhook: bool
    github_hook_id: str | None = None
    status: str


def _parse_repository_url(repository_url: str) -> tuple[str, str]:
    parsed = urlparse(repository_url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Repository URL must point to github.com")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        raise ValueError("Repository URL must include both owner and repo name")

    owner, repo_name = path_parts[0], path_parts[1]
    return owner, repo_name


def _make_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


async def _save_repository_record(record: dict[str, Any]) -> dict[str, Any]:
    if settings.supabase_url and settings.supabase_service_role_key:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/repositories",
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

    REPOSITORY_STORE.append(record)
    return record


async def _register_github_webhook(
    owner: str, repo_name: str, github_token: str, webhook_url: str, webhook_secret: str
) -> str | None:
    if not github_token:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"https://api.github.com/repos/{owner}/{repo_name}/hooks",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            json={
                "name": "web",
                "active": True,
                "events": ["pull_request"],
                "config": {
                    "url": webhook_url,
                    "content_type": "json",
                    "secret": webhook_secret,
                    "insecure_ssl": "0",
                },
            },
        )

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    payload = response.json()
    return str(payload.get("id"))


@router.post("/repos/register", response_model=RepositoryRegistrationResponse)
async def register_repository(payload: RepositoryRegistrationRequest) -> RepositoryRegistrationResponse:
    try:
        owner, repo_name = _parse_repository_url(payload.repository_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    webhook_secret = _make_webhook_secret()
    github_hook_id = None
    status = "manual_setup_required"

    if payload.auto_install_webhook:
        if payload.github_token:
            github_hook_id = await _register_github_webhook(
                owner=owner,
                repo_name=repo_name,
                github_token=payload.github_token,
                webhook_url=settings.gitstream_webhook_url,
                webhook_secret=webhook_secret,
            )
            status = "hook_registered"
        else:
            status = "manual_setup_required"

    record = {
        "owner": owner,
        "repository": repo_name,
        "repository_url": payload.repository_url,
        "full_name": f"{owner}/{repo_name}",
        "org_id": payload.org_id,
        "github_token": payload.github_token,
        "webhook_secret": webhook_secret,
        "webhook_url": settings.gitstream_webhook_url,
        "auto_install_webhook": payload.auto_install_webhook,
        "github_hook_id": github_hook_id,
        "status": status,
    }
    await _save_repository_record(record)

    return RepositoryRegistrationResponse(
        repository=RepositorySummary(owner=owner, name=repo_name, url=payload.repository_url),
        org_id=payload.org_id,
        webhook_url=settings.gitstream_webhook_url,
        webhook_secret=webhook_secret,
        auto_install_webhook=payload.auto_install_webhook,
        github_hook_id=github_hook_id,
        status=status,
    )
