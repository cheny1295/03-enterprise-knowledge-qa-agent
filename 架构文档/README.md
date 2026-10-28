# 架构设计文档

本目录承载企业知识问答 Agent 的详细设计，目标是让评审者沿着
“需求 → 架构 → 状态 → 工具 → 证据 → 验证”逐层阅读，而不是在实现文件中猜测边界。
设计基于：

- [`../docs/02-solution.md`](../docs/02-solution.md)：现行六层方案与实现说明；
- [`../docs/06-open-source-reference-survey.md`](../docs/06-open-source-reference-survey.md)：精简后的开源架构参考；
- [`../docs/07-recursive-coverage-and-open-questions.md`](../docs/07-recursive-coverage-and-open-questions.md)：精简后的问题覆盖与未决项。

## 阅读路径

```text
需求与目标
    ↓
系统分层与依赖
    ↓
数据/接口契约
    ↓
运行状态与动作循环
    ↓
工具边界与证据链
    ↓
可靠性、安全与评估
```

## 文档导航

1. [`01-system-architecture.md`](01-system-architecture.md)：架构边界、分层职责、依赖方向、模块结构和端到端数据流。
2. [`02-data-and-interface-contracts.md`](02-data-and-interface-contracts.md)：领域对象、资料格式、模型动作、工具参数、结果和 Trace 契约。
3. [`03-agent-runtime-and-decision.md`](03-agent-runtime-and-decision.md)：Agent 状态机、动作选择、证据处理、冲突/澄清与停止规则。
4. [`04-reliability-security-and-evaluation.md`](04-reliability-security-and-evaluation.md)：错误分类、安全边界、测试矩阵、Trace 检查和评测方法。
5. [`05-layer-by-layer-design.md`](05-layer-by-layer-design.md)：每个系统层的详细职责、接口、生命周期、失败处理和验证设计。
6. [`06-state-by-state-design.md`](06-state-by-state-design.md)：每个运行状态的进入条件、状态动作、退出条件、可转移状态和 Trace 要求。
7. [`07-tool-by-tool-design.md`](07-tool-by-tool-design.md)：每个业务工具的参数、执行步骤、返回契约、错误语义、安全边界和测试用例。
8. [`08-review-resolution.md`](08-review-resolution.md)：规范性架构基线、统一身份链、状态/错误契约和决策规则。
9. [`09-repair-plan-and-closure.md`](09-repair-plan-and-closure.md)：子 agent 审查问题清单、对应修复动作和收敛记录。
10. [`10-routing-vector-and-tenant-design.md`](10-routing-vector-and-tenant-design.md)：向量库、意图路由、租户隔离、三路回复和摄取发布设计。

## 权威设计基线

现行设计由 01–08 与 10 共同组成：01/05 定义分层，02 定义字段和错误映射，
03/06 定义运行路径，04 定义验收，07 定义工具，08 汇总决策，10 细化向量/租户/
三路回复专题。09 记录修复及覆盖状态，不新增另一套接口。docs/06/07 只承担
“为什么这样设计”的参考职责，不作为冲突字段或实现接口的依据。

### 设计摘要

| 评审问题 | 统一答案 |
|---|---|
| 谁决定访问范围 | 可信入口解析 `Principal` 与 `TenantContext`，不是用户正文 |
| 谁决定下一步 | 模型适配器提出结构化动作，编排器校验后执行 |
| 什么才能被引用 | 只有 Read 产生的 `ReadEvidence` |
| 冲突如何处理 | 先保留双方证据，只有显式优先级/范围才可裁决 |
| 失败如何表达 | `status`、`error_code`、`stop_reason` 分离 |
| 如何防止无限循环 | 模型预算、工具预算和生命周期状态共同门禁 |

- 系统固定为 6 个业务层；Result、校验、Trace 和错误归一化是横切能力，不是第 7 层。
- `AgentState.lifecycle_state` 表示运行阶段；`AnswerResult.status` 表示最终业务结果。
- Search 产生 `SearchCandidate`；Read 产生最终 Claim 可引用的 `ReadEvidence`。
- Evidence 创建、去重、来源校验和冲突分析由知识与证据层独占。
- `ask_clarification` 与 `finish` 都必须经过统一结果校验。
- 应用服务在知识路径完成可信身份和路由准备后绑定一次当前租户只读快照并注入所有工具；非事实模板和前置拒绝不加载知识。
- `status`、`error_code`、`stop_reason` 不得混用；工具调用禁止自动重放。
- 所有 ToolResult（包含错误和 not_found）先转换为 Observation；所有最终路径必须经过 `RESULT_VALIDATION`。
- 未决 `ConflictAssessment` 必须传递到最终结果并覆盖冲突双方证据。

## 设计与实现状态标记

本目录定义目标设计，不代表业务实现已经全部完成。2026-10-28 已进入分阶段实现，实际代码与局部测试记录以 [`../docs/08-implementation-progress.md`](../docs/08-implementation-progress.md) 和 [`../docs/03-evaluation.md`](../docs/03-evaluation.md) 为准。以下是独立的进度维度，不是一个互斥枚举；例如同一能力可以 contracted 且 implementation_pending：

- `defined`：架构责任和边界已有定义；
- `contracted`：字段、状态、输入输出及错误映射已形成规范契约；
- `implemented_locally`：已有本地实现；仅列明的局部用例具有运行证据；
- `implementation_pending`：实现尚未完成或不属于当前 MVP；
- `runtime_evidence_pending`：尚无对应运行/评测证据。

09 第 9 节保留设计阶段快照，第 12 节同步实现阶段状态。MVP 外的能力明确标记范围外，
不暗示首版承诺实现。docs/08 和 docs/09 是历史开发记录；当前交付结论以 docs/03 为准。

2026-10-28 本轮已补显式条件入口、分类前历史补全、明示来源裁决、覆盖/失败恢复校验及擦除续清理。实际修改与局部证据见 [`../docs/09-architecture-implementation-completion.md`](../docs/09-architecture-implementation-completion.md)；不代表真实向量/模型后端已验收。

## MVP 范围和交付边界

- 首版为本地 CLI；知识源为合成 FAQ/制度文件。
- 设计包含当前租户快照内的 Qdrant local 向量 + 词项混合召回；当前运行材料采用显式
  fixture 词项路径。真实 Qdrant/CUDA 尚未执行，不把目标设计当作实际运行结果。
- 意图识别区分非事实快答、FAQ 快答、深层查询和兜底；事实快答仍须 Search→Read→统一校验。
- TenantContext 由可信入口确定，贯穿索引、检索、原文、Evidence、Trace 和输出；问题和模型动作不能切换租户。
- Agent 通过模型适配器选择结构化动作；离线 Mock 必须可运行，真实模型可选。
- 工具白名单仅含文档检索和原文读取；不开放任意网络、文件系统或代码执行工具。
- 引用必须来自工具返回的证据对象；模型不能自行编造文档 ID、日期或原文。
- 未知的制度优先级不得推断；未解决冲突时请求澄清。
- 步数/工具调用受限；Trace 记录可审查事实，不记录模型内部思维链。
- 当前运行命令见项目根目录 README，局部结果和未测范围见 `docs/03-evaluation.md`；
  不将离线测试或替身接线当作真实向量/模型验收。

