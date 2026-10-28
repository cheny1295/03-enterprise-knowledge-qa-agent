"""单进程串行全量摄取与 manifest 发布；不属于问答工具白名单。"""

import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .contracts import AgentError, Document, FaqEntry, KnowledgeSnapshot, Section
from .embedding import QUERY_PREFIX
from .knowledge import KnowledgeRepository, validate_documents


def tenant_folder(root, tenant_id):
    # 可逆编码，不将未经处理的租户字符串拼入磁盘路径。
    return Path(root) / tenant_id.encode("utf-8").hex()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AgentError("knowledge_load_error", "持久知识包不可读取。") from exc


def package_from_json(raw, active=False):
    try:
        manifest_data = dict(raw["manifest"])
        manifest_data["document_versions"] = tuple(manifest_data["document_versions"])
        if active:
            manifest_data["publication_state"] = "ACTIVE"
        manifest = KnowledgeSnapshot(**manifest_data)
        documents = []
        for record in raw["documents"]:
            values = dict(record)
            values["sections"] = tuple(Section(
                s["section_id"], s["heading"], s["text"], tuple(s.get("faq_topic_ids", ())),
            ) for s in values["sections"])
            for key in ("scope", "supersedes", "keywords"):
                values[key] = tuple(values.get(key, ()))
            documents.append(Document(**values))
        faqs = tuple(FaqEntry(
            f["tenant_id"], f["snapshot_id"], f["faq_topic_id"], f["canonical_question"],
            tuple(f["aliases"]), tuple(f["required_conditions"]),
            tuple(tuple(ref) for ref in f["source_refs"]), f["topic_complete"],
        ) for f in raw.get("faqs", []))
        return manifest, tuple(documents), faqs
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentError("knowledge_load_error", "持久知识包字段不符合契约。") from exc


class IngestionService:
    def __init__(self, root, identities, repository, embedding, vector_store):
        self.root = Path(root)
        self.identities = identities
        self.repository = repository
        self.embedding = embedding
        self.vector_store = vector_store
        self._lock = RLock()

    def _snapshot_path(self, tenant_id, snapshot_id):
        if not re.fullmatch(r"snap-[0-9a-f]{32}", snapshot_id):
            raise AgentError("invalid_arguments", "管理快照 ID 格式无效。")
        return tenant_folder(self.root / "knowledge", tenant_id) / (snapshot_id + ".json")

    def _active_path(self, tenant_id):
        return tenant_folder(self.root / "knowledge", tenant_id) / "active.json"

    def ingest(self, context, documents, faqs=()):
        with self._lock:
            self.identities.authorize(context, "knowledge_editor")
            documents = tuple(documents)
            validate_documents(documents)
            if any(d.tenant_id != context.tenant_id for d in documents):
                raise AgentError("access_denied", "源资料租户与编辑身份不一致。")
            snapshot_id = "snap-" + uuid4().hex
            manifest = KnowledgeSnapshot(
                snapshot_id, context.tenant_id, "data-" + uuid4().hex,
                "bge-chunk320-overlap48-v1", self.embedding.model_id,
                self.embedding.revision, self.embedding.dimension, "BUILDING",
                tuple(d.version_id for d in documents),
                {"query_prefix": QUERY_PREFIX, "normalize": True, "distance": "Cosine",
                 "chunk_tokens": 320, "overlap_tokens": 48},
            )
            faqs = tuple(replace(f, snapshot_id=snapshot_id) for f in faqs)
            # 发布前校验完整来源映射，不让 FAQ 漏列同 topic 冲突来源。
            staging = KnowledgeRepository()
            staging.install(replace(manifest, publication_state="ACTIVE"), documents, faqs)
            path = self._snapshot_path(context.tenant_id, snapshot_id)
            package = {"manifest": asdict(manifest),
                       "documents": [asdict(d) for d in documents],
                       "faqs": [asdict(f) for f in faqs]}
            try:
                write_json(path, package)
                chunks = self.embedding.chunks(documents, snapshot_id) if documents else []
                for chunk in chunks:
                    chunk.payload["index_version"] = manifest.index_version
                vectors = self.embedding.embed_documents(
                    [chunk.text_for_embedding for chunk in chunks],
                ) if chunks else []
                if chunks:
                    self.vector_store.upsert(chunks, vectors)
                    if self.vector_store.count(context.tenant_id, snapshot_id) != len(chunks):
                        raise AgentError("index_version_mismatch", "向量入库数量不一致。")
                self.identities.authorize(context, "knowledge_editor")
                package["manifest"]["publication_state"] = "READY"
                write_json(path, package)
            except Exception as exc:
                package["manifest"]["publication_state"] = "FAILED"
                try:
                    write_json(path, package)
                except OSError:
                    pass
                code = exc.code if isinstance(exc, AgentError) else "internal_error"
                return {"tenant_id": context.tenant_id, "snapshot_id": snapshot_id,
                        "publication_state": "FAILED", "error_code": code}
            return {"tenant_id": context.tenant_id, "snapshot_id": snapshot_id,
                    "publication_state": "READY", "error_code": None}

    def publish(self, context, snapshot_id):
        with self._lock:
            self.identities.authorize(context, "knowledge_editor")
            folder = tenant_folder(self.root / "knowledge", context.tenant_id)
            blocked_path = folder / "blocked.json"
            blocked = read_json(blocked_path) if blocked_path.exists() else []
            if snapshot_id in blocked:
                raise AgentError("access_denied", "已阻断的旧快照不能重新发布。")
            erase_path = folder / "erase.json"
            if (erase_path.exists()
                    and read_json(erase_path).get("replacement_snapshot_id") != snapshot_id):
                raise AgentError("access_denied", "擦除尚未完成，只能发布其替代快照。")
            raw = read_json(self._snapshot_path(context.tenant_id, snapshot_id))
            manifest, documents, faqs = package_from_json(raw)
            if (manifest.tenant_id != context.tenant_id or manifest.snapshot_id != snapshot_id
                    or manifest.publication_state != "READY"):
                raise AgentError("index_version_mismatch", "只能发布当前租户 READY 快照。")
            active = replace(manifest, publication_state="ACTIVE")
            staging = KnowledgeRepository()
            staging.install(active, documents, faqs)
            active_path = self._active_path(context.tenant_id)
            previous = read_json(active_path)["snapshot_id"] if active_path.exists() else None
            if previous == snapshot_id:
                raise AgentError("index_version_mismatch", "该快照已经发布。")
            self.identities.authorize(context, "knowledge_editor")
            # 文件是重启后的单一入口；在途请求仍持有原来注入的 immutable manifest。
            try:
                self.repository.install(
                    active, documents, faqs,
                    before_activate=lambda: write_json(active_path, {
                        "tenant_id": context.tenant_id, "snapshot_id": snapshot_id,
                    }),
                )
            except OSError as exc:
                raise AgentError("knowledge_load_error", "发布引用写入失败，未切换快照。") from exc
            return {"tenant_id": context.tenant_id, "active_snapshot_id": snapshot_id,
                    "previous_snapshot_id": previous, "error_code": None}

    def load_active(self, context):
        self.identities.authorize(context)
        pointer = read_json(self._active_path(context.tenant_id))
        if (not isinstance(pointer, dict) or pointer.get("tenant_id") != context.tenant_id
                or not isinstance(pointer.get("snapshot_id"), str)):
            raise AgentError("access_denied", "租户发布引用无效。")
        folder = tenant_folder(self.root / "knowledge", context.tenant_id)
        blocked_path = folder / "blocked.json"
        blocked = read_json(blocked_path) if blocked_path.exists() else []
        if pointer["snapshot_id"] in blocked:
            raise AgentError("access_denied", "当前知识快照已停止使用。")
        erase_path = folder / "erase.json"
        if (erase_path.exists()
                and read_json(erase_path).get("replacement_snapshot_id") != pointer["snapshot_id"]):
            raise AgentError("access_denied", "当前租户正在擦除旧知识快照。")
        raw = read_json(self._snapshot_path(context.tenant_id, pointer["snapshot_id"]))
        original, documents, faqs = package_from_json(raw)
        if original.publication_state != "READY":
            raise AgentError("knowledge_load_error", "发布引用指向未完成知识包。")
        manifest = replace(original, publication_state="ACTIVE")
        if manifest.tenant_id != context.tenant_id or manifest.snapshot_id != pointer["snapshot_id"]:
            raise AgentError("access_denied", "知识包不属于当前发布域。")
        self.repository.install(manifest, documents, faqs)

    def delete(self, context, document_id, erase=False):
        with self._lock:
            self.identities.authorize(context, "knowledge_editor")
            folder = tenant_folder(self.root / "knowledge", context.tenant_id)
            erase_path = folder / "erase.json"
            pending = read_json(erase_path) if erase_path.exists() else None
            if pending:
                if not erase or pending["document_id"] != document_id:
                    raise AgentError("access_denied", "请先续完当前文档的擦除操作。")
                if pending["replacement_snapshot_id"]:
                    return self._finish_erase(context, pending)
                # 仅 editor 的续清理可读已阻断原包；问答入口仍拒绝该包。
                current, documents, entries = package_from_json(read_json(
                    self._snapshot_path(context.tenant_id, pending["source_snapshot_id"]),
                ))
                if current.tenant_id != context.tenant_id:
                    raise AgentError("access_denied", "擦除来源不属于当前租户。")
            else:
                current = self.repository.load_snapshot(context)
                documents = self.repository.documents(context.tenant_id, current.snapshot_id)
                entries = self.repository.faqs(context.tenant_id, current.snapshot_id)
            if not any(d.document_id == document_id for d in documents):
                raise AgentError("invalid_arguments", "当前知识包没有指定文档。")
            remaining = tuple(d for d in documents if d.document_id != document_id)
            faqs = []
            for entry in entries:
                refs = tuple(ref for ref in entry.source_refs if ref[0] != document_id)
                if refs:
                    faqs.append(replace(entry, source_refs=refs))
            old_paths = list(folder.glob("snap-*.json")) if erase else []
            if erase:
                blocked_path = folder / "blocked.json"
                blocked = set(read_json(blocked_path)) if blocked_path.exists() else set()
                old_ids = {p.stem for p in old_paths}
                pending = {
                    "document_id": document_id, "source_snapshot_id": current.snapshot_id,
                    "old_snapshot_ids": sorted(old_ids), "replacement_snapshot_id": None,
                }
                write_json(erase_path, pending)
                for snapshot_id in old_ids:
                    self.repository.block_snapshot(context.tenant_id, snapshot_id)
                write_json(blocked_path, sorted(blocked | old_ids))
                self.identities.invalidate_tenant(context.tenant_id)
                context = self.identities.resolve_identity(context.principal_id, context.tenant_id)
            build = self.ingest(context, remaining, faqs)
            if build["publication_state"] != "READY":
                return build  # 隐私删除失败也不恢复已经阻断的旧快照。
            if erase:
                pending["replacement_snapshot_id"] = build["snapshot_id"]
                write_json(erase_path, pending)
                return self._finish_erase(context, pending)
            return self.publish(context, build["snapshot_id"])

    def _finish_erase(self, context, pending):
        """先发布无目标资料的新包，再幂等清理旧包；失败保留续清理记录。"""
        replacement = pending["replacement_snapshot_id"]
        active_path = self._active_path(context.tenant_id)
        pointer = read_json(active_path) if active_path.exists() else {}
        if pointer.get("snapshot_id") != replacement:
            publication = self.publish(context, replacement)
        else:
            publication = {
                "tenant_id": context.tenant_id, "active_snapshot_id": replacement,
                "previous_snapshot_id": pending["source_snapshot_id"], "error_code": None,
            }
        for snapshot_id in pending["old_snapshot_ids"]:
            self.vector_store.delete_snapshot(context.tenant_id, snapshot_id)
            self.repository.discard_snapshot(context.tenant_id, snapshot_id)
            path = self._snapshot_path(context.tenant_id, snapshot_id)
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)
        trace_folder = tenant_folder(self.root / "traces", context.tenant_id)
        for path in trace_folder.glob("*.jsonl"):
            path.unlink()
        erase_path = tenant_folder(self.root / "knowledge", context.tenant_id) / "erase.json"
        erase_path.unlink()
        return publication
