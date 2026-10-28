"""仅从操作者指定的本地配置读取身份/FAQ；模型无法调用此模块。"""

from .contracts import AgentError, FaqEntry
from .identity import IdentityRegistry
from .ingestion import read_json


def load_identities(path):
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise AgentError("invalid_arguments", "可信身份配置必须是对象。")
    memberships = {}
    for principal, tenants in raw.items():
        if not isinstance(principal, str) or not principal or not isinstance(tenants, dict):
            raise AgentError("invalid_arguments", "可信身份映射无效。")
        memberships[principal] = {}
        for tenant, roles in tenants.items():
            if (not isinstance(tenant, str) or not tenant or not isinstance(roles, list)
                    or not roles or not all(
                        r in {"knowledge_reader", "knowledge_editor"} for r in roles)):
                raise AgentError("invalid_arguments", "可信租户角色映射无效。")
            memberships[principal][tenant] = tuple(roles)
    return IdentityRegistry(memberships)


def load_faqs(path):
    raw = read_json(path)
    try:
        return tuple(FaqEntry(
            item["tenant_id"], "", item["faq_topic_id"], item["canonical_question"],
            tuple(item["aliases"]), tuple(item["required_conditions"]),
            tuple(tuple(ref) for ref in item["source_refs"]), item["topic_complete"],
        ) for item in raw)
    except (KeyError, TypeError) as exc:
        raise AgentError("knowledge_load_error", "FAQ 配置字段无效。") from exc
