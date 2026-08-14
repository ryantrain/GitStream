from __future__ import annotations

from datetime import datetime
from math import ceil
from statistics import mean, median
from typing import Any
from urllib.parse import quote

import requests

GITHUB_API_URL = "https://api.github.com"


class GithubHistoryError(RuntimeError):
    pass


def _parse_github_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]

    idx = ceil(q * len(sorted_values)) - 1
    idx = max(0, min(idx, len(sorted_values) - 1))
    return sorted_values[idx]


def _risk_band(hours: float) -> str:
    if hours < 12:
        return "low"
    if hours < 36:
        return "medium"
    return "high"


def fetch_closed_pr_history(
    owner: str,
    repository: str,
    lookback_prs: int,
    github_token: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    owner_slug = quote(owner)
    repo_slug = quote(repository)

    while len(results) < lookback_prs:
        response = requests.get(
            f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls",
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            },
            headers=headers,
            timeout=20,
        )

        if response.status_code == 404:
            raise GithubHistoryError("Repository not found.")
        if response.status_code == 403:
            raise GithubHistoryError(
                "GitHub API rate limit hit. Add a token and retry."
            )
        if response.status_code >= 400:
            raise GithubHistoryError(
                f"GitHub API returned status {response.status_code}."
            )

        page_items = response.json()
        if not isinstance(page_items, list) or not page_items:
            break

        results.extend(page_items)
        page += 1

    return results[:lookback_prs]


def estimate_from_pr_history(prs: list[dict[str, Any]]) -> dict[str, float | int | str]:
    merged_durations: list[float] = []

    for pr in prs:
        created = _parse_github_timestamp(pr.get("created_at"))
        merged = _parse_github_timestamp(pr.get("merged_at"))
        if not created or not merged:
            continue

        duration_hours = (merged - created).total_seconds() / 3600.0
        if duration_hours >= 0:
            merged_durations.append(duration_hours)

    if not merged_durations:
        raise GithubHistoryError("No merged pull requests found in the selected sample.")

    merged_durations.sort()
    avg_hours = mean(merged_durations)
    med_hours = median(merged_durations)
    p75 = _percentile(merged_durations, 0.75)
    p90 = _percentile(merged_durations, 0.90)

    # Weighted estimate favors p75 to hedge against queue risk.
    estimate_next = 0.5 * med_hours + 0.5 * p75

    return {
        "merged_pr_count": len(merged_durations),
        "sample_window_pr_count": len(prs),
        "average_merge_hours": round(avg_hours, 2),
        "median_merge_hours": round(med_hours, 2),
        "p75_merge_hours": round(p75, 2),
        "p90_merge_hours": round(p90, 2),
        "estimate_next_pr_hours": round(estimate_next, 2),
        "risk_band": _risk_band(estimate_next),
    }


def fetch_active_prs(
    owner: str,
    repository: str,
    github_token: str | None = None,
) -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    response = requests.get(
        f"{GITHUB_API_URL}/repos/{quote(owner)}/{quote(repository)}/pulls",
        params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 100},
        headers=headers,
        timeout=20,
    )

    if response.status_code == 404:
        raise GithubHistoryError("Repository not found.")
    if response.status_code == 403:
        raise GithubHistoryError("GitHub API rate limit hit. Add a token and retry.")
    if response.status_code >= 400:
        raise GithubHistoryError(f"GitHub API returned status {response.status_code}.")

    prs = response.json()
    if not isinstance(prs, list):
        return []
    return prs


def build_active_pr_prediction_rows(
    prs: list[dict[str, Any]],
    baseline_hours: float = 24.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for pr in prs:
        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
        changed_files = int(pr.get("changed_files") or 0)
        requested_reviewers = pr.get("requested_reviewers") or []
        requested_teams = pr.get("requested_teams") or []
        requested_reviewers_count = len(requested_reviewers) + len(requested_teams)
        author = (pr.get("user") or {}).get("login") or "unknown"
        title = pr.get("title") or "Untitled PR"

        complexity_weight = 0.08 * additions + 0.06 * deletions + 0.9 * changed_files
        reviewer_weight = 1.4 * requested_reviewers_count
        predicted_hours = max(baseline_hours + complexity_weight + reviewer_weight, 2.0)

        if predicted_hours < 12:
            risk_band = "low"
        elif predicted_hours < 36:
            risk_band = "medium"
        else:
            risk_band = "high"

        rows.append(
            {
                "number": int(pr.get("number") or 0),
                "title": title,
                "author": author,
                "additions": additions,
                "deletions": deletions,
                "changed_files": changed_files,
                "requested_reviewers_count": requested_reviewers_count,
                "predicted_merge_hours": round(predicted_hours, 2),
                "risk_band": risk_band,
                "url": pr.get("html_url") or "",
                "draft": bool(pr.get("draft")),
            }
        )

    return rows
