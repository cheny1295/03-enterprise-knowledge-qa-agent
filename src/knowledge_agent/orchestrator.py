"""L3 只返回候选；L2 是组装、校验与最终结果的唯一出口。"""

from .contracts import AgentError, CandidateResult, Lifecycle
from .routing import model_source
from .tools import validate_action


def error_candidate(error, *, action_error=False):
    if error.code == "budget_exhausted":
        return CandidateResult(
            "incomplete", "已达到本次执行预算，查询尚未完成。",
            gaps=["仍有未完成的查询或核验步骤"], stop_reason="budget_exhausted",
        )
    invalid_action = action_error or error.code == "provider_response_invalid"
    return CandidateResult(
        "invalid_model_action" if invalid_action else "execution_error",
        "模型响应不符合约束，未输出不可信结论。" if invalid_action else "当前查询执行失败，未能完成。",
        gaps=["需要重新执行或检查配置"], error_code=error.code,
        stop_reason="action_rejected" if invalid_action else "execution_failed",
    )


def finish_candidate(state, action):
    if action["type"] == "ask_clarification":
        return CandidateResult(
            "needs_clarification", action["question"], gaps=[action["gap"]],
            follow_up_question=action["question"], stop_reason="clarification_required",
        )
    status = action["status"]
    error_code = None
    reason = {
        "completed": "finished_with_evidence",
        "needs_clarification": "clarification_required",
        "insufficient_evidence": "no_supporting_evidence",
        "execution_error": "recoverable_error_exhausted",
    }[status]
    unrecovered = state.unrecovered_errors
    if status == "execution_error":
        if not unrecovered:
            raise AgentError("invalid_arguments", "模型未提供实际执行错误依据。")
        error_code = next(iter(unrecovered.values()))
    elif unrecovered and status in {"completed", "insufficient_evidence"}:
        raise AgentError("invalid_arguments", "未恢复的工具失败不能伪装成业务结果。")
    return CandidateResult(
        status, action["answer"], action["claims"], action["gaps"],
        action["follow_up_question"], error_code, reason,
    )


class AgentOrchestrator:
    def __init__(self, model, tools):
        self.model = model
        self.tools = tools

    def run(self, state):
        while True:
            try:
                state.lifecycle_state = Lifecycle.DECIDING
                state.budget.consume_model_step()
                raw_action = self.model.decide(state)
            except AgentError as exc:
                return error_candidate(exc)
            try:
                action = validate_action(raw_action)
                state.emit(
                    "decision", decision_source=model_source(self.model),
                    model_mode=self.model.model_mode, adapter_id=self.model.adapter_id,
                    policy_id=self.model.adapter_id if self.model.model_mode == "mock" else None,
                    action_type=action["type"], action_arguments=action,
                    input_observation_ids=[o.observation_id for o in state.observations],
                    decision_basis_summary=(
                        "尚无工具观察"
                        if not state.observations else
                        f"最近观察 {state.observations[-1].tool_name}："
                        f"{state.observations[-1].status}，"
                        f"{len(state.candidates)} 个候选、{len(state.evidence)} 个已读来源"
                    ) + f"；下一动作 {action['type']}",
                )
                if action["type"] in {"finish", "ask_clarification"}:
                    return finish_candidate(state, action)
            except AgentError as exc:
                return error_candidate(exc, action_error=True)
            try:
                result = self.tools.execute(state, action)
            except AgentError as exc:
                return error_candidate(exc)
            if result.status == "error" and not result.error["retryable"]:
                return error_candidate(AgentError(
                    result.error["code"], result.error["message"],
                ))
