"""受信任操作者的摄取/发布 CLI；不在模型工具白名单中。"""

import argparse
import json
import sys
from pathlib import Path

from .cli import DEFAULT_IDENTITIES
from .configuration import load_faqs, load_identities
from .contracts import AgentError
from .embedding import CudaEmbedding
from .ingestion import IngestionService
from .knowledge import KnowledgeRepository, load_documents
from .vector_store import QdrantStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="当前租户的知识摄取与发布")
    parser.add_argument("--identities", type=Path, default=DEFAULT_IDENTITIES)
    parser.add_argument("--principal", default="demo-user")
    parser.add_argument("--tenant", default="tenant-A")
    parser.add_argument("--storage-root", type=Path, default=Path("runtime"))
    parser.add_argument("--embedding-revision", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--data", type=Path, required=True)
    ingest.add_argument("--faqs", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--snapshot", required=True)
    delete = commands.add_parser("delete")
    delete.add_argument("--document", required=True)
    delete.add_argument("--erase", action="store_true")
    args = parser.parse_args(argv)
    vector_store = None
    try:
        identities = load_identities(args.identities)
        context = identities.resolve_identity(args.principal, args.tenant)
        identities.authorize(context, "knowledge_editor")
        embedding = CudaEmbedding(args.embedding_revision)
        vector_store = QdrantStore(args.storage_root / "qdrant")
        repository = KnowledgeRepository()
        service = IngestionService(
            args.storage_root, identities, repository, embedding, vector_store,
        )
        service.repository = KnowledgeRepository(active_loader=service.load_active)
        if args.command == "ingest":
            result = service.ingest(
                context, load_documents(args.data),
                load_faqs(args.faqs) if args.faqs else (),
            )
        elif args.command == "publish":
            result = service.publish(context, args.snapshot)
        else:
            result = service.delete(context, args.document, erase=args.erase)
    except AgentError as exc:
        result = {"error_code": exc.code, "message": exc.message}
    except OSError:
        result = {"error_code": "knowledge_load_error", "message": "管理持久化操作失败，未确认完成。"}
    finally:
        if vector_store is not None:
            vector_store.close()
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if result.get("error_code") else 0


if __name__ == "__main__":
    raise SystemExit(main())
