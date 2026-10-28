"""离线模型策略；只验证结构化动作和观察驱动，不代表真实 LLM。"""

from .contracts import IntentAssessment
from .retrieval import resolved_evidence


class OfflineModel:
    model_mode = "mock"
    adapter_id = "offline-policy-v1"

    def classify(self, question, history):
        lowered = question.strip().lower()
        if lowered in {"你好", "您好", "谢谢", "感谢"}:
            return IntentAssessment("", "greeting", "shallow")
        if "怎么用" in question or "你能做什么" in question:
            return IntentAssessment("", "capability", "shallow")
        if any(token in question for token in ("写诗", "天气预报", "炒股建议")):
            return IntentAssessment("", "out_of_scope", "uncertain")
        if question.strip("？?。 ") in {"多久", "多少", "怎么办"}:
            return IntentAssessment("", "unknown", "uncertain", ("询问对象",))
        if any(token in question for token in ("报销", "制度", "期限", "申请", "规定")):
            return IntentAssessment("", "knowledge_question", "uncertain")
        return IntentAssessment("", "unknown", "uncertain")

    def decide(self, state):
        if not state.observations:
            return {"type": "search_documents", "query": state.normalized_question}
        if state.unrecovered_errors:
            return {
                "type": "finish", "status": "execution_error",
                "answer": "指定来源未能读取，当前查询无法完成。",
                "claims": [], "gaps": ["来源读取未恢复"], "follow_up_question": None,
            }
        if any(a.relation in {"unresolved_conflict", "insufficient_data"}
               for a in state.conflict_assessments):
            return {
                "type": "finish", "status": "needs_clarification",
                "answer": "当前已读取的制度存在未决冲突，请确认适用的制度范围或版本。",
                "claims": [
                    {"text": e.quote, "evidence_ids": [e.evidence_id]}
                    for e in state.evidence.values()
                ],
                "gaps": ["适用制度或版本未明确"],
                "follow_up_question": "请确认适用的制度范围或版本。",
            }
        unread = state.unread_candidates()
        if unread:
            return {"type": "read_document", "candidate_id": unread[0].candidate_id}
        if state.search_coverage.get("candidate_truncated"):
            return {
                "type": "finish", "status": "insufficient_evidence",
                "answer": "检索候选被截断，尚不能确定完整适用依据。",
                "claims": [], "gaps": ["候选来源未完整查证"], "follow_up_question": None,
            }
        if state.evidence:
            # 只摘录原文，不假装具备通用语义裁决能力。
            evidence = resolved_evidence(state)
            if not evidence or any(a.relation == "scope_disjoint"
                                   for a in state.conflict_assessments):
                return {
                    "type": "ask_clarification",
                    "question": "请确认适用范围或参考时间。",
                    "gap": "来源适用范围不同或明示关系存在循环，不能唯一裁决",
                }
            return {
                "type": "finish", "status": "completed",
                "answer": "\n".join(dict.fromkeys(e.quote for e in evidence)),
                "claims": [{"text": e.quote, "evidence_ids": [e.evidence_id]}
                           for e in evidence],
                "gaps": [], "follow_up_question": None,
            }
        return {
            "type": "finish", "status": "insufficient_evidence",
            "answer": "当前资料无法支持这个问题的确定回答。",
            "claims": [], "gaps": ["没有找到支持当前问题的资料"],
            "follow_up_question": None,
        }
