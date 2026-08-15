"""Prediction service: time-to-merge forecasting and bottleneck insights."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func

from app.db.models import PredictionLog, PullRequestMetric, ReviewCycleMetric
from app.db.session import tenant_session
from app.schemas.prediction import BottleneckInsight, PredictionFactor, PredictionRequest
from app.services.feature_engineering import build_feature_vector
from app.services.historical_metrics import enrich_prediction_context
from app.services.model_loader import predict_with_model
from app.services.stats import percentile, robust_center

logger = logging.getLogger(__name__)


# --- Heuristic coefficients ---
# Each entry: (feature_name, coefficient, baseline_value, description)
# contribution = coefficient * (feature_value - baseline_value)
HEURISTIC_COEFFICIENTS: list[tuple[str, float, float, str]] = [
    ("change_size", 0.015, 0.0, "PR size (lines changed)"),
    ("files_changed", 0.3, 1.0, "Number of files modified"),
    ("reviewers_requested", 1.1, 1.0, "Number of reviewers requested"),
    ("reviewer_load_index", 4.0, 1.0, "Reviewer workload ratio"),
    ("avg_author_merge_hours", 0.12, 24.0, "Author's historical merge speed"),
    ("time_to_first_review_hours", 0.8, 0.0, "Time waiting for first review"),
    ("review_rounds", 2.5, 1.0, "Number of review iterations"),
    ("ci_pass_rate", -8.0, 1.0, "CI success rate (lower = slower)"),
    ("ci_reruns", 1.5, 0.0, "CI pipeline reruns"),
    ("force_push_count", 1.0, 0.0, "Force pushes (rebases/rewrites)"),
    ("author_open_pr_count", 0.5, 1.0, "Author's concurrent open PRs"),
    ("directories_touched", 0.4, 1.0, "Directory spread of changes"),
    ("touches_critical_path", 4.0, 0.0, "Touches critical code paths"),
    ("is_cross_timezone", 6.0, 0.0, "Cross-timezone review required"),
    ("comment_count", 0.3, 0.0, "Discussion volume"),
    ("has_slow_label", 8.0, 0.0, "Has blocking/slow label"),
    ("has_fast_label", -4.0, 0.0, "Has fast-track label"),
    ("test_ratio", -3.0, 0.0, "Test coverage ratio (more tests = faster approval)"),
]

HEURISTIC_INTERCEPT = 3.0  # Base hours even for trivial PRs

MIN_PREDICTED_HOURS = 1.0

# Absolute risk-band cut points, used when a tenant has too little history to
# derive its own. Repository-relative bands are preferred (see
# _tenant_risk_thresholds): a fixed 36h "high" threshold is meaningless in a repo
# whose median merge time is 200h.
ABSOLUTE_RISK_LOW_MAX = 12.0
ABSOLUTE_RISK_MEDIUM_MAX = 36.0

# Observations needed before tenant-relative bands replace the absolute ones.
MIN_ROWS_FOR_RELATIVE_BANDS = 20

# --- Confidence scoring ---
# Optional request fields that represent a real, measured signal when supplied.
OPTIONAL_SIGNAL_FIELDS = (
    "time_to_first_review_hours",
    "review_rounds",
    "ci_pass_rate",
    "ci_duration_minutes",
    "ci_reruns",
    "commit_count",
    "force_push_count",
    "author_open_pr_count",
    "test_lines_added",
    "directories_touched",
    "touches_critical_path",
    "comment_count",
    "is_cross_timezone",
)

CONFIDENCE_BASE = 0.30
CONFIDENCE_SIGNAL_WEIGHT = 0.30
CONFIDENCE_MODEL_BONUS = 0.20
CONFIDENCE_AUTHOR_HISTORY_BONUS = 0.10
CONFIDENCE_REVIEWER_DATA_BONUS = 0.10

# --- Bottleneck insight analysis ---
# Minimum observations on *each side* of a comparison before a difference is
# reported. Without this, one large PR against forty small ones produced a
# confident "large PRs take 340h longer".
MIN_GROUP_SIZE = 5
MIN_ROWS_FOR_INSIGHTS = 5
MIN_REVIEWS_PER_REVIEWER = 3
MIN_PRS_PER_AUTHOR = 3
INSIGHT_LOOKBACK_DAYS = 90
MAX_INSIGHT_ROWS = 5000

# Only surface a delta if it is worth acting on.
MIN_REPORTABLE_IMPACT_HOURS = 1.0

LARGE_PR_LINES = 500
MANY_REVIEWERS = 3
MANY_REVIEW_ROUNDS = 2
CI_HEALTHY_THRESHOLD = 0.9


def _heuristic_predict(features: dict[str, float]) -> tuple[float, list[PredictionFactor]]:
    """Enhanced heuristic that returns both prediction and ranked factor contributions."""
    contributions: list[tuple[str, float]] = []
    total = HEURISTIC_INTERCEPT

    for feature_name, coeff, baseline, _description in HEURISTIC_COEFFICIENTS:
        value = features.get(feature_name, baseline)
        contribution = coeff * (value - baseline)
        total += contribution
        if abs(contribution) > 0.1:  # Only track meaningful contributions
            contributions.append((feature_name, contribution))

    # Sort by absolute contribution (most impactful first)
    contributions.sort(key=lambda x: abs(x[1]), reverse=True)

    top_factors = [
        PredictionFactor(
            factor=name,
            contribution_hours=round(contrib, 2),
            direction="increases" if contrib > 0 else "decreases",
        )
        for name, contrib in contributions[:5]
    ]

    return total, top_factors


def _risk_band(hours: float) -> str:
    """Absolute risk band, used when tenant history is too thin to be relative."""
    if hours < ABSOLUTE_RISK_LOW_MAX:
        return "low"
    if hours < ABSOLUTE_RISK_MEDIUM_MAX:
        return "medium"
    return "high"


def _relative_risk_band(hours: float, thresholds: dict[str, float] | None) -> str:
    """Band a prediction against the tenant's own merge-time distribution."""
    if not thresholds:
        return _risk_band(hours)
    if hours <= thresholds["low_max"]:
        return "low"
    if hours <= thresholds["medium_max"]:
        return "medium"
    return "high"


def _tenant_risk_thresholds(session, tenant_id: str) -> dict[str, float] | None:
    """Derive risk-band cut points from the tenant's own merged PR durations.

    ``low`` is at or better than the median, ``high`` is in the tenant's own
    p90 tail. Returns None when there is not enough history, so the caller falls
    back to the absolute bands.
    """
    cutoff = datetime.now(UTC) - timedelta(days=INSIGHT_LOOKBACK_DAYS)

    try:
        rows = (
            session.query(PullRequestMetric.observed_merge_hours)
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.observed_merge_hours.isnot(None),
                PullRequestMetric.merged_at.isnot(None),
                PullRequestMetric.merged_at >= cutoff,
            )
            .limit(MAX_INSIGHT_ROWS)
            .all()
        )

        values = [float(row.observed_merge_hours) for row in rows if row.observed_merge_hours is not None]
    except (TypeError, AttributeError):
        # A stubbed session that does not model this query: fall back cleanly.
        return None

    if len(values) < MIN_ROWS_FOR_RELATIVE_BANDS:
        return None

    return {
        "low_max": round(robust_center(values), 2),
        "medium_max": round(percentile(values, 0.90), 2),
    }


def _compute_confidence(
    payload: PredictionRequest,
    used_model: bool,
    author_history_source: str,
    reviewer_load_source: str,
) -> float:
    """Estimate prediction confidence from what is actually known.

    Scored on presence, not on "differs from the default". The previous version
    compared each feature against its neutral value, so a genuinely passing CI
    (``ci_pass_rate == 1.0``) and a genuine single-round review
    (``review_rounds == 1``) both counted as missing data and pushed confidence
    down. It also scored a heuristic fallback identically to a fitted model.

    Range: 0.0 to 1.0.
    """
    supplied = sum(1 for field in OPTIONAL_SIGNAL_FIELDS if getattr(payload, field, None) is not None)
    signal_coverage = supplied / len(OPTIONAL_SIGNAL_FIELDS)

    confidence = CONFIDENCE_BASE + CONFIDENCE_SIGNAL_WEIGHT * signal_coverage

    if used_model:
        confidence += CONFIDENCE_MODEL_BONUS

    # An author-specific baseline is stronger evidence than the tenant average,
    # which is stronger than the hardcoded default.
    if author_history_source == "author":
        confidence += CONFIDENCE_AUTHOR_HISTORY_BONUS
    elif author_history_source == "tenant":
        confidence += CONFIDENCE_AUTHOR_HISTORY_BONUS / 2

    if reviewer_load_source in ("measured", "capacity"):
        confidence += CONFIDENCE_REVIEWER_DATA_BONUS
    elif reviewer_load_source == "proxy":
        confidence += CONFIDENCE_REVIEWER_DATA_BONUS / 2

    return round(min(max(confidence, 0.0), 1.0), 2)


def predict_time_to_merge_hours(payload: PredictionRequest, tenant_id: str) -> dict:
    """Generate a merge-time prediction and persist it to prediction_logs.

    Auto-enriches missing contextual features (avg_author_merge_hours,
    reviewer_load_index) from historical data before prediction.
    Uses the trained ML model if available, otherwise falls back to heuristic.

    The caller's ``payload`` is not modified: enrichment happens on a copy, so
    callers do not observe their request object mutating as a side effect.
    """
    author_history_source = "supplied"
    reviewer_load_source = "supplied"

    # Work on a copy — the original request object belongs to the caller.
    request = payload.model_copy(deep=True)

    if request.avg_author_merge_hours is None or request.reviewer_load_index is None:
        context = enrich_prediction_context(
            tenant_id=tenant_id,
            author_id=request.author_id,
            repository=request.repository,
        )
        if request.avg_author_merge_hours is None:
            request.avg_author_merge_hours = context["avg_author_merge_hours"]
            author_history_source = context.get("author_history_source", "default")
        if request.reviewer_load_index is None:
            request.reviewer_load_index = context["reviewer_load_index"]
            reviewer_load_source = context.get("reviewer_load_source", "unknown")

    features = build_feature_vector(request)

    # Try the trained ML model first; fall back to the heuristic.
    ml_prediction = predict_with_model(features)

    # Factor contributions always come from the heuristic, because that is the
    # only model here that decomposes additively. When the ML model produced the
    # number, the factors explain *direction and relative weight*, not the exact
    # hours, so the response labels them as such rather than implying the ML
    # model attributed them.
    heuristic_hours, top_factors = _heuristic_predict(features)

    predicted: float
    if ml_prediction is not None:
        predicted = max(ml_prediction, MIN_PREDICTED_HOURS)
        used_model = True
        attribution_method = "heuristic_proxy"
    else:
        predicted = max(heuristic_hours, MIN_PREDICTED_HOURS)
        used_model = False
        attribution_method = "heuristic"

    prediction_method = "ml_model" if used_model else "heuristic"

    confidence = _compute_confidence(
        payload=request,
        used_model=used_model,
        author_history_source=author_history_source,
        reviewer_load_source=reviewer_load_source,
    )

    # Persist prediction to the database for accuracy tracking and drift detection.
    factor_names = [f.factor for f in top_factors]
    with tenant_session(tenant_id) as session:
        thresholds = _tenant_risk_thresholds(session, tenant_id)
        risk = _relative_risk_band(predicted, thresholds)

        log_entry = PredictionLog(
            tenant_id=tenant_id,
            pr_id=request.pr_id,
            predicted_merge_hours=round(predicted, 2),
            risk_band=risk,
            top_factors=json.dumps(factor_names),
            created_at=datetime.now(UTC),
        )
        session.add(log_entry)
        session.commit()

    return {
        "tenant_id": tenant_id,
        "pr_id": request.pr_id,
        "predicted_merge_hours": round(predicted, 2),
        "risk_band": risk,
        "top_factors": top_factors,
        "confidence_score": confidence,
        "prediction_method": prediction_method,
        "attribution_method": attribution_method,
        "risk_thresholds_source": "tenant_history" if thresholds else "absolute_default",
    }


# ---------------------------------------------------------------------------
# Bottleneck insights
# ---------------------------------------------------------------------------


def _median_delta(
    slower: list[float],
    faster: list[float],
    min_size: int = MIN_GROUP_SIZE,
) -> float | None:
    """Median difference between two groups, or None if either is too small.

    Medians rather than means: a single abandoned PR in one bucket used to
    manufacture a large, confident "impact". Both sides must clear ``min_size``.
    """
    if len(slower) < min_size or len(faster) < min_size:
        return None
    delta = robust_center(slower) - robust_center(faster)
    if delta < MIN_REPORTABLE_IMPACT_HOURS:
        return None
    return round(delta, 1)


def compute_bottleneck_insights(tenant_id: str) -> list[BottleneckInsight]:
    """Compute bottleneck insights from actual PR metrics in the database.

    Every insight reports ``impact_hours`` as the *additional delay attributable
    to that factor* — a median difference between a slower group and its
    comparison group. Previously some insights reported a difference while
    others reported an absolute average, so sorting by impact mixed incomparable
    units and the "slowest author" insight (an absolute figure) permanently
    occupied the top slot.

    The whole analysis runs from two queries instead of roughly fifteen
    sequential aggregates.
    """
    cutoff = datetime.now(UTC) - timedelta(days=INSIGHT_LOOKBACK_DAYS)

    with tenant_session(tenant_id) as session:
        # Cheap gate first: is there enough data to say anything at all?
        row_count = (
            session.query(func.count(PullRequestMetric.id))
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.observed_merge_hours.isnot(None),
            )
            .scalar()
            or 0
        )

        if row_count < MIN_ROWS_FOR_INSIGHTS:
            return _generic_insights()

        pr_rows = (
            session.query(
                PullRequestMetric.observed_merge_hours,
                PullRequestMetric.lines_added,
                PullRequestMetric.lines_deleted,
                PullRequestMetric.reviewers_requested,
                PullRequestMetric.time_to_first_review_hours,
                PullRequestMetric.ci_pass_rate,
                PullRequestMetric.review_rounds,
                PullRequestMetric.is_cross_timezone,
                PullRequestMetric.touches_critical_path,
                PullRequestMetric.author_id,
            )
            .filter(
                PullRequestMetric.tenant_id == tenant_id,
                PullRequestMetric.observed_merge_hours.isnot(None),
            )
            .limit(MAX_INSIGHT_ROWS)
            .all()
        )

        review_rows = (
            session.query(
                ReviewCycleMetric.reviewer_id,
                ReviewCycleMetric.wait_hours,
            )
            .filter(
                ReviewCycleMetric.tenant_id == tenant_id,
                ReviewCycleMetric.wait_hours.isnot(None),
                ReviewCycleMetric.created_at >= cutoff,
            )
            .limit(MAX_INSIGHT_ROWS)
            .all()
        )

    insights = _analyse_rows(pr_rows, review_rows)

    if not insights:
        insights.append(
            BottleneckInsight(
                factor="general",
                impact_hours=0.0,
                category="general",
                observations=len(pr_rows),
                recommendation=(
                    "No factor shows a clear, repeated effect on merge time yet. "
                    "Keep ingesting PR events — comparisons need at least "
                    f"{MIN_GROUP_SIZE} PRs on each side to be meaningful."
                ),
            )
        )

    insights.sort(key=lambda i: i.impact_hours, reverse=True)
    return insights


def _analyse_rows(pr_rows: list, review_rows: list) -> list[BottleneckInsight]:
    """Derive insights from in-memory PR and review-cycle rows.

    Separated from the query so the comparison logic is testable without a
    database and so every comparison shares one definition of "impact".
    """
    insights: list[BottleneckInsight] = []

    merged: list[dict] = []
    for row in pr_rows:
        if row.observed_merge_hours is None:
            continue
        merged.append(
            {
                "hours": float(row.observed_merge_hours),
                "size": int(row.lines_added or 0) + int(row.lines_deleted or 0),
                "reviewers": row.reviewers_requested,
                "first_review": row.time_to_first_review_hours,
                "ci": row.ci_pass_rate,
                "rounds": row.review_rounds,
                "cross_tz": row.is_cross_timezone,
                "critical": row.touches_critical_path,
                "author": row.author_id,
            }
        )

    if not merged:
        return insights

    def hours_where(predicate) -> list[float]:
        return [item["hours"] for item in merged if predicate(item)]

    # --- Change size ---
    delta = _median_delta(
        hours_where(lambda i: i["size"] > LARGE_PR_LINES),
        hours_where(lambda i: i["size"] <= LARGE_PR_LINES),
    )
    if delta is not None:
        insights.append(
            BottleneckInsight(
                factor="change_size",
                impact_hours=delta,
                category="composition",
                observations=len(merged),
                recommendation=(
                    f"Large PRs (>{LARGE_PR_LINES} lines) take {delta:.0f}h longer than "
                    "smaller ones. Consider breaking work into smaller, focused pull "
                    "requests."
                ),
            )
        )

    # --- Reviewer count ---
    delta = _median_delta(
        hours_where(lambda i: i["reviewers"] is not None and i["reviewers"] >= MANY_REVIEWERS),
        hours_where(lambda i: i["reviewers"] is not None and i["reviewers"] < MANY_REVIEWERS),
    )
    if delta is not None:
        insights.append(
            BottleneckInsight(
                factor="reviewers_requested",
                impact_hours=delta,
                category="review",
                observations=len(merged),
                recommendation=(
                    f"PRs with {MANY_REVIEWERS}+ reviewers take {delta:.0f}h longer. "
                    "Evaluate if all reviewers are necessary for low-risk changes."
                ),
            )
        )

    # --- Time to first review ---
    # Expressed as a delta by splitting on the median wait, so it is comparable
    # with the other factors instead of being an absolute average.
    first_review_values = [float(i["first_review"]) for i in merged if i["first_review"] is not None]
    if len(first_review_values) >= MIN_GROUP_SIZE * 2:
        split = robust_center(first_review_values)
        delta = _median_delta(
            hours_where(lambda i: i["first_review"] is not None and i["first_review"] > split),
            hours_where(lambda i: i["first_review"] is not None and i["first_review"] <= split),
        )
        if delta is not None:
            insights.append(
                BottleneckInsight(
                    factor="time_to_first_review",
                    impact_hours=delta,
                    category="review",
                    baseline_hours=round(split, 1),
                    observations=len(first_review_values),
                    recommendation=(
                        f"PRs waiting longer than {split:.0f}h for a first review take "
                        f"{delta:.0f}h longer overall. Reviewer rotation schedules or "
                        "review-ready notifications would cut the initial wait."
                    ),
                )
            )

    # --- CI health ---
    ci_values = [float(i["ci"]) for i in merged if i["ci"] is not None]
    if ci_values:
        delta = _median_delta(
            hours_where(lambda i: i["ci"] is not None and i["ci"] < CI_HEALTHY_THRESHOLD),
            hours_where(lambda i: i["ci"] is not None and i["ci"] >= CI_HEALTHY_THRESHOLD),
        )
        if delta is not None:
            avg_ci = robust_center(ci_values)
            insights.append(
                BottleneckInsight(
                    factor="ci_pass_rate",
                    impact_hours=delta,
                    category="ci",
                    observations=len(ci_values),
                    recommendation=(
                        f"PRs with CI failures take {delta:.0f}h longer to merge. "
                        f"Median CI pass rate is {avg_ci:.0%}. Investigate flaky tests "
                        "and improve pre-push validation."
                    ),
                )
            )

    # --- Review rounds ---
    delta = _median_delta(
        hours_where(lambda i: i["rounds"] is not None and i["rounds"] > MANY_REVIEW_ROUNDS),
        hours_where(lambda i: i["rounds"] is not None and i["rounds"] <= MANY_REVIEW_ROUNDS),
    )
    if delta is not None:
        insights.append(
            BottleneckInsight(
                factor="review_rounds",
                impact_hours=delta,
                category="review",
                observations=len(merged),
                recommendation=(
                    f"PRs with more than {MANY_REVIEW_ROUNDS} review rounds take "
                    f"{delta:.0f}h longer. Clearer PR descriptions, smaller scopes, or "
                    "pre-review design discussions reduce iteration cycles."
                ),
            )
        )

    # --- Cross-timezone review ---
    delta = _median_delta(
        hours_where(lambda i: i["cross_tz"] is True),
        hours_where(lambda i: i["cross_tz"] is not True),
    )
    if delta is not None:
        insights.append(
            BottleneckInsight(
                factor="cross_timezone",
                impact_hours=delta,
                category="review",
                observations=len(merged),
                recommendation=(
                    f"Cross-timezone PRs take {delta:.0f}h longer due to handoff delays. "
                    "Consider timezone-aware reviewer assignment or async review practices."
                ),
            )
        )

    # --- Critical path ---
    delta = _median_delta(
        hours_where(lambda i: i["critical"] is True),
        hours_where(lambda i: i["critical"] is not True),
    )
    if delta is not None:
        insights.append(
            BottleneckInsight(
                factor="critical_path",
                impact_hours=delta,
                category="composition",
                observations=len(merged),
                recommendation=(
                    f"PRs touching critical paths (auth, config, DB) take {delta:.0f}h "
                    "longer. Some of that is warranted; pre-approved patterns for "
                    "routine critical-path changes would recover the rest."
                ),
            )
        )

    # --- Author spread ---
    # Reported as the gap between the slowest qualifying author and everyone
    # else, so it is a delay attributable to a pattern rather than that author's
    # absolute cycle time.
    by_author: dict[str, list[float]] = {}
    for item in merged:
        if item["author"]:
            by_author.setdefault(str(item["author"]), []).append(item["hours"])

    eligible_authors = {author: values for author, values in by_author.items() if len(values) >= MIN_PRS_PER_AUTHOR}
    if len(eligible_authors) >= 2:
        slowest = max(eligible_authors.items(), key=lambda kv: robust_center(kv[1]))
        others = [hours for author, values in eligible_authors.items() if author != slowest[0] for hours in values]
        delta = _median_delta(slowest[1], others, min_size=MIN_PRS_PER_AUTHOR)
        if delta is not None:
            insights.append(
                BottleneckInsight(
                    factor="author_merge_pattern",
                    impact_hours=delta,
                    category="author",
                    baseline_hours=round(robust_center(others), 1),
                    observations=len(slowest[1]),
                    recommendation=(
                        f"One author's PRs take {delta:.0f}h longer to merge than the "
                        f"team median across {len(slowest[1])} PRs. Pairing or "
                        "pre-review discussion often closes this gap."
                    ),
                )
            )

    # --- Reviewer response spread ---
    by_reviewer: dict[str, list[float]] = {}
    for row in review_rows:
        if row.wait_hours is None or not row.reviewer_id:
            continue
        by_reviewer.setdefault(str(row.reviewer_id), []).append(float(row.wait_hours))

    eligible_reviewers = {
        reviewer: waits for reviewer, waits in by_reviewer.items() if len(waits) >= MIN_REVIEWS_PER_REVIEWER
    }
    if len(eligible_reviewers) >= 2:
        slowest = max(eligible_reviewers.items(), key=lambda kv: robust_center(kv[1]))
        others = [wait for reviewer, waits in eligible_reviewers.items() if reviewer != slowest[0] for wait in waits]
        delta = _median_delta(slowest[1], others, min_size=MIN_REVIEWS_PER_REVIEWER)
        if delta is not None:
            insights.append(
                BottleneckInsight(
                    factor="reviewer_response_time",
                    impact_hours=delta,
                    category="review",
                    baseline_hours=round(robust_center(others), 1),
                    observations=len(slowest[1]),
                    recommendation=(
                        f"The slowest reviewer responds {delta:.0f}h later than the team "
                        f"median across {len(slowest[1])} reviews. Redistributing "
                        "reviews or agreeing response-time expectations would help."
                    ),
                )
            )

    return insights


def _generic_insights() -> list[BottleneckInsight]:
    """Return generic insights when insufficient historical data exists.

    ``impact_hours`` here are illustrative industry-typical figures, not
    measurements; ``is_measured`` is False so callers can label them.
    """
    generic = [
        (
            "time_to_first_review",
            24.0,
            "review",
            "Time to first review is typically the largest component of merge time. "
            "Set up review-ready notifications and consider reviewer rotation schedules.",
        ),
        (
            "reviewers_requested",
            18.0,
            "review",
            "Review whether requiring a third reviewer improves quality for low-risk pull requests.",
        ),
        (
            "reviewer_load_index",
            12.0,
            "queue",
            "Balance reviewer assignment across the team to reduce queue time.",
        ),
        (
            "change_size",
            8.0,
            "composition",
            "Keep PRs under 400 lines when possible. Smaller PRs get reviewed faster and have fewer review rounds.",
        ),
        (
            "ci_pass_rate",
            6.0,
            "ci",
            "Failing CI blocks merges and adds iteration time. Invest in local pre-commit checks and fix flaky tests.",
        ),
    ]

    return [
        BottleneckInsight(
            factor=factor,
            impact_hours=impact,
            category=category,
            recommendation=recommendation,
            is_measured=False,
            observations=0,
        )
        for factor, impact, category, recommendation in generic
    ]
