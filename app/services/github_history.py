from __future__ import annotations

from datetime import UTC, datetime
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
                "GitHub API rate limit hit. If you haven't configured a token, add a personal access token to your .env file (GITHUB_TOKEN) for more requests."
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


def _fetch_pr_detail(
    owner_slug: str,
    repo_slug: str,
    pr_number: int,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    """Fetch a single PR's detail to get additions/deletions/changed_files."""
    response = requests.get(
        f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls/{pr_number}",
        headers=headers,
        timeout=20,
    )
    if response.status_code == 200:
        return response.json()
    return None


def fetch_open_pull_requests(
    owner: str,
    repository: str,
    github_token: str | None = None,
    limit: int = 100,
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

    while len(results) < limit:
        response = requests.get(
            f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls",
            params={
                "state": "open",
                "sort": "created",
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
                "GitHub API rate limit hit. If you haven't configured a token, add a personal access token to your .env file (GITHUB_TOKEN) for more requests."
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

    results = results[:limit]

    # The list endpoint does not return additions/deletions/changed_files.
    # Fetch individual PR details to get those fields.
    for i, pr in enumerate(results):
        pr_number = pr.get("number")
        if not pr_number:
            continue
        detail = _fetch_pr_detail(owner_slug, repo_slug, pr_number, headers)
        if detail:
            results[i]["additions"] = detail.get("additions", 0)
            results[i]["deletions"] = detail.get("deletions", 0)
            results[i]["changed_files"] = detail.get("changed_files", 0)

    return results


def estimate_from_pr_history(prs: list[dict[str, Any]]) -> dict[str, float | int | str]:
    merged_durations: list[float] = []
    # Track (change_size, duration) pairs to build size-bucketed baselines.
    size_duration_pairs: list[tuple[int, float]] = []

    for pr in prs:
        created = _parse_github_timestamp(pr.get("created_at"))
        merged = _parse_github_timestamp(pr.get("merged_at"))
        if not created or not merged:
            continue

        duration_hours = (merged - created).total_seconds() / 3600.0
        if duration_hours >= 0:
            merged_durations.append(duration_hours)
            change_size = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
            size_duration_pairs.append((change_size, duration_hours))

    if not merged_durations:
        raise GithubHistoryError("No merged pull requests found in the selected sample.")

    merged_durations.sort()
    avg_hours = mean(merged_durations)
    med_hours = median(merged_durations)
    p75 = _percentile(merged_durations, 0.75)
    p90 = _percentile(merged_durations, 0.90)

    # Weighted estimate favors p75 to hedge against queue risk.
    estimate_next = 0.5 * med_hours + 0.5 * p75

    # Build size-bucketed baselines so we can match open PRs to similarly
    # sized historical PRs for more accurate estimates.
    # Buckets: small (<100 lines), medium (100-500), large (500-1500), xl (>1500)
    size_buckets: dict[str, list[float]] = {
        "small": [],
        "medium": [],
        "large": [],
        "xl": [],
    }
    for size, duration in size_duration_pairs:
        if size < 100:
            size_buckets["small"].append(duration)
        elif size < 500:
            size_buckets["medium"].append(duration)
        elif size < 1500:
            size_buckets["large"].append(duration)
        else:
            size_buckets["xl"].append(duration)

    size_bucket_stats: dict[str, dict[str, float]] = {}
    for bucket, durations in size_buckets.items():
        if durations:
            sorted_d = sorted(durations)
            size_bucket_stats[bucket] = {
                "median_hours": median(sorted_d),
                "p75_hours": _percentile(sorted_d, 0.75),
                "count": len(sorted_d),
            }

    return {
        "merged_pr_count": len(merged_durations),
        "sample_window_pr_count": len(prs),
        "average_merge_hours": round(avg_hours, 2),
        "median_merge_hours": round(med_hours, 2),
        "p75_merge_hours": round(p75, 2),
        "p90_merge_hours": round(p90, 2),
        "estimate_next_pr_hours": round(estimate_next, 2),
        "risk_band": _risk_band(estimate_next),
        "size_bucket_stats": size_bucket_stats,
    }


def _estimate_review_effort_hours(
    additions: int,
    deletions: int,
    changed_files: int,
) -> float:
    """Estimate active review effort: how long a reviewer sits and reads the diff.

    Based on empirical code-review research:
    - Effective review rate is ~200-400 LOC/hour for net-new logic.
    - Deletions are faster to verify (~3x faster) — you're confirming removal,
      not understanding new logic.
    - Context-switching between files costs ~3-5 min per file.
    - Very large diffs (>5000 lines) often contain mechanical/generated changes
      that scan faster, so throughput increases for bulk.
    - Minimum realistic review: ~10 min (for trivial PRs).
    """
    # Lines that need careful reading (additions) vs quick scan (deletions).
    # Additions require understanding new logic; deletions just need
    # confirmation that nothing important was removed.
    careful_lines = additions
    scan_lines = deletions

    # Review throughput (lines/hour) — decreases for complex diffs,
    # increases for very large bulk changes (likely generated/mechanical).
    if careful_lines <= 200:
        # Small PRs: reviewer is fresh, can go ~400 LOC/h
        careful_rate = 400.0
    elif careful_lines <= 1000:
        # Medium PRs: attention starts to degrade, ~300 LOC/h
        careful_rate = 300.0
    elif careful_lines <= 5000:
        # Large PRs: significant cognitive load, ~200 LOC/h
        careful_rate = 200.0
    else:
        # Very large PRs: likely contains mechanical/generated sections.
        # First 5000 lines at 200 LOC/h, rest at 600 LOC/h (scanning).
        careful_rate = None  # handled below

    # Deletions scan at ~800 LOC/h (you're verifying, not understanding)
    scan_rate = 800.0

    if careful_rate is not None:
        reading_hours = careful_lines / careful_rate
    else:
        # Tiered: first 5k lines are slow, rest is faster scanning
        reading_hours = 5000 / 200.0 + (careful_lines - 5000) / 600.0

    deletion_hours = scan_lines / scan_rate

    # Context-switching cost: ~4 minutes per file on average.
    # First file is "free" (you're already there).
    file_switch_hours = max(changed_files - 1, 0) * (4.0 / 60.0)

    # Comprehension overhead: understanding how files relate to each other.
    # Grows sub-linearly — 10 files is harder than 2, but 50 isn't 5x harder than 10.
    if changed_files > 5:
        comprehension_hours = (changed_files - 5) ** 0.6 * (5.0 / 60.0)
    else:
        comprehension_hours = 0.0

    # Merge mechanics: reading CI, resolving trivial issues, clicking merge.
    merge_overhead_hours = 5.0 / 60.0  # ~5 minutes

    total = (
        reading_hours
        + deletion_hours
        + file_switch_hours
        + comprehension_hours
        + merge_overhead_hours
    )

    # Floor: even a 1-line PR takes ~10 minutes to context-switch into,
    # read, confirm, and merge.
    return max(total, 10.0 / 60.0)


def build_active_pr_estimates(
    open_prs: list[dict[str, Any]],
    history_estimate: dict[str, float | int | str],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now_utc = now or datetime.now(UTC)

    results: list[dict[str, Any]] = []

    for pr in open_prs:
        created = _parse_github_timestamp(pr.get("created_at"))
        if not created:
            continue

        created_utc = created.replace(tzinfo=UTC)
        age_hours = max((now_utc - created_utc).total_seconds() / 3600.0, 0.0)

        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
        changed_files = int(pr.get("changed_files") or 1)
        requested_reviewers = len(pr.get("requested_reviewers") or [])
        is_draft = bool(pr.get("draft"))
        author = str((pr.get("user") or {}).get("login") or "unknown")

        # --- Core estimate: active review effort ---
        review_hours = _estimate_review_effort_hours(additions, deletions, changed_files)

        # Risk band based on review effort:
        # low: <1h (quick review), medium: 1-4h (substantial), high: >4h (major effort)
        risk = _review_effort_risk_band(review_hours)

        results.append(
            {
                "number": int(pr.get("number") or 0),
                "title": str(pr.get("title") or "Untitled PR"),
                "author": author,
                "html_url": str(pr.get("html_url") or ""),
                "created_at": created_utc.isoformat(),
                "age_hours": round(age_hours, 2),
                "additions": additions,
                "deletions": deletions,
                "changed_files": changed_files,
                "requested_reviewers": requested_reviewers,
                "is_draft": is_draft,
                "review_effort_hours": round(review_hours, 2),
                # Keep these for API backward compat; both now reflect effort.
                "historical_baseline_hours": round(review_hours, 2),
                "complexity_multiplier": 1.0,
                "estimated_total_merge_hours": round(review_hours, 2),
                "estimated_remaining_hours": round(review_hours, 2),
                "risk_band": risk,
            }
        )

    results.sort(key=lambda item: item["review_effort_hours"], reverse=True)
    return results


def _review_effort_risk_band(hours: float) -> str:
    """Risk based on how much active reviewer time is needed."""
    if hours < 1.0:
        return "low"
    if hours <= 4.0:
        return "medium"
    return "high"
