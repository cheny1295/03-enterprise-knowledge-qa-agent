"""Qdrant local 适配；collection 和租户条件都由应用代码生成。"""

import math
from datetime import date

from .contracts import AgentError
from .embedding import QUERY_PREFIX, VECTOR_DIMENSION
from .retrieval import LexicalSearchService, lexical_ranked


COLLECTION = "enterprise_knowledge_bge_zh_v1_5"


class QdrantStore:
    def __init__(self, path):
        self.path = str(path)
        self._client = None

    def _open(self):
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
                self._client = QdrantClient(path=self.path)
            except Exception as exc:
                raise AgentError("vector_store_error", "Qdrant 本地存储不可用。") from exc
        return self._client

    @staticmethod
    def _models():
        from qdrant_client import models
        return models

    @staticmethod
    def _validate_vector(vector):
        if len(vector) != VECTOR_DIMENSION or not all(math.isfinite(v) for v in vector):
            raise AgentError("index_version_mismatch", "向量必须为 512 维有限数值。")

    def _collection(self, create=False):
        client = self._open()
        models = self._models()
        if create and not client.collection_exists(COLLECTION):
            client.create_collection(
                COLLECTION, vectors_config=models.VectorParams(
                    size=VECTOR_DIMENSION, distance=models.Distance.COSINE,
                ),
            )
        config = client.get_collection(COLLECTION).config.params.vectors
        if (not isinstance(config, models.VectorParams) or config.size != VECTOR_DIMENSION
                or config.distance != models.Distance.COSINE):
            raise AgentError("index_version_mismatch", "向量 collection 配置不匹配。")
        return client

    def _filter(self, tenant_id, snapshot_id, filters):
        models = self._models()
        def exact(key, value):
            return models.FieldCondition(key=key, match=models.MatchValue(value=value))
        conditions = [exact("tenant_id", tenant_id), exact("snapshot_id", snapshot_id)]
        for key, payload_key in (("status", "status"), ("document_ids", "document_id"),
                                 ("scope_any", "scope")):
            if filters.get(key):
                conditions.append(models.FieldCondition(
                    key=payload_key, match=models.MatchAny(any=filters[key]),
                ))
        for value in filters.get("scope_all", []):
            conditions.append(exact("scope", value))
        def day(value):
            return (date.fromisoformat(value) - date(1970, 1, 1)).days
        low, high = filters.get("version_date_from"), filters.get("version_date_to")
        if low or high:
            conditions.append(models.FieldCondition(
                key="version_date_day",
                range=models.Range(gte=day(low) if low else None,
                                   lte=day(high) if high else None),
            ))
        if filters.get("effective_on"):
            value = day(filters["effective_on"])
            conditions.extend([
                models.FieldCondition(key="effective_from_day", range=models.Range(lte=value)),
                models.FieldCondition(key="effective_to_day", range=models.Range(gte=value)),
            ])
        return models.Filter(must=conditions)

    def upsert(self, chunks, vectors):
        if len(chunks) != len(vectors):
            raise AgentError("index_version_mismatch", "chunk 与向量数量不一致。")
        for vector in vectors:
            self._validate_vector(vector)
        try:
            client, models = self._collection(create=True), self._models()
            for offset in range(0, len(chunks), 128):
                client.upsert(
                    COLLECTION, wait=True,
                    points=[models.PointStruct(
                        id=chunk.point_id, vector=vector, payload=chunk.payload,
                    ) for chunk, vector in zip(
                        chunks[offset:offset + 128], vectors[offset:offset + 128],
                    )],
                )
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("vector_store_error", "向量写入失败，未发布快照。") from exc

    def query(self, tenant_id, snapshot_id, vector, filters, *, revision, index_version):
        self._validate_vector(vector)
        try:
            points = self._collection().query_points(
                collection_name=COLLECTION, query=vector,
                query_filter=self._filter(tenant_id, snapshot_id, filters),
                limit=10, with_payload=True, with_vectors=False,
            ).points
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("vector_store_error", "向量查询失败。") from exc
        ranked = {}
        for point in points:
            payload = point.payload or {}
            if (payload.get("tenant_id") != tenant_id
                    or payload.get("snapshot_id") != snapshot_id):
                raise AgentError("internal_error", "召回结果存在知识域污染。")
            if (payload.get("embedding_revision") != revision
                    or payload.get("index_version") != index_version
                    or payload.get("vector_dimension") != VECTOR_DIMENSION):
                raise AgentError("index_version_mismatch", "召回向量配置与快照不一致。")
            ref = tuple(payload.get(key) for key in ("document_id", "version_id", "section_id"))
            if not all(isinstance(value, str) and value for value in ref):
                raise AgentError("internal_error", "向量结果缺少原文定位。")
            ranked[ref] = max(ranked.get(ref, -math.inf), point.score)
        # chunk top-10 到达上限时，即使聚合后 section 少于 10，也不能声称完整。
        return sorted(ranked.items(), key=lambda item: (-item[1], item[0])), len(points) == 10

    def count(self, tenant_id, snapshot_id):
        try:
            return self._collection().count(
                COLLECTION, count_filter=self._filter(tenant_id, snapshot_id, {}), exact=True,
            ).count
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("vector_store_error", "无法确认向量数量。") from exc

    def delete_snapshot(self, tenant_id, snapshot_id):
        try:
            models = self._models()
            self._collection().delete(
                COLLECTION, points_selector=models.FilterSelector(
                    filter=self._filter(tenant_id, snapshot_id, {}),
                ), wait=True,
            )
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("vector_store_error", "向量快照删除失败。") from exc

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None


class HybridSearchService(LexicalSearchService):
    retrieval_mode = "hybrid"

    def __init__(self, repository, embedding, vector_store):
        super().__init__(repository)
        self.embedding = embedding
        self.vector_store = vector_store

    def search(self, state, query, filters=None, limit=5):
        snapshot = state.snapshot
        if (snapshot.embedding_model_id != self.embedding.model_id
                or snapshot.embedding_revision != self.embedding.revision
                or snapshot.vector_dimension != self.embedding.dimension
                or snapshot.index_config.get("query_prefix") != QUERY_PREFIX
                or snapshot.index_config.get("normalize") is not True
                or snapshot.index_config.get("distance") != "Cosine"):
            raise AgentError("index_version_mismatch", "查询编码配置与快照不一致。")
        filters = filters or {"status": ["active"]}
        docs = self.repository.documents(snapshot.tenant_id, snapshot.snapshot_id)
        if not docs:
            return self.build_result(state, docs, query, filters, limit, [], [])
        state.budget.consume_embedding_call()
        vector = self.embedding.embed_query(query)
        ranked, vector_truncated = self.vector_store.query(
            snapshot.tenant_id, snapshot.snapshot_id, vector, filters,
            revision=snapshot.embedding_revision, index_version=snapshot.index_version,
        )
        lexical = lexical_ranked(docs, query, filters)
        # 任一分支失败均抛出，不能偷偷以词项结果冒充混合检索。
        return self.build_result(
            state, docs, query, filters, limit, lexical[:10], ranked,
            branch_truncated=vector_truncated or len(lexical) > 10,
        )
