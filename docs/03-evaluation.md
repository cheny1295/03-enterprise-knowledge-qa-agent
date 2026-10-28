# 03 测试预期、实际结果与限制

## 1. 评估方法

评估对象是合成制度资料、标准库 Search/Read 和 OfflineModel，不是真实业务系统。
分别核对业务状态、原文引用、观察驱动路径和错误处理，不能用一次命令成功代替
真实模型效果或完整架构验收。

当前评审入口为 [最小示例](../examples/minimal/README.md)，一次运行六类场景。
每组实际输入、输出和本次请求 Trace 由程序一起生成；最新文件位置见
[summary.json](../examples/minimal/summary.json)。重跑会更新汇总及输出，并保留旧请求 Trace。

## 2. 既有离线测试记录

2026-10-28，在 Windows PowerShell 下执行：

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m unittest discover -s tests -v
```

此前集合结果：`Ran 32 tests in 0.089s`，`OK`，摘录见
[local-tests.md](../examples/local-tests.md)。该记录不包括后加入的 3 项最小示例测试，
不能合并成当前 35 项全量均已重跑的结论。

Trace 默认根目录跟随 `--storage-root`，默认目录的管理擦除关系未专门测试。
迁移只复现最小示例与聚焦测试，不扩大为完整架构或真实后端验收。

| 文件 | 数量 | 实际覆盖 |
|---|---:|---|
| `test_foundation.py` | 7 | 身份、撤权、候选知识域、快照绑定、预算延续与失败计数 |
| `test_knowledge_path.py` | 4 | 租户内 Search、授权 Read、冲突双方、未知候选 |
| `test_routing_and_results.py` | 7 | 非事实快答、混合问候、FAQ、冲突、provider 失败、非法动作、非法引用 |
| `test_cli_and_optional.py` | 4 | CLI 问候/FAQ/租户参数拒绝、embedding revision 前置校验 |
| `test_hybrid_wiring.py` | 2 | 摄取 READY→发布→重载→hybrid 接线、失败构建不替换 ACTIVE；全为替身 |
| `test_architecture_completion.py` | 8 | 显式过滤、上下文追问、历史门禁、明示替代/优先级、未读/截断覆盖、失败恢复、擦除续清理、CLI 启动错误信封 |

另实际执行一次：

```powershell
& 'D:\Anaconda3\envs\myenv_1\python.exe' .\examples\run_examples.py
```

## 3. 最小 Example：预期与实际

2026-10-28，已实际运行 `examples/minimal/run.py` 和
`tests/test_minimal_example.py`。在项目根目录复现：

```powershell
python .\examples\minimal\run.py
python .\tests\test_minimal_example.py
```

Python 应为 3.10+，命令只依赖标准库。第 2 节及下文旧七组材料属于历史记录。

| 场景 | 预期 | 已保存实际结果 |
|---|---|---|
| 明确答案 | 单 D01 回答 30 天并引用 | `completed` |
| 冲突 | 不擅选 15 天，引用双方并追问 | `needs_clarification` |
| 资料外 | 不编造答案或引用 | `insufficient_evidence` |
| 改写 | 改写措辞仍找到 D01 | `completed` |
| 历史追问 | 从历史 user 补足对象并重新取证 | `completed` |
| 工具失败 | 错误不伪装为无资料 | `execution_error / source_unavailable` |

新入口自动保存每组实际输入、输出和对应请求的 Trace，不再依赖手工复制 stdout。
最新材料路径见 [summary.json](../examples/minimal/summary.json)，运行与边界说明见
[最小 Example](../examples/minimal/README.md)。

本次提交区重跑的局部测试结果为 `Ran 3 tests in 0.042s / OK`：
[test-results.txt](../examples/minimal/test-results.txt)。覆盖：

- 六类场景状态、原文引用与输出/Trace 请求 ID 对应关系；
- D01 原文由 30 天改成 45 天后，答案和引用同步变化；不证明任意资料变化均导致不同工具动作；
- 模型或工具预算耗尽时返回 `incomplete / budget_exhausted`。

以上仍为规则 Mock 的流程证据，不能视为真实 LLM 驱动 ReAct 的效果验收。

## 4. 历史七组场景材料

下表中的模型次数包含一次分类；全部 `embedding_calls_used=0`，因为场景运行明确采用词项 fixture，不是向量模式。

| 场景 | 实际结果 | 模型/工具次数 | 输入与输出 | 完整 Trace |
|---|---|---|---|---|
| 明确答案 | completed，引用 D01 30 天原文 | 4 / 2 | [input](../examples/01-normal/input.json)、[output](../examples/01-normal/output.json) | [JSONL](../examples/01-normal/runtime/traces/74656e616e742d41/req-5b28f9c9a4a149eb9702880be417ba36.jsonl) |
| 文档冲突 | needs_clarification，D01 30 天、D02 15 天双方引用 | 5 / 3 | [input](../examples/02-conflict/input.json)、[output](../examples/02-conflict/output.json) | [JSONL](../examples/02-conflict/runtime/traces/74656e616e742d41/req-1e43cb72948e4920b997abf393b0cff4.jsonl) |
| 工具故障 | execution_error / source_unavailable | 3 / 1 | [input](../examples/03-tool-failure/input.json)、[output](../examples/03-tool-failure/output.json) | [JSONL](../examples/03-tool-failure/runtime/traces/74656e616e742d41/req-357dc622ec6640b4b0b2f1ef377fc9e2.jsonl) |
| 资料外问题 | insufficient_evidence，无引用 | 3 / 1 | 问题：办公设备领用地点；仅报销资料 | [JSONL](../examples/04-outside/runtime/traces/74656e616e742d41/req-0c75007ab8d6491e9107f2e4ba1dc46b.jsonl) |
| 改写问题 | completed，D01 30 天原文 | 4 / 2 | 问题：差旅费用产生后最迟多少天交报销申请 | [JSONL](../examples/05-paraphrase/runtime/traces/74656e616e742d41/req-8c1b7f29629e4266965f23dc5defe819.jsonl) |
| 上下文追问 | completed，历史补对象、新轮重新读 D01 | 4 / 2 | 当前“那它的申请期限呢”；前问“差旅报销有什么要求” | [JSONL](../examples/06-history/runtime/traces/74656e616e742d41/req-e3674727fb884c1c9d6fa60af8cd8863.jsonl) |
| B 租户 FAQ | fast_faq / completed，只引用 B 的 D01 60 天 | 4 / 2 | demo-bob / tenant-B；问题“报销期限多久” | [JSONL](../examples/07-tenant-b/runtime/traces/74656e616e742d42/req-f83a282c917544bca7ad7819f058f833.jsonl) |

历史请求定义保留在 [run_examples.py](../examples/run_examples.py)。该旧入口仅打印结果，
不自动更新旧 `output.json`；优先使用最小入口，避免新 Trace 与旧输出混用。

两条不同的离线路径为：正常 `Search → Read → Finish`；冲突 `Search → Read → Read → Finish(needs_clarification)`。故障则为 `Search(error) → Finish(execution_error)`。这些路径由 OfflineModel 依据当前 Observation 选择，仍是规则 Mock，不能据此宣称已满足真实 LLM 驱动 ReAct 的效果验收。

第三组注入可重试标记的合成检索故障，模型看到错误 Observation 后选择结束；没有自动重试。`decision_basis_summary` 是运行时对外部观察的简短概括，不是模型内部推理或模型生成的原因。

## 5. 尚未验证及已知限制

- 本轮未安装可选依赖、下载模型、检查实际 GPU 状态或执行 CUDA 编码；
- 未执行真实 Qdrant upsert/query/delete，未对真实索引测试维度、污染点、过滤和删除；
- 未调用真实 provider，也未测真实 JSON 返回、网络超时和服务端限流；
- 未做性能、召回率、意图准确率或大规模多租户压测；
- 管理 CLI 已接线；构建/发布/重载和擦除续清理有替身证据，真实 Qdrant 删除、跨进程重启和 CLI 管理命令仍未运行验证；
- CLI 的 `--history` 文件解析和真实 hybrid 启动尚未单独运行；应用服务的历史场景已有离线运行记录；
- Mock 仅做简单词项/数字/显式 topic 关系处理，不能可靠识别所有自然语言冲突，也不能判断任意主张是否被原文语义蕴含；
- 显式 scope/date/document 过滤已通过请求 `filters` 进入 `confirmed_filters`，但当前没有自然语言条件自动确认器；默认仍只使用 active 资料，不声称已实现任意条件检索。

当前只可报告：核心离线代码 `implemented_locally`，部分契约有局部运行证据；真实向量、
真实模型及未覆盖场景仍为 `runtime_evidence_pending`。缺少真实资料不阻碍题目要求的合成
数据验收，但规则 Mock 不可替代真实模型驱动的 ReAct 效果验收。

## 6. 交付要求映射

| 要求 | 当前材料与边界 |
|---|---|
| 可复制运行、离线 Mock | README、最小示例说明；无需密钥和第三方包 |
| 模型/工具循环、两条路径 | 编排器、模型适配器、正常/冲突 Trace；真实模型效果待验证 |
| 调用上限和明确未完成 | `runtime.py`、聚焦测试预算用例 |
| 至少三组及六项验收 | 六组 input/output/Trace，包含正常、资料不足、工具失败 |
| 五份文档与依赖配置 | `docs/01` 至 `05`、`requirements.txt`、`.env.example` |
| AI、个人贡献、实际用时 | `04-ai-usage.md`、`05-retrospective.md`；约 5 小时 |
| GitHub URL、题号、SHA、私库权限 | 真实提交前补齐，见 `12-submission.md` |
