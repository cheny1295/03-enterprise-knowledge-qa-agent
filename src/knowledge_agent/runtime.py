"""请求级注册表、有限预算与只记录外部事件的 Trace。"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import (
    AgentError, ConflictAssessment, IntentAssessment, KnowledgeSnapshot,
    Lifecycle, Observation, ReadEvidence, RouteDecision, SearchCandidate,
    TenantContext,
)


@dataclass
class Budget:
    max_steps: int = 6
    max_tool_calls: int = 4
    model_steps_used: int = 0
    tool_calls_used: int = 0
    embedding_calls_used: int = 0

    def consume_model_step(self) -> None:
        if self.model_steps_used >= self.max_steps:
            raise AgentError("budget_exhausted", "模型调用预算已耗尽。")
        self.model_steps_used += 1

    def consume_tool_call(self) -> None:
        if self.tool_calls_used >= self.max_tool_calls:
            raise AgentError("budget_exhausted", "工具调用预算已耗尽。")
        self.tool_calls_used += 1

    def consume_embedding_call(self) -> None:
        if self.embedding_calls_used >= self.tool_calls_used:
            raise AgentError("budget_exhausted", "查询编码不能超出 Search 调用预算。")
        self.embedding_calls_used += 1


@dataclass
class AgentState:
    request_id: str
    tenant_context: TenantContext
    question: str
    conversation_history: tuple[dict, ...] = ()
    normalized_question: str = ""
    budget: Budget = field(default_factory=Budget)
    lifecycle_state: Lifecycle = Lifecycle.VALIDATED
    snapshot: KnowledgeSnapshot | None = None
    intent_assessment: IntentAssessment | None = None
    routing: RouteDecision | None = None
    candidates: dict[str, SearchCandidate] = field(default_factory=dict)
    evidence: dict[str, ReadEvidence] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    search_coverage: dict = field(default_factory=dict)
    candidate_reads: dict[str, str] = field(default_factory=dict)
    conflict_assessments: list[ConflictAssessment] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    confirmed_filters: dict = field(default_factory=lambda: {"status": ["active"]})
    unrecovered_errors: dict[tuple[str, str], str] = field(default_factory=dict)

    def unread_candidates(self):
        read_refs = {(e.document_id, e.version_id, e.section_id) for e in self.evidence.values()}
        return [c for c in self.candidates.values() if c.source_ref not in read_refs]

    @property
    def snapshot_id(self):
        return self.snapshot.snapshot_id if self.snapshot else None

    def bind_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        if (self.snapshot is not None or snapshot.publication_state != "ACTIVE"
                or snapshot.tenant_id != self.tenant_context.tenant_id):
            raise AgentError("knowledge_load_error", "无法绑定请求知识快照。")
        self.snapshot = snapshot

    def check_binding(self, source) -> None:
        if (self.snapshot is None
                or source.tenant_id != self.tenant_context.tenant_id
                or source.request_id != self.request_id
                or source.snapshot_id != self.snapshot_id):
            raise AgentError("invalid_result", "来源不属于当前请求知识域。")

    def register_candidate(self, candidate: SearchCandidate) -> None:
        self.check_binding(candidate)
        self.candidates[candidate.candidate_id] = candidate

    def authorize_candidate(self, candidate_id: str) -> SearchCandidate | None:
        candidate = self.candidates.get(candidate_id)
        if candidate is not None:
            self.check_binding(candidate)
        return candidate

    def register_evidence(self, evidence: ReadEvidence) -> None:
        self.check_binding(evidence)
        candidate = self.authorize_candidate(evidence.source_candidate_id)
        if candidate is None or candidate.source_ref != (
                evidence.document_id, evidence.version_id, evidence.section_id):
            raise AgentError("invalid_result", "证据缺少当前请求的读取授权。")
        self.evidence[evidence.evidence_id] = evidence

    def change_route(self, route: str, reason: str) -> None:
        if not self.routing:
            self.routing = RouteDecision(route, reason)
        else:
            attempted = (self.routing.attempted_route
                         if route == "fallback" else route)
            self.routing = replace(
                self.routing, attempted_route=attempted,
                effective_route=route, reason=reason,
            )
        self.emit("fallback_selected" if route == "fallback" else "route_changed",
                  route=route, result_summary=reason)

    def emit(self, event_type: str, decision_source="runtime", **fields) -> None:
        self.trace.append({
            "event_id": uuid4().hex, "event_type": event_type,
            "request_id": self.request_id, "snapshot_id": self.snapshot_id,
            "tenant_ref": self.tenant_context.tenant_id,
            "step_index": self.budget.model_steps_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_source": decision_source,
            "model_steps_used": self.budget.model_steps_used,
            "tool_calls_used": self.budget.tool_calls_used,
            "embedding_calls_used": self.budget.embedding_calls_used,
            "remaining_steps": self.budget.max_steps - self.budget.model_steps_used,
            "remaining_tool_calls": self.budget.max_tool_calls - self.budget.tool_calls_used,
            **fields,
        })
