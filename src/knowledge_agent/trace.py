"""受控目录的 Trace 写出。失败只增加 warning，不篡改业务 status。"""

import json
import sys
from pathlib import Path

from .ingestion import tenant_folder


def write_trace(root, result, events):
    folder = (tenant_folder(Path(root) / "traces", result.tenant_id)
              if result.tenant_id else Path(root) / "rejected")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (result.request_id + ".jsonl")
        with path.open("w", encoding="utf-8") as output:
            for event in events:
                output.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path
    except OSError:
        result.warnings.append("trace_write_failed")
        print("Trace 写入失败。", file=sys.stderr)
        return None
