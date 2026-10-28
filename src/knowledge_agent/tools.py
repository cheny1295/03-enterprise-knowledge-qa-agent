"""只有 Search/Read 可执行。所有工具结果（含错误）先登记 Observation。"""

import json
from datetime import date
from time import perf_counter
from uuid import uuid4

from .contracts import AgentError, Lifecycle, Observation
from .retrieval import tool_result


ALLOWED_ACTIONS = {
    "search_documents": ({"query"}, {"query", "filters", "limit"}),
    "read_document": ({"candidate_id"}, {"candidate_id"}),
    "ask_clarification": ({"question", "gap"}, {"question", "gap"}),
    "finish": ({"status", "answer", "claims", "gaps", "follow_up_question"},
               {"status", "answer", "claims", "gaps", "follow_up_question"}),
}
LIST_FILTERS = {"scope_any", "scope_all", "document_ids", "status"}
DATE_FILTERS = {"version_date_from", "version_date_to", "effective_on"}


def validate_filters(filters):
    if not isinstance(filters, dict) or set(filters) - (LIST_FILTERS | DATE_FILTERS):
        raise AgentError("invalid_arguments", "检索过滤字段无效。")
    normalized = {}
    for key, value in filters.items():
        if key in LIST_FILTERS:
            if not isinstance(value, list) or not all(
                    isinstance(v, str) and v.strip() for v in value):
                raise AgentError("invalid_arguments", "过滤列表必须为非空字符串数组。")
            values = list(dict.fromkeys(v.strip() for v in value))
            if key == "status" and set(values) - {"active", "withdrawn"}:
                raise AgentError("invalid_arguments", "资料 status 过滤无效。")
            if values:
                normalized[key] = values
        else:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("YYYY-MM-DD required")
            except (ValueError, TypeError) as exc:
                raise AgentError("invalid_arguments", "过滤日期必须为 YYYY-MM-DD。") from exc
            normalized[key] = value
    low, high = normalized.get("version_date_from"), normalized.get("version_date_to")
    if low and high and low > high:
        raise AgentError("invalid_arguments", "版本日期起止顺序无效。")
    return normalized


def validate_action(action):
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise AgentError("invalid_arguments", "模型动作必须是已注册的对象。")
    kind = action["type"]
    if kind not in ALLOWED_ACTIONS:
        raise AgentError("invalid_arguments", "模型动作不在允许的白名单中。")
    required, allowed = ALLOWED_ACTIONS[kind]
    if required - action.keys() or action.keys() - (allowed | {"type"}):
        raise AgentError("invalid_arguments", "模型动作缺少字段或包含未注册字段。")
    for key in {"query", "candidate_id", "question", "gap", "answer"} & action.keys():
        if not isinstance(action[key], str) or not action[key].strip():
            raise AgentError("invalid_arguments", "动作文本参数必须是非空字符串。")
    if kind == "search_documents":
        limit = action.get("limit", 5)
        if type(limit) is not int or not 1 <= limit <= 5:
            raise AgentError("invalid_arguments", "检索 limit 必须在 1–5 之间。")
        validate_filters(action.get("filters", {}))
    if kind == "finish":
        if action["status"] not in {
                "completed", "needs_clarification", "insufficient_evidence", "execution_error"}:
            raise AgentError("invalid_arguments", "finish.status 无效。")
        if (not isinstance(action["claims"], list) or not isinstance(action["gaps"], list)
                or not all(isinstance(g, str) and g for g in action["gaps"])
                or (action["follow_up_question"] is not None
                    and not isinstance(action["follow_up_question"], str))):
            raise AgentError("invalid_arguments", "finish 的主张、缺口或追问形状无效。")
        for claim in action["claims"]:
            if (not isinstance(claim, dict) or set(claim) != {"text", "evidence_ids"}
                    or not isinstance(claim["text"], str) or not claim["text"]
                    or not isinstance(claim["evidence_ids"], list) or not claim["evidence_ids"]
                    or not all(isinstance(e, str) and e for e in claim["evidence_ids"])):
                raise AgentError("invalid_arguments", "事实主张必须声明有效证据 ID。")
    return action


def merge_filters(system, proposed):
    """未确认的 scope/date/document 条件拒绝，不能用它们排除冲突来源。"""
    merged = {k: list(v) if isinstance(v, list) else v for k, v in system.items()}
    for key, value in validate_filters(proposed).items():
        if key not in system or value != system[key]:
            raise AgentError("invalid_arguments", "动作过滤条件尚未经用户明确确认。")
        merged[key] = value
    return merged


class ToolRouter:
    def __init__(self, search_service, evidence_service, identities=None):
        self.search_service = search_service
        self.evidence_service = evidence_service
        self.identities = identities

    def execute(self, state, action):
        action = validate_action(action)
        name = action["type"]
        if name not in {"search_documents", "read_document"}:
            raise AgentError("invalid_arguments", "该动作不是业务工具。")
        state.budget.consume_tool_call()
        start = perf_counter()
        # 失败只由同一操作恢复。检索同 query 但不同过滤器不能清除旧失败。
        operation = (name, action.get("candidate_id", action.get("query")))
        try:
            if self.identities:
                self.identities.authorize(state.tenant_context)
            if name == "search_documents":
                filters = merge_filters(state.confirmed_filters, action.get("filters", {}))
                operation = (name, json.dumps(
                    [action["query"], filters], sort_keys=True, ensure_ascii=False,
                ))
                result = self.search_service.search(
                    state, action["query"], filters, action.get("limit", 5),
                )
            else:
                candidate = state.authorize_candidate(action["candidate_id"])
                if candidate:
                    operation = (name, json.dumps(candidate.source_ref))
                result = self.evidence_service.read(state, action["candidate_id"])
        except AgentError as exc:
            result = tool_result(state, name, "error", {}, error={
                "code": exc.code, "message": exc.message, "retryable": exc.retryable,
            })
        except Exception:
            result = tool_result(state, name, "error", {}, error={
                "code": "internal_error", "message": "工具执行失败。", "retryable": False,
            })
        expected = {
            "tenant_id": state.tenant_context.tenant_id,
            "request_id": state.request_id, "snapshot_id": state.snapshot_id,
        }
        if any(result.metadata.get(k) != value for k, value in expected.items()):
            result = tool_result(state, name, "error", {}, error={
                "code": "internal_error", "message": "工具来源完整性检查失败。",
                "retryable": False,
            })
        result.metadata["elapsed_ms"] = int((perf_counter() - start) * 1000)
        candidates = tuple(result.data.get("candidates", ()))
        observation = Observation(
            "obs-" + uuid4().hex, expected["tenant_id"], state.request_id,
            state.snapshot_id, result.tool_call_id, name, result.status, result.status,
            tuple(c.candidate_id for c in candidates),
            tuple(e.evidence_id for e in result.evidence),
            result.error["code"] if result.error else None,
        )
        state.lifecycle_state = Lifecycle.OBSERVING
        state.observations.append(observation)
        if result.status in {"error", "not_found"}:
            state.unrecovered_errors[operation] = observation.error_code or "source_unavailable"
        else:
            state.unrecovered_errors.pop(operation, None)
        if name == "search_documents" and result.status in {"ok", "no_evidence"}:
            coverage = result.data["coverage"]
            state.search_coverage = {
                "candidate_truncated": (
                    state.search_coverage.get("candidate_truncated", False)
                    or coverage["candidate_truncated"]
                ),
                "topic_complete": coverage["topic_complete"],
            }
            if state.routing and state.routing.effective_route == "fast_faq" and (
                    not state.search_coverage["topic_complete"]
                    or state.search_coverage["candidate_truncated"] or len(candidates) != 1):
                state.change_route("deep_query", "FAQ 来源不唯一、不完整或候选截断")
        if state.conflict_assessments and state.routing and state.routing.effective_route == "fast_faq":
            state.change_route("deep_query", "已读来源存在需查证的关系")
        state.emit(
            "tool_result", tool_name=name, tool_call_id=result.tool_call_id,
            tool_status=result.status, observation_id=observation.observation_id,
            observation_status=result.status, candidate_ids=list(observation.candidate_ids),
            evidence_ids=list(observation.evidence_ids),
            error_code=observation.error_code,
            result_summary={"status": result.status, "elapsed_ms": result.metadata["elapsed_ms"],
                            "coverage": result.data.get("coverage"),
                            "candidate_count": len(observation.candidate_ids),
                            "evidence_count": len(observation.evidence_ids)},
        )
        if state.conflict_assessments:
            state.emit("conflict_assessed", conflict_assessment_ids=[
                a.assessment_id for a in state.conflict_assessments
            ])
        return result
