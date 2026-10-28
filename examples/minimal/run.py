"""按要求.md运行企业知识问答 Agent 的最小离线示例。

该入口使用现有 ApplicationService 和 OfflineModel，运行结果会写回本目录：
每个场景包含 input.json、output.json 和 runtime/ 下的 Trace。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src"))

from knowledge_agent.configuration import load_faqs, load_identities
from knowledge_agent.contracts import AgentError
from knowledge_agent.knowledge import KnowledgeRepository, load_documents
from knowledge_agent.models import OfflineModel
from knowledge_agent.service import ApplicationService
from knowledge_agent.trace import write_trace


class UnavailableSearch:
    """仅用于最小示例的明确工具失败场景。"""

    def search(self, state, query, filters=None, limit=5):
        raise AgentError(
            "source_unavailable",
            "合成故障：检索服务不可用。",
            retryable=True,
        )


def build_cases():
    return [
        {
            "name": "01-normal",
            "description": "明确答案：单一 D01 来源",
            "fixture": "single",
            "request": {"question": "报销期限多久"},
            "principal": "demo-user",
            "tenant": "tenant-A",
        },
        {
            "name": "02-conflict",
            "description": "文档冲突：D01 与 D02 的期限不同",
            "fixture": "multi",
            "request": {"question": "报销期限多久"},
            "principal": "demo-user",
            "tenant": "tenant-A",
        },
        {
            "name": "03-outside",
            "description": "资料外问题：现有资料不覆盖设备领用",
            "fixture": "single",
            "request": {"question": "办公设备领用地点"},
            "principal": "demo-user",
            "tenant": "tenant-A",
        },
        {
            "name": "04-paraphrase",
            "description": "改写问题：使用相似表达查询期限",
            "fixture": "single",
            "request": {"question": "差旅费用产生后最迟多少天交报销申请"},
            "principal": "demo-user",
            "tenant": "tenant-A",
        },
        {
            "name": "05-history",
            "description": "上下文追问：从上一条用户问题补足对象",
            "fixture": "single",
            "request": {
                "question": "那它的申请期限呢",
                "conversation_history": [
                    {"role": "user", "content": "差旅报销有什么要求"},
                    {"role": "assistant", "content": "请具体说明想查询的制度事项。"},
                ],
            },
            "principal": "demo-user",
            "tenant": "tenant-A",
        },
        {
            "name": "06-tool-failure",
            "description": "工具失败：Search 不可用时返回 execution_error",
            "fixture": "single",
            "request": {"question": "报销期限多久"},
            "principal": "demo-user",
            "tenant": "tenant-A",
            "fault_injection": "UnavailableSearch",
        },
    ]


def load_repository(fixture: str):
    if fixture == "single":
        return KnowledgeRepository(
            load_documents(WORKSPACE / "data" / "single-document.jsonl")
        )
    return KnowledgeRepository(
        load_documents(WORKSPACE / "data" / "documents.jsonl"),
        load_faqs(WORKSPACE / "config" / "demo-faqs.json"),
    )


def run_case(case: dict, output_root: Path, identities) -> dict:
    case_dir = output_root / case["name"]
    case_dir.mkdir(parents=True, exist_ok=True)
    request = {**case["request"], "mode": "mock", "trace": True}
    (case_dir / "input.json").write_text(
        json.dumps(
            {
                "description": case["description"],
                "request": request,
                "fixture": case["fixture"],
                "principal": case["principal"],
                "tenant": case["tenant"],
                "fault_injection": case.get("fault_injection"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    repository = load_repository(case["fixture"])
    search_service = (
        UnavailableSearch() if case.get("fault_injection") else None
    )
    result, events = ApplicationService(
        identities,
        repository,
        OfflineModel(),
        search_service=search_service,
    ).ask(request, case["principal"], case["tenant"])
    trace_path = write_trace(case_dir / "runtime", result, events)
    output = {
        "case": case["name"],
        "description": case["description"],
        "request": request,
        "fixture": case["fixture"],
        "principal": case["principal"],
        "tenant": case["tenant"],
        "output": result.to_dict(),
        "trace": (
            trace_path.relative_to(case_dir).as_posix()
            if trace_path
            else None
        ),
    }
    (case_dir / "output.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "case": case["name"],
        "status": result.status,
        "stop_reason": result.stop_reason,
        "output": f"{case['name']}/output.json",
        "trace": (
            f"{case['name']}/{trace_path.relative_to(case_dir).as_posix()}"
            if trace_path
            else None
        ),
    }


def run_all(output_root: Path) -> list[dict]:
    identities = load_identities(
        WORKSPACE / "config" / "demo-identities.json"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    summary = [run_case(case, output_root, identities) for case in build_cases()]
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行企业知识问答最小离线示例")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="运行材料输出目录，默认写入 examples/minimal",
    )
    args = parser.parse_args(argv)
    summary = run_all(args.output_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
