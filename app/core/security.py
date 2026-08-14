from dataclasses import dataclass

from fastapi import Header

from app.core.config import settings


@dataclass
class TenantContext:
    tenant_id: str


async def get_tenant_context(x_tenant_id: str | None = Header(default=None)) -> TenantContext:
    tenant_id = x_tenant_id or settings.default_tenant_id
    return TenantContext(tenant_id=tenant_id)
