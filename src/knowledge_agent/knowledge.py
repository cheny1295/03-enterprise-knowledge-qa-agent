"""不可变租户知识包；内存 fixture 与磁盘 manifest 共用读取契约。"""

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

from .contracts import (
    AgentError, Document, FaqEntry, KnowledgeSnapshot, Section, TenantContext,
)


def load_documents(path: Path) -> tuple[Document, ...]:
    documents = []
    try:
        with path.open(encoding="utf-8-sig") as source:
            for line_no, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    topics = tuple(raw.get("faq_topic_ids", ()))
                    sections = tuple(Section(
                        section["section_id"], section["heading"], section["text"],
                        tuple(section.get("faq_topic_ids", topics)),
                    ) for section in raw["sections"])
                    documents.append(Document(
                        tenant_id=raw["tenant_id"], document_id=raw["document_id"],
                        version_id=raw["version_id"], version_date=raw["version_date"],
                        title=raw["title"], sections=sections,
                        scope=tuple(raw.get("scope", ())),
                        status=raw.get("status", "active"),
                        effective_from=raw.get("effective_from"),
                        effective_to=raw.get("effective_to"),
                        supersedes=tuple(raw.get("supersedes", ())),
                        priority=raw.get("priority"),
                        keywords=tuple(raw.get("keywords", ())),
                    ))
                except (KeyError, TypeError, ValueError) as exc:
                    raise AgentError(
                        "knowledge_load_error", f"资料第 {line_no} 行字段无效。"
                    ) from exc
    except OSError as exc:
        raise AgentError("knowledge_load_error", "无法读取知识资料。") from exc
    return tuple(documents)


def validate_documents(documents: tuple[Document, ...]) -> None:
    seen = set()
    for doc in documents:
        key = doc.tenant_id, doc.version_id
        required = (doc.tenant_id, doc.document_id, doc.version_id, doc.title)
        if (not all(isinstance(v, str) and v.strip() for v in required)
                or key in seen or doc.status not in {"active", "withdrawn"}
                or not doc.sections):
            raise AgentError("knowledge_load_error", "资料身份、状态或版本无效。")
        seen.add(key)
        try:
            date.fromisoformat(doc.version_date)
            for value in (doc.effective_from, doc.effective_to):
                if value is not None:
                    date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise AgentError("knowledge_load_error", "资料日期必须是 ISO 日期。") from exc
        if (doc.effective_from and doc.effective_to
                and doc.effective_from > doc.effective_to):
            raise AgentError("knowledge_load_error", "资料有效期起止顺序无效。")
        if doc.priority is not None and type(doc.priority) is not int:
            raise AgentError("knowledge_load_error", "资料 priority 必须为整数或 null。")
        section_ids = set()
        for section in doc.sections:
            if (not isinstance(section.text, str) or not section.text.strip()
                    or not isinstance(section.heading, str)
                    or not isinstance(section.section_id, str) or not section.section_id
                    or section.section_id in section_ids):
                raise AgentError("knowledge_load_error", "章节 ID、标题或原文无效。")
            section_ids.add(section.section_id)
        for values in (doc.scope, doc.keywords, doc.supersedes):
            if not all(isinstance(value, str) and value for value in values):
                raise AgentError("knowledge_load_error", "资料列表字段必须为字符串。")


class KnowledgeRepository:
    """对象在发布后不可变；旧快照保留到显式回收，不隐式刷新。"""

    def __init__(self, documents=(), faqs=(), active_loader=None):
        self._packages = {}
        self._active = {}
        self._blocked = set()
        self._active_loader = active_loader
        by_tenant = defaultdict(list)
        for doc in documents:
            by_tenant[doc.tenant_id].append(doc)
        for tenant, tenant_docs in by_tenant.items():
            manifest = KnowledgeSnapshot(
                "snap-" + uuid4().hex, tenant, "fixture-v1", "lexical-fixture-v1",
                "fixture", "fixture-v1", 0, "ACTIVE",
                tuple(doc.version_id for doc in tenant_docs),
            )
            # fixture FAQ 的 snapshot_id 与新分配 ID 一起绑定，不能跨租户补齐。
            entries = tuple(replace(faq, snapshot_id=manifest.snapshot_id)
                            for faq in faqs if faq.tenant_id == tenant)
            self.install(manifest, tuple(tenant_docs), entries)

    def install(self, manifest, documents, faqs=(), *, before_activate=None) -> None:
        documents, faqs = tuple(documents), tuple(faqs)
        validate_documents(documents)
        if (manifest.publication_state != "ACTIVE"
                or any(doc.tenant_id != manifest.tenant_id for doc in documents)
                or set(manifest.document_versions) != {d.version_id for d in documents}):
            raise AgentError("knowledge_load_error", "知识包和发布清单不一致。")
        key = manifest.tenant_id, manifest.snapshot_id
        if key in self._packages or key in self._blocked:
            raise AgentError("index_version_mismatch", "不能覆盖已发布快照。")
        topic_refs = defaultdict(set)
        for doc in documents:
            for section in doc.sections:
                for topic in section.faq_topic_ids:
                    topic_refs[topic].add((doc.document_id, doc.version_id, section.section_id))
        seen_topics = set()
        for faq in faqs:
            if (faq.tenant_id != manifest.tenant_id or faq.snapshot_id != manifest.snapshot_id
                    or faq.faq_topic_id in seen_topics
                    or not faq.source_refs
                    or set(faq.source_refs) != topic_refs[faq.faq_topic_id]):
                raise AgentError("knowledge_load_error", "FAQ 来源组不完整或跨知识域。")
            seen_topics.add(faq.faq_topic_id)
        # 全部校验通过后才持久切换引用；回调失败不修改内存 ACTIVE。
        if before_activate is not None:
            before_activate()
        self._packages[key] = (manifest, documents, faqs)
        self._active[manifest.tenant_id] = manifest.snapshot_id

    def load_snapshot(self, tenant_context: TenantContext) -> KnowledgeSnapshot:
        tenant = tenant_context.tenant_id
        if tenant not in self._active and self._active_loader is not None:
            self._active_loader(tenant_context)
        snapshot_id = self._active.get(tenant)
        if snapshot_id is None:
            raise AgentError("knowledge_load_error", "当前租户尚无已发布知识快照。")
        return self._package(tenant, snapshot_id)[0]

    def _package(self, tenant_id, snapshot_id):
        if (tenant_id, snapshot_id) in self._blocked:
            raise AgentError("access_denied", "当前知识快照已禁止访问。")
        package = self._packages.get((tenant_id, snapshot_id))
        if package is None:
            raise AgentError("knowledge_load_error", "当前知识快照不可用。")
        return package

    def documents(self, tenant_id, snapshot_id):
        return self._package(tenant_id, snapshot_id)[1]

    def faqs(self, tenant_id, snapshot_id):
        return self._package(tenant_id, snapshot_id)[2]

    def get_document(self, tenant_id, version_id, snapshot_id=None):
        snapshot_id = snapshot_id or self._active.get(tenant_id)
        for doc in self.documents(tenant_id, snapshot_id):
            if doc.version_id == version_id:
                return doc
        return None

    def get_section(self, tenant_id, version_id, section_id, snapshot_id=None):
        doc = self.get_document(tenant_id, version_id, snapshot_id)
        return next((s for s in doc.sections if s.section_id == section_id), None) if doc else None

    def block_snapshot(self, tenant_id, snapshot_id):
        self._blocked.add((tenant_id, snapshot_id))

    def discard_snapshot(self, tenant_id, snapshot_id):
        self.block_snapshot(tenant_id, snapshot_id)
        self._packages.pop((tenant_id, snapshot_id), None)

    def assert_readable(self, tenant_id, snapshot_id):
        self._package(tenant_id, snapshot_id)
