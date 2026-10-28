# 最小 Example

这是按照根目录 `要求.md` 和 `题目.md` 制作的最小可复制运行入口。它选择 CLI 方式，
复用本项目 `src/knowledge_agent` 的 `ApplicationService` 和明确标注的 `OfflineModel`。
Python 3.10+ 和标准库即可运行，不需安装第三方依赖或配置密钥。

## 运行

在项目根目录执行：

```powershell
python .\examples\minimal\run.py
```

已配置 Python 的评审环境直接执行上述命令。本地 Conda 环境可将 `python` 替换为
`& 'D:\Anaconda3\envs\myenv_1\python.exe'`。入口通过自身位置定位项目，不依赖原工作目录。

运行后，本目录会为每个场景写入：

- `input.json`：本次实际执行的请求和 fixture；
- `output.json`：本次实际返回的完整结果；
- `runtime/traces/.../*.jsonl`：与该 `request_id` 对应的 Trace；
- `summary.json`：六组场景的状态汇总。

也可以把材料写到另一个目录，避免覆盖本目录已有记录：

```powershell
python .\examples\minimal\run.py `
  --output-root .\examples\minimal\latest
```

## 六组验收场景

| 场景 | 预期状态 | 覆盖要求 |
|---|---|---|
| `01-normal` | `completed` | 明确答案、引用原文 |
| `02-conflict` | `needs_clarification` | 保留 D01/D02 双方引用，不擅自选一方 |
| `03-outside` | `insufficient_evidence` | 资料外问题不编造引用 |
| `04-paraphrase` | `completed` | 改写后的相似问题 |
| `05-history` | `completed` | 上下文追问，助手历史不当作证据 |
| `06-tool-failure` | `execution_error` | 工具失败与资料不足区分 |

正常路径是 `Search → Read → Finish`；冲突路径是
`Search → Read → Read → Finish(needs_clarification)`。这两条路径由
`OfflineModel` 根据工具观察选择下一动作，Trace 会记录步骤、工具参数、返回摘要、
后续动作和简要决策依据。其中决策依据是运行时的观察摘要，不是模型内部思维链或
模型生成的推理。默认最多 6 次模型调用（包含分类）、4 次工具调用，超限返回
`incomplete / budget_exhausted`。

## 局部验证

在项目根目录执行：

```powershell
python .\tests\test_minimal_example.py
```

该命令仅执行 3 项聚焦测试，并保存实际测试输出到本目录 `test-results.txt`：
六类场景与输入/输出/Trace 对应关系、原文 30 天替换成 45 天后答案与引用随之改变、
模型或工具预算耗尽后明确返回未完成。测试生成的临时材料自动清理，不覆盖六组评审材料。

## 交付边界

项目级 `README.md`、`src/`、`tests/`、`data/`、五份规定文档、
`requirements.txt` 和 `.env.example` 均位于项目根目录，此子目录不是独立项目。
`summary.json` 指向每组最新运行的输出和 Trace；重复运行会更新输入和输出，
保留旧随机请求 ID 对应的 Trace，请以汇总文件中的路径为准。
实际投入时间记录为约 5 小时。GitHub URL 和最终 commit SHA 需要在提交者真实创建
仓库并提交后填写；本示例不编造这些信息。个人贡献和 AI 辅助范围见
`docs/04-ai-usage.md`。本示例本身不执行 Git 提交或推送。

## Mock 边界

本 example 不下载模型、不调用外部 LLM、不连接 Qdrant 或 CUDA。`OfflineModel` 是
规则 Mock，只证明离线流程、工具观察驱动、引用回查和错误状态映射，不能证明真实
LLM 的决策质量、向量召回效果或生产系统性能。
