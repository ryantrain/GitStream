"""GitHub API access for the estimation pipeline.

Split out from ``github_history`` so that transport concerns (pagination,
retries, rate limits, concurrency) live apart from the statistics. The previous
combined module issued one extra REST request per open pull request to recover
``additions``/``deletions``/``changed_files``, so a repository with 100 open PRs
cost 101 sequential round trips — slow enough to look like a hung page, and
enough to exhaust the 60 requests/hour unauthenticated quota on a single run.

Two fetch strategies are provided:

* **GraphQL** (used when a token is available): one request per 50 pull
  requests, returning size fields *and* the changed-file list. The file list
  enables generated/vendored file detection in the effort model.
* **REST + thread pool** (unauthenticated fallback): the original endpoints, but
  with detail requests issued concurrently over pooled connections. File lists
  are skipped here because the extra quota is not available.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5

# Concurrency for the REST fallback detail fetches. Kept modest: GitHub applies
# secondary rate limits to bursts of concurrent requests from one client.
DETAIL_FETCH_WORKERS = 6

# GraphQL page size. 50 PRs x 100 files stays well inside the node limit.
GRAPHQL_PAGE_SIZE = 50
GRAPHQL_FILES_PER_PR = 100

# Hard ceiling on REST pagination so a malformed response cannot spin forever.
MAX_PAGES = 40


class GithubHistoryError(RuntimeError):
    """A user-facing failure while talking to GitHub."""


@dataclass(frozen=True)
class RateLimit:
    """Snapshot of the caller's remaining GitHub quota."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None

    @property
    def is_exhausted(self) -> bool:
        return self.remaining is not None and self.remaining <= 0

    @property
    def is_low(self) -> bool:
        """Whether the remaining quota is small enough to warn the user about."""
        return self.remaining is not None and self.remaining < 25


@dataclass
class PullRequestBatch:
    """A page of pull requests plus provenance about how it was gathered.

    ``truncated`` and ``total_available`` exist so the UI can say "showing 100
    of 143 open PRs" instead of silently reporting a capped count as the truth.
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    total_available: int | None = None
    truncated: bool = False
    rate_limit: RateLimit | None = None
    used_graphql: bool = False
    file_details_available: bool = False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _build_headers(github_token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitStream-Estimator",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def _session() -> requests.Session:
    """Return a per-thread pooled session.

    Connection reuse removes a TLS handshake per request. A session per thread
    avoids sharing one ``requests.Session`` across the detail-fetch pool.
    """
    existing = getattr(_thread_local, "session", None)
    if existing is None:
        existing = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=DETAIL_FETCH_WORKERS,
            pool_maxsize=DETAIL_FETCH_WORKERS,
        )
        existing.mount("https://", adapter)
        _thread_local.session = existing
    return existing


def _parse_rate_limit(response: requests.Response) -> RateLimit:
    def _int(header: str) -> int | None:
        raw = response.headers.get(header)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    reset_epoch = _int("X-RateLimit-Reset")
    reset_at = datetime.fromtimestamp(reset_epoch, tz=UTC) if reset_epoch is not None else None
    return RateLimit(
        limit=_int("X-RateLimit-Limit"),
        remaining=_int("X-RateLimit-Remaining"),
        reset_at=reset_at,
    )


def _rate_limit_message(rate_limit: RateLimit, has_token: bool) -> str:
    when = ""
    if rate_limit.reset_at is not None:
        when = f" Quota resets at {rate_limit.reset_at:%H:%M UTC}."
    if has_token:
        return f"GitHub API rate limit reached for your token.{when} Try a smaller PR sample."
    return (
        "GitHub API rate limit hit. Unauthenticated requests are limited to 60 per "
        "hour. Add a personal access token to your .env file (GITHUB_TOKEN) for a "
        f"5,000/hour quota.{when}"
    )


def _raise_for_status(response: requests.Response, has_token: bool) -> None:
    """Translate GitHub error responses into user-facing messages."""
    if response.status_code == 404:
        raise GithubHistoryError(
            "Repository not found. Check the owner and repository name, and that "
            "your token can see it if the repository is private."
        )
    if response.status_code == 401:
        raise GithubHistoryError(
            "GitHub rejected the credentials. Check that GITHUB_TOKEN is valid and has not expired."
        )
    if response.status_code in (403, 429):
        rate_limit = _parse_rate_limit(response)
        if rate_limit.is_exhausted or response.status_code == 429:
            raise GithubHistoryError(_rate_limit_message(rate_limit, has_token))
        raise GithubHistoryError("GitHub denied the request (403). The token may lack access to this repository.")
    if response.status_code >= 400:
        raise GithubHistoryError(f"GitHub API returned status {response.status_code}.")


def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    has_token: bool,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> requests.Response:
    """Issue a request, retrying transient failures with backoff.

    Retries cover 5xx responses and GitHub's secondary rate limit, which
    responds with ``Retry-After``. Connection and timeout errors are converted
    into ``GithubHistoryError`` so callers never see a raw requests exception.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = _session().request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            last_error = exc
            logger.warning("GitHub request timed out (attempt %s): %s", attempt + 1, url)
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("GitHub request failed (attempt %s): %s", attempt + 1, exc)
        else:
            retry_after = response.headers.get("Retry-After")
            should_retry = response.status_code >= 500 or (
                response.status_code in (403, 429) and retry_after is not None
            )
            if should_retry and attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_SECONDS * (2**attempt)
                if retry_after is not None:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                logger.info("Retrying GitHub request in %.1fs: %s", delay, url)
                threading.Event().wait(min(delay, 10.0))
                continue
            return response

        if attempt < MAX_RETRIES - 1:
            threading.Event().wait(RETRY_BACKOFF_SECONDS * (2**attempt))

    raise GithubHistoryError(
        "Could not reach the GitHub API. Check your network connection and try again."
    ) from last_error


def check_rate_limit(github_token: str | None = None) -> RateLimit:
    """Read the caller's current quota without spending a REST request.

    ``/rate_limit`` is exempt from the rate limit, so this is a free pre-flight
    check before starting a multi-request estimate run.
    """
    headers = _build_headers(github_token)
    try:
        response = _request("GET", f"{GITHUB_API_URL}/rate_limit", headers, bool(github_token))
    except GithubHistoryError:
        return RateLimit()

    if response.status_code >= 400:
        return RateLimit()

    try:
        core = response.json().get("resources", {}).get("core", {})
    except ValueError:
        return _parse_rate_limit(response)

    reset_epoch = core.get("reset")
    return RateLimit(
        limit=core.get("limit"),
        remaining=core.get("remaining"),
        reset_at=(datetime.fromtimestamp(reset_epoch, tz=UTC) if isinstance(reset_epoch, int) else None),
    )


# ---------------------------------------------------------------------------
# Closed PR history (REST)
# ---------------------------------------------------------------------------


def fetch_closed_pr_history(
    owner: str,
    repository: str,
    lookback_prs: int,
    github_token: str | None = None,
) -> PullRequestBatch:
    """Fetch recently closed pull requests for duration statistics.

    Sorted by ``updated`` descending, which is the closest proxy the REST pulls
    endpoint offers for "most recently closed" — it has no ``sort=merged``. The
    caller is expected to apply a recency window on ``merged_at`` to discard old
    PRs that were bumped back into this ordering by a late comment.
    """
    headers = _build_headers(github_token)
    has_token = bool(github_token)
    owner_slug = quote(owner, safe="")
    repo_slug = quote(repository, safe="")

    results: list[dict[str, Any]] = []
    rate_limit: RateLimit | None = None
    page = 1

    while len(results) < lookback_prs and page <= MAX_PAGES:
        response = _request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls",
            headers,
            has_token,
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        _raise_for_status(response, has_token)
        rate_limit = _parse_rate_limit(response)

        try:
            page_items = response.json()
        except ValueError as exc:
            raise GithubHistoryError("GitHub returned a malformed response.") from exc

        if not isinstance(page_items, list) or not page_items:
            break

        results.extend(page_items)
        if len(page_items) < 100:
            break
        page += 1

    return PullRequestBatch(
        items=results[:lookback_prs],
        total_available=None,
        truncated=len(results) > lookback_prs,
        rate_limit=rate_limit,
    )


# ---------------------------------------------------------------------------
# Open PRs
# ---------------------------------------------------------------------------

_OPEN_PR_GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $pageSize: Int!, $filesPerPr: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: OPEN
      first: $pageSize
      after: $cursor
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        createdAt
        isDraft
        additions
        deletions
        changedFiles
        author { login }
        reviewRequests(first: 1) { totalCount }
        files(first: $filesPerPr) {
          totalCount
          nodes { path additions deletions }
        }
      }
    }
  }
}
"""


def _graphql_node_to_pr(node: dict[str, Any]) -> dict[str, Any]:
    """Normalise a GraphQL PR node into the REST-shaped dict the rest of the
    pipeline expects, so downstream code has a single input format."""
    author = node.get("author") or {}
    review_requests = node.get("reviewRequests") or {}
    files_block = node.get("files") or {}
    file_nodes = files_block.get("nodes") or []

    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "html_url": node.get("url"),
        "created_at": node.get("createdAt"),
        "draft": bool(node.get("isDraft")),
        "additions": node.get("additions") or 0,
        "deletions": node.get("deletions") or 0,
        "changed_files": node.get("changedFiles") or 0,
        "user": {"login": author.get("login") or "unknown"},
        # REST returns a list here; the pipeline only needs its length.
        "requested_reviewers": [None] * int(review_requests.get("totalCount") or 0),
        "files": [
            {
                "path": f.get("path") or "",
                "additions": f.get("additions") or 0,
                "deletions": f.get("deletions") or 0,
            }
            for f in file_nodes
            if isinstance(f, dict)
        ],
        "files_truncated": int(files_block.get("totalCount") or 0) > len(file_nodes),
    }


def _fetch_open_prs_graphql(
    owner: str,
    repository: str,
    github_token: str,
    limit: int,
) -> PullRequestBatch:
    """Fetch open PRs with size and file data in one request per 50 PRs."""
    headers = _build_headers(github_token)
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    total_count: int | None = None
    rate_limit: RateLimit | None = None

    while len(items) < limit:
        response = _request(
            "POST",
            GITHUB_GRAPHQL_URL,
            headers,
            True,
            json_body={
                "query": _OPEN_PR_GRAPHQL_QUERY,
                "variables": {
                    "owner": owner,
                    "name": repository,
                    "pageSize": min(GRAPHQL_PAGE_SIZE, limit - len(items)),
                    "filesPerPr": GRAPHQL_FILES_PER_PR,
                    "cursor": cursor,
                },
            },
        )
        _raise_for_status(response, True)
        rate_limit = _parse_rate_limit(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise GithubHistoryError("GitHub returned a malformed response.") from exc

        if body.get("errors"):
            messages = "; ".join(str(err.get("message", "unknown")) for err in body["errors"])
            raise GithubHistoryError(f"GitHub GraphQL error: {messages}")

        repo_block = (body.get("data") or {}).get("repository")
        if repo_block is None:
            raise GithubHistoryError(
                "Repository not found. Check the owner and repository name, and that "
                "your token can see it if the repository is private."
            )

        pr_block = repo_block.get("pullRequests") or {}
        if total_count is None:
            total_count = pr_block.get("totalCount")

        nodes = pr_block.get("nodes") or []
        items.extend(_graphql_node_to_pr(n) for n in nodes if isinstance(n, dict))

        page_info = pr_block.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    items = items[:limit]
    return PullRequestBatch(
        items=items,
        total_available=total_count,
        truncated=bool(total_count is not None and total_count > len(items)),
        rate_limit=rate_limit,
        used_graphql=True,
        file_details_available=True,
    )


def _fetch_pr_detail(
    owner_slug: str,
    repo_slug: str,
    pr_number: int,
    headers: dict[str, str],
    has_token: bool,
) -> tuple[int, dict[str, Any] | None]:
    """Fetch one PR's detail for additions/deletions/changed_files."""
    try:
        response = _request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls/{pr_number}",
            headers,
            has_token,
        )
    except GithubHistoryError:
        # A single missing detail degrades that row's precision rather than
        # failing the whole run.
        return pr_number, None

    if response.status_code == 200:
        try:
            return pr_number, response.json()
        except ValueError:
            return pr_number, None
    return pr_number, None


def _fetch_open_prs_rest(
    owner: str,
    repository: str,
    github_token: str | None,
    limit: int,
) -> PullRequestBatch:
    """Unauthenticated fallback: list open PRs, then fetch details concurrently."""
    headers = _build_headers(github_token)
    has_token = bool(github_token)
    owner_slug = quote(owner, safe="")
    repo_slug = quote(repository, safe="")

    results: list[dict[str, Any]] = []
    rate_limit: RateLimit | None = None
    page = 1
    saw_full_page = False

    while len(results) < limit and page <= MAX_PAGES:
        response = _request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner_slug}/{repo_slug}/pulls",
            headers,
            has_token,
            params={
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        _raise_for_status(response, has_token)
        rate_limit = _parse_rate_limit(response)

        try:
            page_items = response.json()
        except ValueError as exc:
            raise GithubHistoryError("GitHub returned a malformed response.") from exc

        if not isinstance(page_items, list) or not page_items:
            break

        results.extend(page_items)
        saw_full_page = len(page_items) == 100
        if not saw_full_page:
            break
        page += 1

    truncated = len(results) > limit or (saw_full_page and len(results) >= limit)
    results = results[:limit]

    # The list endpoint omits additions/deletions/changed_files. Fetch them
    # concurrently instead of one-at-a-time.
    numbers = [int(pr["number"]) for pr in results if pr.get("number")]
    details: dict[int, dict[str, Any]] = {}
    if numbers:
        with ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
            futures = [
                pool.submit(_fetch_pr_detail, owner_slug, repo_slug, number, headers, has_token) for number in numbers
            ]
            for future in futures:
                number, detail = future.result()
                if detail is not None:
                    details[number] = detail

    for pr in results:
        detail = details.get(int(pr.get("number") or 0))
        if detail is None:
            continue
        pr["additions"] = detail.get("additions", 0)
        pr["deletions"] = detail.get("deletions", 0)
        pr["changed_files"] = detail.get("changed_files", 0)

    return PullRequestBatch(
        items=results,
        total_available=None,
        truncated=truncated,
        rate_limit=rate_limit,
        used_graphql=False,
        # Per-file data needs an extra request per PR, which the 60/hour
        # unauthenticated quota cannot absorb.
        file_details_available=False,
    )


def fetch_open_pull_requests(
    owner: str,
    repository: str,
    github_token: str | None = None,
    limit: int = 100,
) -> PullRequestBatch:
    """Fetch open pull requests with size data.

    Uses GraphQL when a token is available (one request per 50 PRs, includes the
    changed-file list). Falls back to REST with concurrent detail fetches
    otherwise.
    """
    if github_token:
        try:
            return _fetch_open_prs_graphql(owner, repository, github_token, limit)
        except GithubHistoryError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("GraphQL open-PR fetch failed, falling back to REST: %s", exc)

    return _fetch_open_prs_rest(owner, repository, github_token, limit)
