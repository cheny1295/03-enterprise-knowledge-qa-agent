"""本地可信配置入口；请求正文和模型永远不参与成员资格建立。"""

from uuid import uuid4

from .contracts import AgentError, TenantContext


class IdentityRegistry:
    def __init__(self, memberships: dict[str, dict[str, tuple[str, ...]]]):
        self._memberships = {
            principal: dict(tenants) for principal, tenants in memberships.items()
        }
        self._versions = {
            (principal, tenant): uuid4().hex
            for principal, tenants in memberships.items() for tenant in tenants
        }

    def resolve_identity(self, principal_id: str, tenant_id: str) -> TenantContext:
        roles = self._memberships.get(principal_id, {}).get(tenant_id)
        if not roles:
            raise AgentError("access_denied", "无法验证当前身份的访问权限。")
        return TenantContext(
            principal_id, tenant_id, tuple(roles),
            self._versions[(principal_id, tenant_id)],
        )

    def authorize(self, context: TenantContext, role="knowledge_reader") -> None:
        roles = self._memberships.get(context.principal_id, {}).get(context.tenant_id, ())
        version = self._versions.get((context.principal_id, context.tenant_id))
        if (role not in roles or version != context.authz_version
                or context.roles != tuple(roles)
                or context.source != "trusted_process_config"):
            raise AgentError("access_denied", "当前访问授权无效或已撤销。")

    def revoke(self, principal_id: str, tenant_id: str) -> None:
        self._memberships.get(principal_id, {}).pop(tenant_id, None)
        self._versions[(principal_id, tenant_id)] = uuid4().hex

    def invalidate_tenant(self, tenant_id: str) -> None:
        """隐私删除时使该租户全部在途上下文过期；成员资格本身不变。"""
        for principal, tenants in self._memberships.items():
            if tenant_id in tenants:
                self._versions[(principal, tenant_id)] = uuid4().hex
