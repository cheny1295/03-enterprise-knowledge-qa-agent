"""最终出口校验来源可回查性，不宣称以字符串匹配证明语义蕴含。"""

from dataclasses import asdict

from .contracts import AgentError, AnswerResult
from .retrieval import resolved_evidence
from .routing import NONFACTUAL_ANSWER, is_pure_nonfactual


STATUS_REASONS = {
    "completed": {"finished_with_evidence", "finished_nonfactual"},
    "needs_clarification": {"clarification_required"},
    "insufficient_evidence": {"no_supporting_evidence"},
    "execution_error": {"execution_failed", "recoverable_error_exhausted"},
    "incomplete": {"budget_exhausted", "interrupted"},
    "invalid_request": {"request_rejected"},
    "invalid_model_action": {"action_rejected"},
}


class ResultValidator:
    def validate(self, result):
        if (result.status not in STATUS_REASONS
                or result.stop_reason not in STATUS_REASONS[result.status]
                or not result.answer):
            raise AgentError("invalid_result", "结果状态与停止原因不一致。")
        if result.status in {"execution_error", "invalid_request", "invalid_model_action"}:
            if not result.error_code:
                raise AgentError("invalid_result", "失败结果缺少诊断码。")
        elif result.error_code is not None:
            raise AgentError("invalid_result", "正常业务结果不能携带执行错误码。")
        if result.status == "needs_clarification" and (
                not result.gaps or not result.follow_up_question):
            raise AgentError("invalid_result", "澄清结果缺少缺口或追问。")
        if result.status in {"incomplete", "insufficient_evidence"} and not result.gaps:
            raise AgentError("invalid_result", "未完成或不足结果缺少缺口。")
        if result.status == "completed":
            if result.answer_kind == "nonfactual":
                if (result.answer != NONFACTUAL_ANSWER or result.claims or result.citations
                        or result.snapshot_id is not None
                        or result.stop_reason != "finished_nonfactual"):
                    raise AgentError("invalid_result", "非事实完成结果必须来自固定模板。")
            elif (not result.claims or not result.citations or not result.snapshot_id
                  or result.stop_reason != "finished_with_evidence"):
                raise AgentError("invalid_result", "事实完成结果必须有已读取的引用。")
            if any(a.relation in {"unresolved_conflict", "insufficient_data"}
                   for a in result.conflict_assessments):
                raise AgentError("invalid_result", "未决或未查证冲突不得输出 completed。")
        return result


class ResultAssembler:
    def __init__(self, repository):
        self.repository = repository

    def assemble(self, state, candidate):
        if (state.unrecovered_errors
                and candidate.status in {"completed", "insufficient_evidence"}):
            raise AgentError("invalid_result", "尚有未恢复的工具失败。")
        if candidate.status == "completed" and state.snapshot:
            if (not state.search_coverage or state.unread_candidates()
                    or state.search_coverage.get("candidate_truncated")):
                raise AgentError("invalid_result", "召回覆盖未完成，不得输出确定结论。")
            if state.evidence and not resolved_evidence(state):
                raise AgentError("invalid_result", "明示来源关系循环，不能选择确定依据。")
        ids = set()
        for claim in candidate.claims:
            if (not claim.get("text") or not claim.get("evidence_ids")
                    or not isinstance(claim["evidence_ids"], list)):
                raise AgentError("invalid_result", "事实主张缺少引用。")
            ids.update(claim["evidence_ids"])
        for assessment in state.conflict_assessments:
            state.check_binding(assessment)
            ids.update(assessment.evidence_ids)
        citations = []
        for evidence_id in sorted(ids):
            evidence = state.evidence.get(evidence_id)
            if evidence is None:
                raise AgentError("invalid_result", "答案引用了未读取的证据。")
            state.check_binding(evidence)
            authorized = state.authorize_candidate(evidence.source_candidate_id)
            if authorized is None or authorized.source_ref != (
                    evidence.document_id, evidence.version_id, evidence.section_id):
                raise AgentError("invalid_result", "引用缺少当前请求的授权链。")
            section = self.repository.get_section(
                evidence.tenant_id, evidence.version_id, evidence.section_id, state.snapshot_id,
            )
            if section is None or section.text != evidence.quote:
                raise AgentError("invalid_result", "引用原文无法回查。")
            citations.append({
                key: value for key, value in asdict(evidence).items()
                if key in {"evidence_id", "document_id", "version_id", "version_date",
                           "section_id", "quote"}
            })
        nonfactual = (
            state.routing and state.routing.effective_route == "fast_nonfactual"
            and is_pure_nonfactual(state.question)
        )
        kind = (
            "nonfactual" if nonfactual and candidate.status == "completed" else
            "clarification" if candidate.status == "needs_clarification" else
            "factual" if candidate.status == "completed" else "fallback"
        )
        result = AnswerResult(
            state.request_id, state.tenant_context.tenant_id, state.routing,
            kind, candidate.status, candidate.answer, candidate.claims, citations,
            candidate.gaps, candidate.follow_up_question, list(state.conflict_assessments),
            [], candidate.error_code, candidate.stop_reason, state.snapshot_id,
            "trace-" + state.request_id,
        )
        return ResultValidator().validate(result)
