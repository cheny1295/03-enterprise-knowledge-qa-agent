"""CUDA-only BGE 编码器；仅使用已缓存权重，不触发下载。"""

import re
from dataclasses import dataclass
from uuid import uuid4

from .contracts import AgentError


MODEL_ID = "BAAI/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
VECTOR_DIMENSION = 512


@dataclass(frozen=True)
class KnowledgeChunk:
    point_id: str
    tenant_id: str
    snapshot_id: str
    document_id: str
    version_id: str
    section_id: str
    chunk_id: str
    char_start: int
    char_end: int
    text_for_embedding: str
    payload: dict


class CudaEmbedding:
    model_id = MODEL_ID
    dimension = VECTOR_DIMENSION

    def __init__(self, revision: str):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise AgentError("index_version_mismatch", "请配置实际模型的固定 commit revision。")
        self.revision = revision
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            if not torch.cuda.is_available():
                raise AgentError("embedding_unavailable", "CUDA 不可用，不能执行真实编码。")
            self._model = SentenceTransformer(
                self.model_id, revision=self.revision, device="cuda",
                local_files_only=True, trust_remote_code=False,
            )
            if self._model.get_sentence_embedding_dimension() != self.dimension:
                self._model = None
                raise AgentError("index_version_mismatch", "embedding 输出维度不匹配。")
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(
                "embedding_unavailable", "编码依赖或本地 CUDA 模型不可用。"
            ) from exc
        return self._model

    def _encode(self, texts):
        model = self._load()
        try:
            for text in texts:
                if len(model.tokenizer(text, truncation=False)["input_ids"]) > 512:
                    raise AgentError("invalid_arguments", "编码文本超出模型 token 上限。")
            vectors = model.encode(
                list(texts), batch_size=16, device="cuda",
                normalize_embeddings=True, show_progress_bar=False,
                convert_to_numpy=True,
            )
            return vectors.tolist()
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("embedding_unavailable", "CUDA 编码失败，未回退 CPU。") from exc

    def embed_query(self, query):
        return self._encode([QUERY_PREFIX + query])[0]

    def embed_documents(self, texts):
        return self._encode(texts)

    def chunks(self, documents, snapshot_id):
        tokenizer = self._load().tokenizer
        if not tokenizer.is_fast:
            raise AgentError("embedding_unavailable", "切片需要支持原文 offset 的 tokenizer。")
        chunks = []
        for doc in documents:
            for section in doc.sections:
                prefix = doc.title + "\n" + section.heading + "\n"
                prefix_size = len(tokenizer(prefix, add_special_tokens=False)["input_ids"])
                window = min(320, 512 - prefix_size - tokenizer.num_special_tokens_to_add())
                if window <= 48:
                    raise AgentError("invalid_arguments", "标题和章节名过长，无法安全切片。")
                offsets = tokenizer(
                    section.text, add_special_tokens=False, truncation=False,
                    return_offsets_mapping=True,
                )["offset_mapping"]
                start = 0
                while start < len(offsets):
                    end = min(start + window, len(offsets))
                    char_start = offsets[start][0]
                    char_end = offsets[end - 1][1]
                    if start == 0:
                        char_start = 0
                    if end == len(offsets):
                        char_end = len(section.text)
                    chunk_id = uuid4().hex
                    payload = {
                        "tenant_id": doc.tenant_id, "snapshot_id": snapshot_id,
                        "document_id": doc.document_id, "version_id": doc.version_id,
                        "section_id": section.section_id, "chunk_id": chunk_id,
                        "char_start": char_start, "char_end": char_end,
                        "scope": list(doc.scope), "status": doc.status,
                        "embedding_model_id": self.model_id,
                        "embedding_revision": self.revision,
                        "vector_dimension": self.dimension,
                    }
                    # 日期转换只在存储边界发生，领域对象仍保留 ISO 日期。
                    from datetime import date
                    for field in ("version_date", "effective_from", "effective_to"):
                        value = getattr(doc, field)
                        if value is not None:
                            payload[field + "_day"] = (
                                date.fromisoformat(value) - date(1970, 1, 1)
                            ).days
                    chunks.append(KnowledgeChunk(
                        str(uuid4()), doc.tenant_id, snapshot_id, doc.document_id,
                        doc.version_id, section.section_id, chunk_id, char_start, char_end,
                        prefix + section.text[char_start:char_end], payload,
                    ))
                    if end == len(offsets):
                        break
                    start = end - 48
                if not offsets:
                    raise AgentError("invalid_arguments", "原文章节无法形成 token 切片。")
        return chunks
