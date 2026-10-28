# examples：离线运行材料

# 评审入口

`examples/minimal/run.py` 一次执行题目的六类场景，自动同步本次
`input.json`、`output.json` 和对应 Trace；详细说明见 `examples/minimal/README.md`。
下文旧七组材料保留为历史记录，不由新入口覆盖。

本目录只收录合成租户、合成制度和离线 `Mock` 的运行材料。随机 `request_id`、`candidate_id`、`evidence_id` 和时间戳来自实际运行，因此每次重跑会不同；材料中的状态、引用关系和 Trace 事件类型应保持一致。

| 材料 | 覆盖场景 | 结论 |
|---|---|---|
| `01-normal` | 单来源、明确事实问题 | `completed`，答案来自 `ReadEvidence` |
| `02-conflict` | D01/D02 同主题但 30/15 天冲突 | `needs_clarification`，保留两侧引用 |
| `03-tool-failure` | 注入合成检索故障 | `execution_error`，不伪装为资料不足 |
| `04-outside` | 制度未覆盖的设备领用问题 | `insufficient_evidence` |
| `05-paraphrase` | 改写后的相似问题 | `completed`，只证明词项 fixture 路径 |
| `06-history` | 依赖上一条用户问题的追问 | `completed`，历史答案不是证据 |
| `07-tenant-b` | 另一租户拥有同名 D01 | `fast_faq`，只引用 B 租户 60 天规则 |

历史示例复现命令（在项目根目录）：

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m knowledge_agent.cli `
  --mode mock --data .\data\single-document.jsonl `
  --question '报销期限多久' --trace-root .\examples\01-normal\runtime

& 'D:\Anaconda3\envs\myenv_1\python.exe' -m knowledge_agent.cli `
  --mode mock --data .\data\documents.jsonl --faqs .\config\demo-faqs.json `
  --question '报销期限多久' --trace-root .\examples\02-conflict\runtime

& 'D:\Anaconda3\envs\myenv_1\python.exe' .\examples\run_examples.py
```

各组 `input.json` 是请求及运行配置说明；其中 `fixture`、`principal`、`tenant`、`fault_injection` 不是直接交给 ApplicationService 的请求正文，具体调用见 `run_examples.py`。

`run_examples.py` 使用 ApplicationService 和真实的离线 Search/Read 代码执行七组输入，仅第三组注入 `UnavailableSearch` 替身。脚本打印实际输入/输出与对应 Trace 路径，不伪造工具返回，也不调用外部模型。各组 `output.json` 根据本轮实际 stdout 保存，ID 与其 Trace 一一对应。重跑会生成新的请求 ID 和 Trace，旧输入/输出材料不自动更新或删除。上面的执行材料不代表真实 embedding、Qdrant 或 provider 已验收。
