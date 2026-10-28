"""合成场景入口：只打印实际输出，Trace 由项目的写出模块保存。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_agent.configuration import load_faqs, load_identities
from knowledge_agent.contracts import AgentError
from knowledge_agent.knowledge import KnowledgeRepository, load_documents
from knowledge_agent.models import OfflineModel
from knowledge_agent.service import ApplicationService
from knowledge_agent.trace import write_trace


class UnavailableSearch:
    def search(self, state, query, filters=None, limit=5):
        raise AgentError("source_unavailable", "合成故障：检索服务不可用。", retryable=True)


def main():
    identities = load_identities(ROOT / "config" / "demo-identities.json")
    single = KnowledgeRepository(load_documents(ROOT / "data" / "single-document.jsonl"))
    multi = KnowledgeRepository(
        load_documents(ROOT / "data" / "documents.jsonl"),
        load_faqs(ROOT / "config" / "demo-faqs.json"),
    )
    cases = [
        ("01-normal", single, {"question": "报销期限多久"}, "demo-user", "tenant-A", None),
        ("02-conflict", multi, {"question": "报销期限多久"}, "demo-user", "tenant-A", None),
        ("03-tool-failure", single, {"question": "报销期限多久"},
         "demo-user", "tenant-A", UnavailableSearch()),
        ("04-outside", single, {"question": "办公设备领用地点"},
         "demo-user", "tenant-A", None),
        ("05-paraphrase", single, {"question": "差旅费用产生后最迟多少天交报销申请"},
         "demo-user", "tenant-A", None),
        ("06-history", single, {
            "question": "那它的申请期限呢",
            "conversation_history": [
                {"role": "user", "content": "差旅报销有什么要求"},
                {"role": "assistant", "content": "请具体说明想查询的制度事项。"},
            ],
        }, "demo-user", "tenant-A", None),
        ("07-tenant-b", multi, {"question": "报销期限多久"},
         "demo-bob", "tenant-B", None),
    ]
    records = []
    for name, repository, request, principal, tenant, search in cases:
        request = {**request, "mode": "mock", "trace": True}
        result, events = ApplicationService(
            identities, repository, OfflineModel(), search_service=search,
        ).ask(request, principal, tenant)
        path = write_trace(ROOT / "examples" / name / "runtime", result, events)
        records.append({
            "case": name, "request": request,
            "fixture": "multi" if repository is multi else "single",
            "principal": principal, "tenant": tenant,
            "output": result.to_dict(),
            "trace": path.relative_to(ROOT).as_posix() if path else None,
        })
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
