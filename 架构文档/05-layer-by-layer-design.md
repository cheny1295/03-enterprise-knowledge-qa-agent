# 05 逐层详细设计

本文将系统拆为六个业务层，并定义横切能力。每层都规定职责、输入输出、依赖边界、前置/后置条件、错误处理、可观测字段和验证点。实现可以将相邻层放在同一个 Python 模块中，但不得跨越下面的职责边界。

## 1. 层级总表

| 层 | 入口 | 输出 | 允许依赖 | 禁止承担 |
|---|---|---|---|---|
| L1 交互层 | CLI 参数/stdin + 可信启动身份 | `AskRequest`、展示文本/JSON、退出码 | 应用服务、序列化器 | 接受问题中的 tenant_id、模型决策、检索、冲突裁决 |
| L2 应用服务层 | `AskRequest` + trusted identity | `AnswerResult`、Trace | 身份解析、编排器、配置、结果校验 | 直接拼 prompt、直接读资料 |
| L3 Agent 编排层 | `AgentState`、路由/`DecisionModel`、`ToolRouter` | 状态更新、`AgentRunResult` 候选 | 模型接口、工具接口、证据服务 | 具体 SDK、向量存储细节 |
| L4 模型策略适配层 | `DecisionContext`、embedding 请求 | 结构化动作、意图结果、向量 | Mock/Provider/Embedding SDK | 执行工具、生成来源元数据 |
| L5 工具执行层 | 结构化工具动作 + 运行时租户上下文 | `ToolResult` | 知识仓库、向量查询、工具白名单 | 接受模型 tenant、生成最终答案、选择制度优先级 |
| L6 知识与证据层 | tenant 快照、混合查询、来源 ID | `SearchCandidate`、`ReadEvidence`、冲突分析 | 原文、词项索引、Qdrant | 调用模型、向用户追问 |

终态使用 02/08 的统一枚举。工具错误只属于 ToolResult/ToolError 命名空间；最终执行失败统一为 `execution_error`，所有候选终态经横切 `ResultValidator` 校验后进入生命周期终态 `TERMINATED`。

运行依赖为 `L1 → L2 → L3 → {L4, L5}`，`L5 → L6`；L2 负责身份解析、通过 L6 loader 创建/注入租户快照，L3 只可调用 L6 的证据/冲突服务，不可绕过工具检索/读取正文。L4 与 L5 并列，L5 通过 `EmbeddingModel` 协议取得向量但不调用决策模型；L4 不读原文。`AnswerResult`、`ResultValidator`、`TraceWriter`、配置和错误归一化是横切能力，不形成第七层。

## 2. L1 交互层：CLI

### 2.1 职责

- 接收单条问题、可选历史、运行模式、Trace 开关和预算配置。
- 接收可信启动身份的引用，不从问题正文切换租户。
- 将命令行输入转换为 `AskRequest`。
- 把输入级错误交给 L2 的统一退出流程，不启动决策循环。
- 将 `AnswerResult` 序列化为人可读文本或 JSON。
- 通过退出码区分输入错误、正常完成和任务未完成。

### 2.2 输入与输出

最终状态枚举以 02/08 为准；执行失败统一为 `execution_error`。结果校验、结果组装与 Trace 写入是横切能力。

```text
输入：
  question: string
  conversation_history?: Message[]
  mode?: mock | model
  max_steps?: integer
  max_tool_calls?: integer
  output_format?: text | json
  trace?: boolean

输出：
  stdout: AnswerResult
  trace file/stdout: TraceEvent[]
  exit code:
    0 = completed
    2 = invalid_request
    3 = needs_clarification / insufficient_evidence
    4 = execution_error / incomplete
    5 = invalid_model_action
```

### 2.3 前置与后置条件

- 前置：CLI 配置可解析，问题去除首尾空白后非空，历史消息结构合法。
- 后置：成功构造 `AskRequest` 后只调用一次应用服务；不在 CLI 内重复请求。
- 后置：应用服务返回后，原始状态和引用字段不被展示层修改。

### 2.4 错误处理

| 错误 | 行为 |
|---|---|
| 缺少问题 | 输出 `invalid_request`，不进入 Agent |
| JSON 历史格式错误 | 指出字段位置，不猜测修复 |
| 非法模式/负预算 | 输出参数错误 |
| 应用服务异常 | 输出安全错误摘要，详细信息仅本地诊断 |
| 结果状态非 `completed` | 仍输出答案、缺口、引用和状态，不能用非零退出码抹掉结果 |

### 2.5 Trace

CLI 记录 `request_id`、启动模式、参数摘要、退出状态和结果文件位置；不得记录密钥。问题文本是否完整记录由 `trace` 配置决定。

### 2.6 验证点

- 空问题不会调用模型/工具。
- `mock` 和 `model` 明确显示运行模式。
- JSON 输出可被程序重新解析。
- 冲突状态仍能显示双方引用。

## 3. L2 应用服务层：AskApplicationService

本层最终错误结果使用 `execution_error`；工具执行错误不得提升为新的最终状态。本文中的退出码 4 表示 `execution_error / incomplete`。

### 3.1 职责

1. 创建或接收入口预分配的唯一 `request_id/trace_id`，解析并校验 `TenantContext`。
2. 创建带租户和零计数的 AgentState，调用 L3 的路由准备，L3 负责规则/一次模型分类。
3. 对知识路径绑定一次租户快照并由 L3 检查 FAQ 资格；非事实/前置兜底无需知识。
4. 调用 `AgentOrchestrator.run(state)`。
5. 统一调用 ResultAssembler、ResultValidator 和输出记录器。
6. finish、ask_clarification、输入拒绝、快照错误及预算退出共用该出口，L3 不另行校验最终结果，避免重复组装。

### 3.2 接口

```text
ask(request: AskRequest) -> AnswerResult
```

服务层不得接受模型自由文本作为最终输出；编排器必须返回结构化结果或结构化失败。

### 3.3 生命周期

```text
create request_id
  → validate request and trusted identity
  → initialize state; L3 classify/route preparation
  → nonfactual/fallback candidate OR bind tenant snapshot and run knowledge loop
  → validate result-source binding
  → write trace
  → return result
```

一次知识请求只使用一个租户知识快照。加载完成后，查询期间资料文件/索引变化不得改变该请求的读取结果；无效身份不创建快照。

### 3.4 错误处理

- 知识加载错误：构造 `execution_error / knowledge_load_error` 候选并统一校验，不启动决策；snapshot_id 为 null。
- 配置缺失：mock 不需要 provider；model 缺少密钥则返回 `execution_error / provider_unavailable` 并校验，不自动降级。
- 编排器异常：转换为 `execution_error / internal_error`；只有实际预算耗尽或显式中断才用 incomplete，保留已有 Trace。
- 结果契约错误：ResultValidator 记录诊断码 `invalid_result`；若问题来自模型候选则最终为 `invalid_model_action`，若来源/内部状态损坏则最终为 `execution_error`，不得强行输出伪造的 completed。

### 3.5 验证点

- 一次请求只加载一次快照。
- Provider 不可用不会静默使用 Mock。
- Trace 与结果使用相同 `request_id`。
- 结果校验失败时不会丢失原始工具错误。

## 4. L3 Agent 编排层：AgentOrchestrator

### 4.1 职责

- 管理状态机和有限循环。
- 在 VALIDATED 阶段保存 IntentAssessment/RouteDecision；FAQ 不满足快答条件时将 route_changed 记为 deep，不重置预算。
- 调用 `DecisionModel` 获取动作。
- 校验动作并通过 `ToolRouter` 执行。
- 将所有 ToolResult（含 not_found/error）先转换为 Observation；ReadEvidence 和 ConflictAssessment 由 L6 生成，L3 只保存与传递。
- 处理预算、错误、冲突和终止。
- 组装候选结果，供横切 `ResultValidator` 校验。

### 4.2 接口

```text
run(initial_state: AgentState) -> AgentRunResult
```

### 4.3 单轮职责边界

编排器执行模型选择动作对应的状态转移，但不以自身启发式规则替模型挑选冲突制度。Evidence 创建、去重、来源回查和 ConflictAssessment 由 L6 独占；编排器只保存和传递。`AgentState.conflict_assessments → AgentRunResult.conflict_assessments → AnswerResult.conflict_assessments` 必须保留其 ID 和内容；未决冲突结果保留双边引用且不得 completed，正常业务出口为 needs_clarification，执行错误/预算出口保持真实停止原因。只有安全和契约规则可以在模型动作前后强制执行：

- 动作白名单；
- 参数和来源 ID 校验；
- 预算上限；
- 引用必须来自 Evidence；
- 未注册工具拒绝执行。

### 4.4 决策上下文构造

每次调用模型只提供：

- 当前问题和必要历史；
- 可用动作说明；
- 已执行动作及工具结果摘要；
- Evidence ID、来源标题、范围和原文片段；
- 剩余步数/工具调用数；
- 系统策略和安全边界。

检索文本必须标记为不可信资料，不能与系统指令混在同一语义层。

### 4.5 错误处理

| 阶段 | 错误 | 处理 |
|---|---|---|
| 决策前 | 状态损坏 | 构造 `execution_error` 候选并经 RESULT_VALIDATION |
| 模型返回 | JSON/动作无效 | 写 Trace，禁止执行工具；有限终止 |
| 路由 | 未注册工具 | `invalid_model_action` |
| 工具执行 | `ToolError` | 形成 Observation；不可恢复时为 `execution_error`，不得自动重放 |
| 结果组装 | Evidence 不存在 | 拒绝 `completed`，返回 `invalid_model_action` 或 `execution_error` |
| 循环 | 超过预算 | `incomplete`，保留已有证据 |

首版工具只读，因此不需要幂等写操作；仍然不允许无条件自动重试。

### 4.6 验证点

- 不同 Observation 能产生不同下一动作。
- 所有工具调用都能在 Trace 找到对应决策。
- 达到上限前不会执行越界工具。
- `execution_error` 不会伪装成 `no_evidence`。

## 5. L4 模型策略适配层：DecisionModel

### 5.1 职责

- 将 `DecisionContext` 变成 Mock 策略输入或 provider 请求。
- 将模型响应解析成结构化 `AgentAction`。
- 提供 `IntentClassifier` 和 `EmbeddingModel` 协议；embedding 使用固定 revision 和 CUDA 配置。
- 归一化 provider 不可用、超时、无效 JSON 等错误。

### 5.2 接口

```text
decide(context: DecisionContext) -> AgentAction | DecisionError
```

### 5.3 Mock 设计

Mock 必须基于当前状态和观察结果返回动作。例如：

```text
无观察 → search_documents
单一命中且需要原文 → read_document
发现未决冲突 → read_document / finish(needs_clarification)
空命中 → search_documents(改写 query) 或 finish(insufficient_evidence)
可恢复工具错误 → 新决策；不可恢复工具错误 → `execution_error` 候选并校验
```

Mock 不能直接返回预制完整 Trace；动作仍需经过编排器执行和记录。

### 5.4 真实模型设计

- 使用结构化输出约束动作联合类型。
- Provider 的密钥只从环境变量读取。
- 模型返回自然语言但无法解析为动作时，归类为 `provider_response_invalid`。
- 不把模型声称的引用元数据作为可信来源；只接受 Evidence ID。

### 5.5 验证点

- Mock 与真实模型共用同一 `DecisionModel` 协议。
- 无效动作不会进入工具层。
- 运行记录标注 model_mode、decision_source 和 provider/model 或 policy/fixture ID；通过 input_observation_ids 回查动作依据。
- Prompt 中恶意资料不会改变工具白名单。

## 6. L5 工具执行层：ToolRouter

### 6.1 职责

- 维护工具白名单。
- 将运行时 TenantContext 和 snapshot 约束强制注入 Search/Read，模型不能提供 tenant/collection/path。
- 根据动作类型解析参数。
- 检查工具前置条件。
- 执行具体工具并归一化返回。
- 为编排器提供统一 `ToolResult`。

### 6.2 路由规则

```text
search_documents → SearchDocumentsTool
read_document    → ReadDocumentTool
ask_clarification/finish → 不进入工具层
其他 type        → invalid_model_action
```

### 6.3 通用工具前置条件

- 工具名称来自静态注册表。
- 参数通过 schema 校验。
- 当前请求存在有效知识快照。
- 工具调用次数未达到上限。
- 工具不访问未声明的外部资源。

### 6.4 验证点

- 未注册工具不可执行。
- 参数错误与执行异常可区分。
- 每次执行具有唯一 tool call 记录。
- 工具返回不会直接成为最终答案，必须经过 Observation/Evidence 处理。

## 7. L6 知识与证据层

### 7.1 知识仓库

负责加载、校验和提供当前租户 ACTIVE 快照中的文档版本、FAQ 来源组、词项索引和 Qdrant 派生索引。它不调用模型，不决定用户最终答复。

```text
load_snapshot(tenant_context) -> KnowledgeSnapshot
get_document(document_id, version_id) -> Document | NotFound
list_sections(document_id, version_id) -> Section[]
```

load_snapshot 的规范契约见 02 第 11.3 节；存储根目录由应用配置固定，后两项方法绑定同一租户快照实例，不能通过 document_id 跨租户读取。

校验规则：ID 在租户内唯一、版本存在、日期格式正确、section 有原文、`supersedes` 指向存在来源或被明确标记为外部关系。

### 7.2 检索器

负责规范化词项、接收 L5 已获取的查询向量、执行 tenant/snapshot 强制过滤和混合排序；L6 不生成向量或调用模型。分数只表示候选相关性，不表示制度权威性。

### 7.3 Evidence 服务

- Search 产生 `SearchCandidate`，Read 成功后由 EvidenceService 将 ReadResult 转为 `ReadEvidence`。
- 首版读取完整 section，在单次请求内按 `tenant_id + snapshot_id + document_id + version_id + section_id` 去重；不跨请求或跨租户复用证据。
- 检查 quote 是否来自当前快照原文。
- `ConflictAnalyzer` 根据已读取 Evidence 和显式元数据生成 `ConflictAssessment`，不擅自创建优先级。

### 7.4 验证点

- 删除/替换资料后，新请求不读取旧快照。
- 引用 quote 能回到稳定 section。
- 文档日期不自动被解释为优先级。
- 同一冲突双方都能保留为独立 Evidence。

## 8. 横切结果与记录能力

### 8.1 ResultAssembler

负责将候选结果、状态、答案主张、可信 ReadEvidence、ConflictAssessment、缺口和终止原因组装成 `AnswerResult`。未决冲突时必须携带包含双方 candidate/evidence IDs 的 assessments。不负责重新回答问题，不修正文模型主张。

### 8.2 ResultValidator

至少检查：

- 状态枚举合法，且 `status`、`error_code`、`stop_reason` 不混用；
- completed 的事实主张均有 Evidence ID；
- Citation 的来源、版本、section 和 quote 可回查；
- needs_clarification 有非空 gaps 和 follow_up_question；未决冲突还要求 assessment 和双方完整引用；
- incomplete 有 `stop_reason=budget_exhausted` 或中断原因；
- execution_error 有 `error_code`；
- `ask_clarification` 与 `finish` 均经过同一套校验。

### 8.3 TraceWriter

按事件写 JSONL；同一决策步骤允许多个事件。写入失败不能修改已经生成的业务 status，只在 `AnswerResult.warnings` 加入 trace_write_failed 并在 stderr 输出安全提示。trace_id 是关联 ID，不宣称文件一定存在。

### 8.4 验证点

- 结果和 Trace 可用同一 request ID 关联。
- 不能输出不存在的引用。
- 不输出 chain-of-thought、密钥或本机敏感路径。
- 非 completed 状态仍保留已获得的有效证据。

## 9. 三路、模型子接口与管理入口细化

| 接口 | 输入 | 输出/后置条件 | 失败和观测 |
|---|---|---|---|
| L2 resolve_identity | 可信启动配置/将来认证中间件 | TenantContext；无效则不启动知识访问 | access_denied；不披露外租户 |
| L3 prepare_route | State、IntentClassifier、非事实模板策略 | IntentAssessment；知识请求待快照绑定再判 FAQ | 分类计全局预算；Trace 来源明确 |
| L4 classify | question/必要历史/固定标签前缀 | 标签、深度、缺失条件，不含答案 | provider/协议错误如实映射 |
| L4 embed_query/embed_documents | 固定 revision/指令/文本 | CUDA 归一化向量和模型元数据 | 不可用为 embedding_unavailable；不静默 CPU |
| L5 Search/Read | 动作 + TenantContext/snapshot | 租户内 Candidate/ReadEvidence | 成功/失败都 Observation |
| L6 hybrid_search | 已编码 query + 强制约束 | section 聚合、RRF rank、coverage | vector_store_error/index_version_mismatch |
| L2 ingest/publish/delete | editor 上下文与 source package | 完整新快照发布或保留旧 ACTIVE | 不能由问答模型调用 |

每层共同验证点：两个合成租户同 document_id 不串读；事实快答不绕过 Read；模型无权修改 tenant/路径/collection；快转深不重置计数；结果/Trace 导出不越过当前身份域。存储发布状态不是 Agent 状态。详细字段以 02 第 11 节为准，配置和发布步骤见 10。
