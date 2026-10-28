"""检索不产生引用，引用只能来自请求内授权的原文读取。"""

import math
import re
from itertools import combinations
from uuid import uuid4

from .contracts import (
    AgentError, ConflictAssessment, ReadEvidence, SearchCandidate, ToolResult,
)


def terms(text):
    text = text.lower()
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return set(re.findall(r"[a-z0-9]+", text)) | {
        run[i:i + 2] for run in chinese for i in range(max(1, len(run) - 1))
    }


def matches(doc, filters):
    for key, allowed in (("status", [doc.status]), ("document_ids", [doc.document_id])):
        if filters.get(key) and not set(filters[key]).intersection(allowed):
            return False
    if filters.get("scope_any") and not set(filters["scope_any"]).intersection(doc.scope):
        return False
    if filters.get("scope_all") and not set(filters["scope_all"]).issubset(doc.scope):
        return False
    if filters.get("version_date_from") and doc.version_date < filters["version_date_from"]:
        return False
    if filters.get("version_date_to") and doc.version_date > filters["version_date_to"]:
        return False
    day = filters.get("effective_on")
    return not day or bool(
        doc.effective_from and doc.effective_to and doc.effective_from <= day <= doc.effective_to
    )


def lexical_ranked(documents, query, filters):
    query_terms = terms(query)
    ranked = []
    for doc in documents:
        if not matches(doc, filters):
            continue
        for section in doc.sections:
            text_terms = terms(" ".join((doc.title, section.heading, section.text, *doc.keywords)))
            overlap = query_terms & text_terms
            if overlap:
                score = len(overlap) / math.sqrt(len(query_terms) * len(text_terms))
                ranked.append(((doc.document_id, doc.version_id, section.section_id), score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def tool_result(state, name, status, data, evidence=(), error=None):
    return ToolResult(
        name, "tc-" + uuid4().hex, status, data, tuple(evidence), error,
        {"tenant_id": state.tenant_context.tenant_id, "request_id": state.request_id,
         "snapshot_id": state.snapshot_id, "elapsed_ms": 0},
    )


class LexicalSearchService:
    """显式 lexical_fixture；不代表实现了向量相似度。"""

    retrieval_mode = "lexical_fixture"

    def __init__(self, repository):
        self.repository = repository

    def search(self, state, query, filters=None, limit=5):
        docs = self.repository.documents(state.tenant_context.tenant_id, state.snapshot_id)
        filters = filters or {"status": ["active"]}
        ranked = lexical_ranked(docs, query, filters)
        return self.build_result(
            state, docs, query, filters, limit, ranked[:10], [],
            branch_truncated=len(ranked) > 10,
        )

    def build_result(self, state, docs, query, filters, limit, lexical, vector,
                     branch_truncated=False):
        by_ref = {(d.document_id, d.version_id, s.section_id): (d, s)
                  for d in docs if matches(d, filters) for s in d.sections}
        lex = {ref: (rank, score) for rank, (ref, score) in enumerate(lexical, 1)}
        vec = {ref: (rank, score) for rank, (ref, score) in enumerate(vector, 1)}
        refs = set(lex) | set(vec)
        topic_complete = False
        topic_refs = set()
        if state.routing and state.routing.faq_topic_id:
            for faq in self.repository.faqs(state.tenant_context.tenant_id, state.snapshot_id):
                if faq.faq_topic_id == state.routing.faq_topic_id:
                    topic_refs = set(faq.source_refs)
                    refs.update(ref for ref in topic_refs if ref in by_ref)
                    topic_complete = faq.topic_complete
        if any(ref not in by_ref for ref in refs):
            raise AgentError("invalid_result", "召回来源不属于当前知识域或过滤范围。")

        def fused(ref):
            return sum(1 / (60 + branch[ref][0]) for branch in (lex, vec) if ref in branch)

        ordered = sorted(refs, key=lambda ref: (-fused(ref), ref))
        selected = ordered[:limit]
        truncated = branch_truncated or len(ordered) > limit
        # FAQ 完整性仅描述显式来源组；过滤/截断导致来源缺失时不可快答。
        complete = topic_complete and not truncated and topic_refs.issubset(selected)
        candidates = []
        for rank, ref in enumerate(selected, 1):
            doc, section = by_ref[ref]
            c = SearchCandidate(
                candidate_id="C-" + uuid4().hex, tenant_id=doc.tenant_id,
                request_id=state.request_id, snapshot_id=state.snapshot_id,
                document_id=doc.document_id, version_id=doc.version_id,
                version_date=doc.version_date, title=doc.title,
                section_id=section.section_id, snippet=section.text[:160],
                retrieval_query=query, retrieval_rank=rank, retrieval_score=fused(ref),
                lexical_score=lex[ref][1] if ref in lex else None,
                lexical_rank=lex[ref][0] if ref in lex else None,
                vector_score=vec[ref][1] if ref in vec else None,
                vector_rank=vec[ref][0] if ref in vec else None,
                retrieval_mode=self.retrieval_mode, scope=doc.scope, status=doc.status,
                effective_from=doc.effective_from, effective_to=doc.effective_to,
                supersedes=doc.supersedes, priority=doc.priority,
            )
            state.register_candidate(c)
            candidates.append(c)
        return tool_result(state, "search_documents", "ok" if candidates else "no_evidence", {
            "candidates": candidates,
            "coverage": {"candidate_truncated": truncated, "topic_complete": complete},
            "retrieval_mode": self.retrieval_mode,
            "embedding_revision": state.snapshot.embedding_revision,
        })


class EvidenceService:
    def __init__(self, repository):
        self.repository = repository

    def read(self, state, candidate_id):
        candidate = state.authorize_candidate(candidate_id)
        if candidate is None:
            return tool_result(state, "read_document", "not_found", {"candidate_id": candidate_id})
        tenant, snap = state.tenant_context.tenant_id, state.snapshot_id
        doc = self.repository.get_document(tenant, candidate.version_id, snap)
        if doc is None:
            return tool_result(state, "read_document", "not_found", {"candidate_id": candidate_id})
        section = self.repository.get_section(tenant, candidate.version_id, candidate.section_id, snap)
        if section is None or doc.document_id != candidate.document_id:
            raise AgentError("internal_error", "已授权来源的章节损坏。")
        existing = next((e for e in state.evidence.values()
                         if (e.document_id, e.version_id, e.section_id) == candidate.source_ref), None)
        evidence = existing or ReadEvidence(
            "E-" + uuid4().hex, tenant, state.request_id, snap, candidate_id,
            doc.document_id, doc.version_id, doc.version_date, doc.title,
            section.section_id, section.text, doc.scope, doc.status,
            doc.effective_from, doc.effective_to, doc.supersedes, doc.priority,
            section.faq_topic_ids,
        )
        state.register_evidence(evidence)
        state.candidate_reads[candidate_id] = evidence.evidence_id
        state.conflict_assessments = self.assess_conflicts(state)
        return tool_result(state, "read_document", "ok", {
            "candidate_id": candidate_id, "document_id": doc.document_id,
            "version_id": doc.version_id, "section_id": section.section_id,
            "heading": section.heading, "text": section.text,
        }, (evidence,))

    def assess_conflicts(self, state):
        assessments = []
        for left, right in combinations(state.evidence.values(), 2):
            skeleton_left = re.sub(r"\d+(?:\.\d+)?", "#", left.quote)
            skeleton_right = re.sub(r"\d+(?:\.\d+)?", "#", right.quote)
            same_topic = set(left.faq_topic_ids) & set(right.faq_topic_ids)
            same_numeric_rule = skeleton_left == skeleton_right
            # 不将所有不相同的自然语言片段硬判为同一规则。
            if not same_topic and not same_numeric_rule:
                continue
            basis = ()
            if left.scope and right.scope and not set(left.scope).intersection(right.scope):
                relation, basis = "scope_disjoint", ("scope",)
            elif (left.effective_to and right.effective_from
                  and left.effective_to < right.effective_from) or (
                  right.effective_to and left.effective_from
                  and right.effective_to < left.effective_from):
                relation, basis = "scope_disjoint", ("effective_from", "effective_to")
            elif right.version_id in left.supersedes or left.version_id in right.supersedes:
                mutual = (right.version_id in left.supersedes
                          and left.version_id in right.supersedes)
                relation = "unresolved_conflict" if mutual else "explicit_supersession"
                basis = ("supersedes",)
            elif (left.priority is not None and right.priority is not None
                  and left.priority != right.priority and left.scope
                  and set(left.scope) == set(right.scope)):
                relation, basis = "explicit_priority", ("scope", "priority")
            elif left.quote == right.quote:
                relation = "consistent"
            elif same_numeric_rule:
                relation = "unresolved_conflict"
            else:
                relation = "insufficient_data"
            assessments.append(ConflictAssessment(
                "conflict-" + uuid4().hex, left.tenant_id, state.request_id,
                state.snapshot_id, (left.source_candidate_id, right.source_candidate_id),
                (left.evidence_id, right.evidence_id), relation, basis,
                ("适用条件或来源关系尚未确认",)
                if relation in {"unresolved_conflict", "insufficient_data"} else (),
            ))
        return assessments


def resolved_evidence(state):
    """只用明示关系去掉败方；日期/排名不参与。循环关系没有可选赢家。"""
    excluded = set()
    for assessment in state.conflict_assessments:
        left, right = (state.evidence[eid] for eid in assessment.evidence_ids)
        if assessment.relation == "explicit_supersession":
            loser = right if right.version_id in left.supersedes else left
            excluded.add(loser.evidence_id)
        elif assessment.relation == "explicit_priority":
            loser = left if left.priority < right.priority else right
            excluded.add(loser.evidence_id)
    return [e for e in state.evidence.values() if e.evidence_id not in excluded]
