# 01 系统架构与模块边界

## 1. 目标和非目标

系统为员工问题提供基于合成制度/FAQ 的可追溯答案。架构优先保证核心链路易运行、可理解、可复现，满足笔试的动态工具决策和失败场景验收。

### 目标

- 支持问题、可信 TenantContext 和可选对话历史。
- 支持非事实快答、FAQ 快答、复杂知识深查和按真实原因兜底。
- 支持意图/深度识别、租户隔离的词项与向量混合检索。
- 由模型策略动态选择检索、读取原文、澄清或结束。
- 以明确文档证据支持事实性回答；暴露资料缺口和未解决冲突。
- 对模型和工具失败给出机器可判定状态。
- 限制 Agent 步数并记录逐步 Trace。
- 提供无外网/无模型的离线运行方式。

### 非目标

- Web 前端、生产级身份认证/租户管理平台、租户内逐文档 RBAC。
- OCR、多模态解析、知识图谱、MCP 服务端、任意代码执行。
- 分布式队列、长期对话存储、高可用及生产级审计平台。
- 分布式向量数据库集群、重排模型和多 Agent。

## 2. 逻辑分层

```text
┌───────────────────────────────────────────────────────┐
│ L1 交互层：CLI                                         │
├───────────────────────────────────────────────────────┤
│ L2 应用服务层：请求组装、快照创建、生命周期            │
├───────────────────────────────────────────────────────┤
│ L3 Agent 编排层：有限循环、动作校验、预算、状态转移     │
├───────────────────────────────────────────────────────┤
│ L4 模型适配层：Mock / 真实模型（与 L5 并列）            │
│ L5 工具执行层：ToolRouter、白名单、参数校验             │
├───────────────────────────────────────────────────────┤
│ L6 知识与证据层：快照、检索、原文、Evidence、冲突分析   │
└───────────────────────────────────────────────────────┘

横切能力：AnswerResult、ResultValidator、TraceWriter、配置、错误归一化
```

测试从横向覆盖各层，不再额外建立复杂测试服务。

## 3. 依赖方向

依赖由外向内，领域契约位于低层，避免模型 SDK 或 CLI 类型进入核心逻辑：

```text
CLI → ApplicationService → AgentOrchestrator
                                 ├─ IntentClassifier / DecisionModel
                                 └─ ToolRouter → EmbeddingModel + tenant KnowledgeSnapshot/HybridIndex

横切：ResultValidator / AnswerResult / TraceWriter
```

- 编排层只依赖模型决策接口，不依赖某个 SDK。
- 工具实现只读注入的租户快照，不自行加载资料或调用决策/分类模型；Search 通过独立 EmbeddingModel 协议取得查询向量。
- Evidence 只由知识与证据层创建；编排层只保存和传递。
- `AnswerResult` 由应用服务返回，`ResultValidator` 校验，`TraceWriter` 由应用服务统一写入。
- CLI 不直接读取知识文件、不调用模型、不实现冲突规则。

## 4. 模块职责

| 模块 | 主要职责 | 不负责 |
|---|---|---|
| `app/cli` | 解析参数、读取一次请求、输出 JSON/可读结果 | Agent 决策 |
| `app/service` | 建立请求 ID 和配置，调用编排器，收集结果 | 检索排序 |
| `agent/orchestrator` | 决策循环、工具路由、状态/预算/终止 | 具体模型 HTTP |
| `agent/policy` | 系统约束、可用动作描述和上下文构造 | 生成引用元数据 |
| `models/protocol` | 定义模型决策接口和结构化动作 | 业务工具实际执行 |
| `models/mock` | 依据确定场景返回动作，离线验证流程 | 证明真实模型质量 |
| `models/provider` | 可选真实模型请求、响应解析和错误映射 | 工具调用和知识访问 |
| `tools/search` | 检索候选证据，保留分数和元数据 | 生成自然语言答案 |
| `tools/read` | 按当前请求 candidate 授权，解析文档/版本/章节后读取原文 | 决定冲突优先级 |
| `knowledge/loader` | 加载并校验合成资料 | 在线模型请求 |
| `knowledge/retriever` | tenant/snapshot 约束下的词项/向量混合排序 | 判断制度政策含义、调用模型 SDK |
| `agent/routing` | 意图/深度分类、三路回复、快转深 | 认证身份、输出未读取的制度事实 |
| `models/embedding` | CUDA 查询/文档编码，固定模型版本 | 检索、生成答案 |
| `app/identity` | 可信身份映射、租户成员资格、读写授权 | 从问题或模型输出推断身份 |
| `knowledge/index` | Qdrant 本地存储、不可变快照、更新删除 | 接受模型指定 collection/tenant |
| `knowledge/evidence` | 建立证据对象、去重、比较显式属性 | 猜测缺失规则 |
| `schemas` | 输入、动作、工具结果、答案和 Trace 契约 | 持久化业务状态 |
| `tests`/`examples` | 验证契约、路径和实际结果 | 替代生产实现 |

首版允许把小模块合并成少量文件，但不得把模型、工具调用和冲突裁决揉进单个 CLI 函数。

## 5. 部署与运行形态

首版为单进程 CLI：

```text
用户命令
  → CLI 请求对象
  → ApplicationService
  → AgentOrchestrator
  → tenant-scoped 原文 + Qdrant local + 混合召回 + MockModel（默认离线）
  → JSON 结果和 JSONL Trace
```

可选真实模型模式通过环境变量读取配置。若模型未配置，CLI 应明确选择 Mock 或提示可用模式，不允许悄悄将真实模型请求降级成预设答案。评审可使用单条命令运行指定样例。

## 6. 在线问答主数据流

本图说明正常流程与统一退出边界；`ask_clarification`、`finish` 和所有失败候选均由 L2 完成结果组装与校验：

```text
CLI → ApplicationService
  → RECEIVED → validate request and trusted TenantContext → VALIDATED
  → VALIDATED: L3 rule/intent/depth preparation
  → nonfactual/fallback candidate → shared finalize
  OR bind immutable tenant snapshot once, resolve FAQ eligibility
  → DECIDING → DecisionModel → AgentAction
  → validate action/schema/allowlist/budget
  ├─ search/read → ToolRouter → L6 same snapshot
  │             → SearchCandidate / ReadEvidence → ToolResult
  │             → L3 Observation → DECIDING
  ├─ ask_clarification/finish → AgentRunResult(CandidateResult)
  └─ 错误/预算耗尽 → AgentRunResult(CandidateResult)
  → L2 ResultAssembler → RESULT_VALIDATION → ResultValidator
  → TERMINATED → AnswerResult + TraceWriter

输入错误：RECEIVED → RESULT_VALIDATION → TERMINATED
快照失败：VALIDATED → RESULT_VALIDATION → TERMINATED
```

状态、Evidence ID 和错误契约以 `02-data-and-interface-contracts.md` 与 `08-review-resolution.md` 为准。

```text
输入 question/history
  → 输入校验
  → 初始化 AgentState（步骤预算、证据集、Trace）
  → DecisionModel 根据状态选择一个结构化 Action
  → ActionValidator 校验动作名、必需参数、预算和白名单
  → ToolRouter 执行工具
  → ToolResult 转为 Observation；保存 L6 已创建并去重的 ReadEvidence
  → 重新决策，或按状态终止
  → 结果校验（引用必须来自已读取 Evidence）
  → 输出 AnswerResult + Trace
```

工具结果是“观察”，不是自然语言最终答复。模型每次决策都必须能看见此前动作及观察摘要，以便不同工具结果产生不同后续动作。

## 7. 知识资料生命周期

首版流程：

```text
data/*.jsonl
  → loader 校验字段和 ID 唯一性
  → 建立当前运行的知识快照
  → CUDA embedding + tenant/snapshot Qdrant 索引与词项索引全部就绪后发布
  → SearchTool 在 tenant/snapshot 强制过滤下混合检索
  → ReadTool 按当前请求 candidate_id 授权解析 document/version/section 后读取
```

租户快照在一次请求期间保持不变，避免检索和读取看到不同版本。修改或删除资料后，构建完整新快照再发布；禁止使用其他租户或旧请求的 candidate/evidence。首版不做异步连接器、增量同步服务或跨请求缓存。摄取发布与撤权规则见 10 第 10 节。

## 8. 可替换点

| 当前设计 | 替换条件 | 稳定边界 |
|---|---|---|
| CLI | 明确需要服务接口 | `AskRequest` / `AnswerResult` |
| tenant-scoped 词项/向量混合检索 | 召回质量或数据规模需要演进 | `SearchDocuments` 输入/输出与租户约束 |
| Mock 模型 | 需要验证真实模型效果 | `DecisionModel.decide(state)` |
| JSONL 资料 | 数据规模/更新频率增加 | `KnowledgeRepository` |
| 内存 Trace | 需跨进程追踪或生产审计 | `TraceEvent` |

替换技术不应改变“证据携带来源、冲突不猜优先级、动作受限且可追踪”的核心规则。

## 9. 一致性约束

具体工具授权以 02/07/08 为准：Search 生成 `SearchCandidate(candidate_id)`；Read 仅接受同请求、同快照中的 `candidate_id`，由知识与证据层创建 `ReadEvidence`/`ConflictAssessment`。工具不得按模型给出的任意 document/version/section 读取。

1. 事实性答案的每个关键主张至少关联一条 Evidence；没有证据则不得给出确定事实答案。
2. 工具错误和无命中是不同结果，不可混为一谈。
3. 模型动作不能绕过工具白名单和参数校验。
4. Agent 达到预算后必须终止，不能为了生成完整文本越界继续调用。
5. 检索文本是非可信数据，不能改变系统策略或扩大工具权限。
6. 代码实现状态必须与文档描述分开记录。
7. 租户约束由可信上下文产生，贯穿索引、原文、candidate/evidence、Trace 和输出，模型动作不能指定 tenant_id。
8. FAQ 快答仍走 Search→Read→结果校验；快转深不重置预算；路由不是最终业务状态。

## 10. 当前必需能力的架构增补

向量数据库、意图识别、多租户隔离、浅层快答、深层查询及兜底全部属于当前必做范围，详细设计见 10，规范字段见 02 第 11 节。保留六层结构，不增设路由 Agent。

L2 建立可信身份并授权，L3 在 VALIDATED 阶段通过 L4 分类，纯非事实模板可直接生成候选；知识问题取得一次租户快照再进入 DECIDING。FAQ 目录匹配在绑定快照后完成，不能把 FAQ 指针当作证据。所有路径由 L2 统一校验。

L4 提供独立的 EmbeddingModel 协议；L5 取得查询向量后将数据交给 L6 混合检索。L5 不调用决策模型，L6 不调用模型 SDK。摄取由 L2 管理入口协调 L4 编码与 L6 存储，不开放给问答模型。

默认模型/工具预算仍是 6/4，模型分类计入前者。Mock/fixture 验证流程，真实向量效果需要 CUDA embedding 与实际 Qdrant 查询材料；当前均未实现验证。
