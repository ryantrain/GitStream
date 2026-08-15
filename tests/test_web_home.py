from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes.web import humanize_hours, thousands
from app.main import app
from app.services.github_client import GithubHistoryError


def test_home_page_renders() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "GitStream" in response.text


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


class TestHumanizeHours:
    def test_sub_hour_renders_minutes(self) -> None:
        # 0.17 hours told the user nothing useful.
        assert humanize_hours(0.17) == "10m"
        assert humanize_hours(0.5) == "30m"

    def test_tiny_values_never_round_to_zero(self) -> None:
        assert humanize_hours(0.001) == "1m"

    def test_hours_and_minutes(self) -> None:
        assert humanize_hours(1.0) == "1h"
        assert humanize_hours(2.5) == "2h 30m"

    def test_days_for_long_durations(self) -> None:
        assert humanize_hours(24) == "1d"
        assert humanize_hours(742.5) == "30d 22h"

    def test_handles_missing_and_bad_values(self) -> None:
        assert humanize_hours(None) == "—"
        assert humanize_hours("nonsense") == "—"

    def test_zero_is_not_treated_as_missing(self) -> None:
        assert humanize_hours(0) == "1m"


class TestThousands:
    def test_separators(self) -> None:
        assert thousands(1234567) == "1,234,567"

    def test_zero_renders_as_zero(self) -> None:
        assert thousands(0) == "0"

    def test_missing(self) -> None:
        assert thousands(None) == "—"


# ---------------------------------------------------------------------------
# Results template
# ---------------------------------------------------------------------------

CLOSED_PRS = [
    {
        "created_at": "2026-08-01T10:00:00Z",
        "merged_at": "2026-08-01T20:00:00Z",
        "additions": 50,
        "deletions": 10,
    },
    {
        "created_at": "2026-08-02T10:00:00Z",
        "merged_at": "2026-08-03T10:00:00Z",
        "additions": 100,
        "deletions": 20,
    },
    {
        "created_at": "2026-08-04T10:00:00Z",
        "merged_at": "2026-08-06T10:00:00Z",
        "additions": 200,
        "deletions": 50,
    },
]

OPEN_PRS = [
    {
        "number": 42,
        "title": "Add pagination to the users endpoint",
        "html_url": "https://github.com/org/repo/pull/42",
        "created_at": "2026-08-09T10:00:00Z",
        "user": {"login": "dev1"},
        "additions": 100,
        "deletions": 20,
        "changed_files": 5,
        "requested_reviewers": [{"login": "reviewer1"}],
        "draft": False,
    },
    {
        "number": 43,
        "title": "Spike: replace the cache layer",
        "html_url": "https://github.com/org/repo/pull/43",
        "created_at": "2026-08-08T10:00:00Z",
        "user": {"login": "dev2"},
        "additions": 900,
        "deletions": 300,
        "changed_files": 24,
        "requested_reviewers": [],
        "draft": True,
    },
]


def _post_estimate(**form_extra) -> str:
    with (
        patch("app.services.estimation.fetch_closed_pr_history", return_value=CLOSED_PRS),
        patch("app.services.estimation.fetch_open_pull_requests", return_value=OPEN_PRS),
    ):
        client = TestClient(app)
        data = {"owner": "org", "repository": "repo", "lookback_prs": "50"}
        data.update(form_extra)
        response = client.post("/estimate", data=data)

    assert response.status_code == 200
    return response.text


def test_results_page_renders_the_forecast() -> None:
    """The hero promises a forecast; it must actually appear on the page."""
    html = _post_estimate(include_drafts="1")

    assert "org/repo" in html
    assert "Next PR Forecast" in html
    assert "Median Merge" in html
    assert "Tail Latency" in html
    assert "Review Queue" in html
    # Size-bucket baselines are surfaced rather than computed and discarded.
    assert "How merge time varies with PR size" in html


def test_results_page_humanizes_durations() -> None:
    html = _post_estimate(include_drafts="1")
    # Ages are rendered as days/hours, not raw float hours like "26.0".
    assert "1d" in html
    assert "26.0" not in html


def test_results_page_marks_drafts_and_counts_them_separately() -> None:
    html = _post_estimate(include_drafts="1")
    assert 'class="badge draft"' in html
    assert "1 draft" in html


def test_drafts_can_be_excluded_from_the_page() -> None:
    """Without the checkbox, drafts are not fetched into the queue at all."""
    html = _post_estimate()
    assert 'class="badge draft"' not in html
    assert "#43" not in html


def test_results_table_is_accessible() -> None:
    html = _post_estimate(include_drafts="1")

    # Sorting is a real button with announced state, not a click handler on a span.
    assert 'aria-sort="none"' in html
    assert 'class="sort-button"' in html
    # Table semantics.
    assert 'scope="col"' in html
    assert "<caption" in html
    # Change size sorts on the combined total, not just additions.
    assert 'data-sort="120"' in html


def test_tooltips_are_focusable_buttons() -> None:
    html = _post_estimate(include_drafts="1")
    assert 'class="tip"' in html
    assert 'aria-label="Help:' in html
    # The visual tooltip is decorative; the accessible name carries the text.
    assert 'id="floating-tooltip"' in html
    assert 'aria-hidden="true"' in html


def test_small_sample_is_flagged() -> None:
    """Three merged PRs cannot support a trustworthy p90."""
    html = _post_estimate(include_drafts="1")
    assert "merged PRs in the sample" in html


def test_error_is_announced_and_does_not_500() -> None:
    with patch(
        "app.services.estimation.fetch_closed_pr_history",
        side_effect=GithubHistoryError("Repository not found."),
    ):
        client = TestClient(app)
        response = client.post(
            "/estimate",
            data={"owner": "org", "repository": "nope", "lookback_prs": "50"},
        )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "Repository not found." in response.text


def test_unexpected_failure_is_shown_not_raised() -> None:
    """A transport or programming error must not surface as a raw 500 page."""
    with patch(
        "app.services.estimation.fetch_closed_pr_history",
        side_effect=RuntimeError("boom"),
    ):
        client = TestClient(app)
        response = client.post(
            "/estimate",
            data={"owner": "org", "repository": "repo", "lookback_prs": "50"},
        )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "Something went wrong" in response.text
