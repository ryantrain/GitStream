from app.schemas.prediction import PullRequestEvent


def process_pull_request_event(tenant_id: str, event: PullRequestEvent) -> None:
    # This stub marks where async ETL enqueueing or direct feature writes occur.
    _ = (tenant_id, event)
