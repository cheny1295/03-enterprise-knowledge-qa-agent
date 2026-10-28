"""层间对象。SDK 类型和可写身份不进入核心契约。"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Lifecycle(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    DECIDING = "DECIDING"
    OBSERVING = "OBSERVING"
    RESULT_VALIDATION = "RESULT_VALIDATION"
    TERMINATED = "TERMINATED"


class AgentError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class TenantContext:
    principal_id: str
    tenant_id: str
    roles: tuple[str, ...]
    authz_version: str
    source: str = "trusted_process_config"


@dataclass(frozen=True)
class Section:
    section_id: str
    heading: str
    text: str
    faq_topic_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    tenant_id: str
    document_id: str
    version_id: str
    version_date: str
    title: str
    sections: tuple[Section, ...]
    scope: tuple[str, ...] = ()
    status: str = "active"
    effective_from: str | None = None
    effective_to: str | None = None
    supersedes: tuple[str, ...] = ()
    priority: int | None = None
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class FaqEntry:
    tenant_id: str
    snapshot_id: str
    faq_topic_id: str
    canonical_question: str
    aliases: tuple[str, ...]
    required_conditions: tuple[str, ...]
    source_refs: tuple[tuple[str, str, str], ...]
    topic_complete: bool


@dataclass(frozen=True)
class KnowledgeSnapshot:
    snapshot_id: str
    tenant_id: str
    dataset_version: str
    index_version: str
    embedding_model_id: str
    embedding_revision: str
    vector_dimension: int
    publication_state: str
    document_versions: tuple[str, ...]
    index_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchCandidate:
    candidate_id: str
    tenant_id: str
    request_id: str
    snapshot_id: str
    document_id: str
    version_id: str
    version_date: str
    title: str
    section_id: str
    snippet: str
    retrieval_query: str
    retrieval_rank: int
    retrieval_score: float
    lexical_score: float | None = None
    vector_score: float | None = None
    lexical_rank: int | None = None
    vector_rank: int | None = None
    retrieval_mode: str = "hybrid"
    scope: tuple[str, ...] = ()
    status: str = "active"
    effective_from: str | None = None
    effective_to: str | None = None
    supersedes: tuple[str, ...] = ()
    priority: int | None = None

    @property
    def source_ref(self) -> tuple[str, str, str]:
        return self.document_id, self.version_id, self.section_id


@dataclass(frozen=True)
class ReadEvidence:
    evidence_id: str
    tenant_id: str
    request_id: str
    snapshot_id: str
    source_candidate_id: str
    document_id: str
    version_id: str
    version_date: str
    title: str
    section_id: str
    quote: str
    scope: tuple[str, ...] = ()
    status: str = "active"
    effective_from: str | None = None
    effective_to: str | None = None
    supersedes: tuple[str, ...] = ()
    priority: int | None = None
    faq_topic_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConflictAssessment:
    assessment_id: str
    tenant_id: str
    request_id: str
    snapshot_id: str
    candidate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    relation: str
    basis_fields: tuple[str, ...] = ()
    unresolved_gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentAssessment:
    assessment_id: str
    intent: str
    depth: str
    missing_conditions: tuple[str, ...] = ()
    decision_source: str = "mock_policy"
    classifier_id: str = "offline-rules-v1"
    confidence: float | None = None


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str
    faq_topic_id: str | None = None
    attempted_route: str | None = None
    effective_route: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "attempted_route", self.attempted_route or self.route)
        object.__setattr__(self, "effective_route", self.effective_route or self.route)


@dataclass
class ToolResult:
    tool_name: str
    tool_call_id: str
    status: str
    data: dict[str, Any]
    evidence: tuple[ReadEvidence, ...] = ()
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    tenant_id: str
    request_id: str
    snapshot_id: str
    tool_call_id: str
    tool_name: str
    status: str
    summary: str
    candidate_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass
class CandidateResult:
    status: str
    answer: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    follow_up_question: str | None = None
    error_code: str | None = None
    stop_reason: str = ""


@dataclass
class AnswerResult:
    request_id: str
    tenant_id: str | None
    routing: RouteDecision | None
    answer_kind: str
    status: str
    answer: str
    claims: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    gaps: list[str]
    follow_up_question: str | None
    conflict_assessments: list[ConflictAssessment]
    warnings: list[str]
    error_code: str | None
    stop_reason: str
    snapshot_id: str | None
    trace_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
