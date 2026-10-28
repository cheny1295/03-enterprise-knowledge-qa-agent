"""意图/深度路由。路由不是生命周期状态，也不是最终 AnswerResult.status。"""

from dataclasses import replace
from uuid import uuid4

from .contracts import AgentError, CandidateResult, IntentAssessment, RouteDecision


NONFACTUAL_ANSWER = "你好，我可以帮你查询当前租户已发布的制度和 FAQ。"


def model_source(model):
    return "mock_policy" if model.model_mode == "mock" else "model"


def is_pure_nonfactual(question):
    return question.strip() in {"你好", "您好", "谢谢", "感谢", "你能做什么"}


def needs_context(question):
    return (question.strip("？?。 ") in {"多久", "多少", "怎么办"}
            or any(token in question for token in ("那它", "这个", "那个", "上述")))


def contextual_question(question, history):
    if needs_context(question):
        for message in reversed(history):
            if (message["role"] == "user" and not needs_context(message["content"])
                    and not is_pure_nonfactual(message["content"])):
                return message["content"] + "\n追问：" + question
    return question


def prepare_route(state, model):
    question = state.question.strip()
    if is_pure_nonfactual(question):
        state.intent_assessment = IntentAssessment(
            "intent-" + uuid4().hex, "greeting" if question != "你能做什么" else "capability",
            "shallow", decision_source="rule", classifier_id="rule-v1",
        )
        state.routing = RouteDecision("fast_nonfactual", "全句白名单模板")
        state.emit("route_selected", route="fast_nonfactual")
        return CandidateResult(
            "completed",
            NONFACTUAL_ANSWER,
            stop_reason="finished_nonfactual",
        )
    state.normalized_question = contextual_question(question, state.conversation_history)
    if state.normalized_question != question:
        state.emit("question_contextualized", context_source="history.user",
                   normalized_question=state.normalized_question)
    state.budget.consume_model_step()
    assessment = model.classify(state.normalized_question, state.conversation_history)
    if (not isinstance(assessment, IntentAssessment)
            or assessment.intent not in {
                "greeting", "capability", "knowledge_question", "out_of_scope", "unknown"}
            or assessment.depth not in {"shallow", "deep", "uncertain"}
            or not all(isinstance(c, str) and c for c in assessment.missing_conditions)):
        raise AgentError("provider_response_invalid", "意图分类响应不符合协议。")
    assessment = replace(assessment, assessment_id="intent-" + uuid4().hex,
                         decision_source=model_source(model), classifier_id=model.adapter_id)
    state.intent_assessment = assessment
    state.emit("intent_assessed", decision_source=assessment.decision_source,
               intent=assessment.intent, depth=assessment.depth,
               intent_assessment_id=assessment.assessment_id,
               model_mode=model.model_mode, adapter_id=model.adapter_id,
               policy_id=model.adapter_id if model.model_mode == "mock" else None)
    if assessment.missing_conditions:
        state.routing = RouteDecision("fallback", "询问条件缺失")
        return CandidateResult(
            "needs_clarification", "请先补充询问对象或适用条件。",
            gaps=list(assessment.missing_conditions),
            follow_up_question="请补充：" + "、".join(assessment.missing_conditions),
            stop_reason="clarification_required",
        )
    if assessment.intent == "out_of_scope":
        state.routing = RouteDecision("fallback", "超出企业知识范围")
        state.emit("route_selected", route="fallback")
        return CandidateResult(
            "insufficient_evidence",
            "这个问题不属于当前企业知识范围。",
            gaps=["当前知识范围不包含该问题"],
            stop_reason="no_supporting_evidence",
        )
    state.routing = RouteDecision("deep_query", "需要查询当前租户知识")
    state.emit("route_selected", route="deep_query")
    return None


def resolve_faq_route(state, repository):
    entries = repository.faqs(state.tenant_context.tenant_id, state.snapshot_id)
    explicit_text = state.question + "\n" + "\n".join(
        m["content"] for m in state.conversation_history if m["role"] == "user"
    )
    for faq in entries:
        if state.question.strip() not in (faq.canonical_question, *faq.aliases):
            continue
        eligible = (
            faq.topic_complete and len(faq.source_refs) == 1
            and all(condition in explicit_text for condition in faq.required_conditions)
            and not state.conversation_history
        )
        route = "fast_faq" if eligible else "deep_query"
        state.routing = RouteDecision(route, "FAQ 来源与条件检查", faq.faq_topic_id)
        state.emit("route_selected", route=route, faq_topic_id=faq.faq_topic_id)
        return
