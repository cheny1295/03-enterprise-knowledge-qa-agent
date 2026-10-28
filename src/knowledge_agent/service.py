"""L2：可信身份、快照绑定、唯一 finalize；任何失败都不绕过结果校验。"""

from datetime import datetime, timezone
from uuid import uuid4

from .contracts import AgentError, AnswerResult, CandidateResult, Lifecycle
from .orchestrator import AgentOrchestrator, error_candidate
from .results import ResultAssembler, ResultValidator
from .retrieval import EvidenceService, LexicalSearchService
from .routing import prepare_route, resolve_faq_route
from .runtime import AgentState, Budget
from .tools import ToolRouter, validate_filters


def validate_request(request, mode):
    if not isinstance(request, dict) or set(request) - {
            "question", "conversation_history", "mode", "trace", "filters", "historical"}:
        raise AgentError("invalid_arguments", "请求包含未知字段。")
    question = request.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise AgentError("invalid_arguments", "问题必须为 1–4000 字的文本。")
    if request.get("mode", mode) != mode:
        raise AgentError("invalid_arguments", "请求模型模式与已配置适配器不一致。")
    if "trace" in request and type(request["trace"]) is not bool:
        raise AgentError("invalid_arguments", "trace 必须是布尔值。")
    history = request.get("conversation_history", [])
    if not isinstance(history, list) or len(history) > 12:
        raise AgentError("invalid_arguments", "对话历史最多为 12 条消息。")
    for message in history:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
                or not message["content"].strip() or len(message["content"]) > 4000):
            raise AgentError("invalid_arguments", "对话历史角色或内容无效。")
    filters = validate_filters(request.get("filters", {}))
    historical = request.get("historical", False)
    if type(historical) is not bool:
        raise AgentError("invalid_arguments", "historical 必须是布尔值。")
    if "withdrawn" in filters.get("status", []) and (
            not historical or not filters.get("effective_on")):
        raise AgentError("invalid_arguments", "查询撤回制度必须明确历史查询及参考生效日期。")
    sources = {key: "request.filters" for key in filters}
    if "status" not in filters:
        filters["status"] = ["active"]
        sources["status"] = "application.default"
    return question.strip(), tuple(dict(m) for m in history), filters, sources


def minimal_trace(request_id, event_type, **fields):
    return {
        "event_id": uuid4().hex, "event_type": event_type, "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision_source": "runtime", "snapshot_id": None, "tenant_ref": None,
        "model_steps_used": 0, "tool_calls_used": 0, "embedding_calls_used": 0,
        **fields,
    }


def finalize_envelope(request_id, candidate, trace):
    result = AnswerResult(
        request_id, None, None, "fallback", candidate.status, candidate.answer,
        [], [], candidate.gaps, candidate.follow_up_question, [], [],
        candidate.error_code, candidate.stop_reason, None, "trace-" + request_id,
    )
    ResultValidator().validate(result)
    trace.append(minimal_trace(request_id, "terminated",
                               status=result.status, stop_reason=result.stop_reason))
    return result, trace


def startup_failure(error):
    """CLI 配置失败与请求拒绝共用无身份/无资料的结果校验出口。"""
    request_id = "req-" + uuid4().hex
    candidate = error_candidate(error)
    if error.code in {"access_denied", "invalid_arguments"}:
        candidate = CandidateResult(
            "invalid_request", "输入或访问权限无效，未开始知识查询。",
            error_code=error.code, stop_reason="request_rejected",
        )
    return finalize_envelope(request_id, candidate, [
        minimal_trace(request_id, "received"),
        minimal_trace(request_id, "result_validation"),
    ])


class ApplicationService:
    def __init__(self, identities, repository, model, search_service=None,
                 max_steps=6, max_tool_calls=4):
        self.identities = identities
        self.repository = repository
        self.model = model
        self.search_service = search_service or LexicalSearchService(repository)
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls

    def ask(self, request, principal_id, tenant_id):
        request_id = "req-" + uuid4().hex
        trace = [minimal_trace(request_id, "received")]
        state = None
        try:
            question, history, filters, filter_sources = validate_request(
                request, self.model.model_mode,
            )
            context = self.identities.resolve_identity(principal_id, tenant_id)
            self.identities.authorize(context)
        except AgentError as exc:
            candidate = CandidateResult(
                "invalid_request", "输入或访问权限无效，未开始知识查询。",
                error_code=exc.code, stop_reason="request_rejected",
            )
            if exc.code == "access_denied":
                trace.append(minimal_trace(request_id, "identity_rejected"))
            return self.finalize(request_id, state, candidate, trace)
        state = AgentState(
            request_id, context, question, history,
            budget=Budget(self.max_steps, self.max_tool_calls), trace=trace,
            confirmed_filters=filters,
        )
        state.emit("validated")
        state.emit("filters_confirmed", confirmed_filters=filters,
                   filter_sources=filter_sources)
        try:
            candidate = prepare_route(state, self.model)
            if candidate is None:
                state.bind_snapshot(self.repository.load_snapshot(context))
                state.emit("snapshot_bound")
                resolve_faq_route(state, self.repository)
                tools = ToolRouter(self.search_service, EvidenceService(self.repository),
                                   self.identities)
                candidate = AgentOrchestrator(self.model, tools).run(state)
        except AgentError as exc:
            candidate = error_candidate(exc)
        except Exception:
            candidate = error_candidate(AgentError("internal_error", "内部处理失败。"))
        return self.finalize(request_id, state, candidate, trace)

    def finalize(self, request_id, state, candidate, trace):
        if state:
            state.lifecycle_state = Lifecycle.RESULT_VALIDATION
            state.emit("result_validation")
            try:
                self.identities.authorize(state.tenant_context)
                if state.snapshot:
                    self.repository.assert_readable(
                        state.tenant_context.tenant_id, state.snapshot_id,
                    )
            except AgentError:
                # 已读正文不能随错误信封或 Trace 一起泄漏给被撤权的调用方。
                candidate = error_candidate(AgentError("access_denied", "授权已撤销。"))
                trace[:] = [
                    minimal_trace(request_id, "result_validation"),
                    minimal_trace(request_id, "identity_rejected"),
                ]
                state = None
            if state:
                if candidate.status != "completed" and state.routing:
                    state.change_route("fallback", candidate.stop_reason)
                try:
                    result = ResultAssembler(self.repository).assemble(state, candidate)
                except AgentError as exc:
                    # 不把不可信模型答案原样放进错误结果。
                    code = exc.code
                    candidate = error_candidate(
                        AgentError(code, "结果来源或契约校验失败。"),
                        action_error=code == "invalid_result",
                    )
                    state.change_route("fallback", "结果校验拒绝")
                    candidate.claims = []
                    result = ResultAssembler(self.repository).assemble(state, candidate)
                state.lifecycle_state = Lifecycle.TERMINATED
                state.emit("terminated", stop_reason=result.stop_reason,
                           status=result.status, answer_kind=result.answer_kind)
                return result, trace
        # 输入拒绝/撤权信封也经过同一个 ResultValidator，不含资料正文或外域身份。
        return finalize_envelope(request_id, candidate, trace)
