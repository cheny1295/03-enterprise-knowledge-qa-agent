"""离线 CLI：默认只依赖标准库和合成知识。"""

import argparse
import json
import sys
from pathlib import Path

from .configuration import load_faqs, load_identities
from .contracts import AgentError
from .embedding import CudaEmbedding
from .ingestion import IngestionService, read_json
from .knowledge import KnowledgeRepository, load_documents
from .models import OfflineModel
from .provider import ProviderModel
from .service import ApplicationService, startup_failure
from .trace import write_trace
from .vector_store import HybridSearchService, QdrantStore


DEFAULT_IDENTITIES = Path(__file__).resolve().parents[2] / "config" / "demo-identities.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="企业知识问答 Agent（离线/Mock）")
    parser.add_argument("--question", required=True)
    parser.add_argument("--principal", default="demo-user")
    parser.add_argument("--tenant", default="tenant-A")
    parser.add_argument("--identities", type=Path, default=DEFAULT_IDENTITIES)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--faqs", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--filters", type=Path, help="用户明确确认的过滤条件 JSON 对象")
    parser.add_argument("--historical", action="store_true", help="明确查询历史制度")
    parser.add_argument("--retrieval", choices=("fixture", "hybrid"), default="fixture")
    parser.add_argument("--storage-root", type=Path, default=Path("runtime"))
    parser.add_argument("--embedding-revision")
    parser.add_argument("--mode", choices=("mock", "model"), default="mock")
    parser.add_argument("--trace-root", type=Path)
    args = parser.parse_args(argv)
    if args.faqs and not args.data:
        parser.error("--faqs 需要同时提供 --data。")
    if args.retrieval == "hybrid" and (args.data or args.faqs):
        parser.error("hybrid 只能读取已发布快照，不能同时使用 --data 或 --faqs。")
    if args.retrieval == "hybrid" and not args.embedding_revision:
        parser.error("hybrid 需要 --embedding-revision 指定本地权重的固定 commit。")
    vector_store = None
    try:
        identities = load_identities(args.identities)
        context = identities.resolve_identity(args.principal, args.tenant)
        identities.authorize(context)
        repository = KnowledgeRepository()
        search_service = None
        if args.retrieval == "fixture":
            # 授权后才加载合成知识；纯问候也不会接触数据文件。
            def load_fixture(context):
                documents = load_documents(args.data) if args.data else ()
                faqs = load_faqs(args.faqs) if args.faqs else ()
                package = KnowledgeRepository(documents, faqs)
                snapshot = package.load_snapshot(context)
                repository.install(
                    snapshot, package.documents(context.tenant_id, snapshot.snapshot_id),
                    package.faqs(context.tenant_id, snapshot.snapshot_id),
                )
            repository = KnowledgeRepository(active_loader=load_fixture)
        else:
            embedding = CudaEmbedding(args.embedding_revision)
            vector_store = QdrantStore(args.storage_root / "qdrant")
            ingestion = IngestionService(
                args.storage_root, identities, repository, embedding, vector_store,
            )
            repository = KnowledgeRepository(active_loader=ingestion.load_active)
            ingestion.repository = repository
            search_service = HybridSearchService(repository, embedding, vector_store)
        model = OfflineModel() if args.mode == "mock" else ProviderModel.from_env()
        request = {"question": args.question, "mode": args.mode, "trace": True,
                   "historical": args.historical}
        if args.history:
            request["conversation_history"] = read_json(args.history)
        if args.filters:
            request["filters"] = read_json(args.filters)
        result, events = ApplicationService(
            identities, repository, model, search_service=search_service,
        ).ask(request, args.principal, args.tenant)
    except AgentError as exc:
        result, events = startup_failure(exc)
    finally:
        if vector_store is not None:
            vector_store.close()
    write_trace(args.trace_root or args.storage_root, result, events)
    json.dump(result.to_dict(), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result.status in {"completed", "needs_clarification", "insufficient_evidence"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
