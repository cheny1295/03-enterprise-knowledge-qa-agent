# 02 数据模型与接口契约

本文定义层间传递的最小稳定对象。实现可使用 Python dataclass、TypedDict 或 Pydantic，但语义和字段约束保持一致；除非真实模型结构化输出需要，不增加运行时依赖。

## 1. 标识和时间约定

- `request_id`：一次问答唯一标识。
- `tenant_id`：可信身份绑定的租户标识；文档 ID 仅在租户内唯一，来源身份必须带租户。
- `candidate_id`：Search 在一次请求内创建的候选 ID，不是引用 ID。
- `document_id`：资料稳定 ID；更新版本仍沿用逻辑 ID，版本由 `version_date`/`version_id` 区分。
- `evidence_id`：一次请求内来源片段的稳定引用 ID，由程序生成。
- 日期统一使用 ISO 8601 日期字符串 `YYYY-MM-DD`。
- 片段位置使用 `section_id` 或字符区间；首版至少提供可读章节 ID，保证原文可重复定位。

## 2. 输入请求

```json
{
  "question": "报销申请需要在多久内提交？",
  "conversation_history": [
    {"role": "user", "content": "我问的是差旅报销。"},
    {"role": "assistant", "content": "我还没有确认具体适用制度。"}
  ],
  "mode": "mock",
  "trace": true,
  "filters": {"scope_any": ["差旅报销"]},
  "historical": false
}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `question` | string | 必填，去除首尾空白后不可为空 |
| `conversation_history` | message[] | 可选；只接受 `user`/`assistant`，限制条数/长度 |
| `mode` | enum | `mock` 或 `model`；默认配置必须明确 |
| `trace` | bool | CLI 默认生成 Trace，展示可由参数控制 |
| `filters` | object | 可选；用户已明确确认的 scope/document/date/status 条件，字段按 Search 契约校验 |
| `historical` | bool | 可选；包含 withdrawn 时必须为 true，且 filters 同时提供 effective_on |

对话历史用于补充指代、对象和已确认条件，不得作为制度事实的证据。历史中的旧答案不替代当前资料检索。

当前实现只从显式 `filters` 生成 `confirmed_filters`，不将模型或自然语言猜测升级为排除来源的约束。未提供 status 时默认 active；L2 以 `filters_confirmed` 记录各字段来自 `request.filters` 或 `application.default`。

## 3. 知识文档

建议 JSONL 一行一份逻辑文档版本：

```json
{
  "tenant_id": "tenant-A",
  "document_id": "D01",
  "version_id": "D01-v1",
  "version_date": "2026-03-01",
  "effective_from": "2026-03-01",
  "effective_to": null,
  "title": "费用报销管理办法",
  "scope": ["差旅报销"],
  "faq_topic_ids": ["reimbursement-deadline"],
  "status": "active",
  "supersedes": [],
  "priority": null,
  "sections": [
    {
      "section_id": "4.2",
      "heading": "提交期限",
      "text": "员工应在费用发生后30天内提交报销申请。"
    }
  ],
  "keywords": ["报销", "期限", "提交"]
}
```

### 字段约束

- `tenant_id` 必填且与 editor 上下文一致；`document_id` 在租户内必填，版本 ID 在租户快照内唯一。
- `version_date` 是资料日期，不自动等于权威优先级。
- `effective_from`、`effective_to` 是可空 ISO 日期，表示资料明确声明的生效区间，起止日均包含；缺失时不推导有效或无效。
- `scope` 显式表示适用对象/业务范围；为空表示资料未声明范围。
- `status` 只允许已知状态，如 `active`、`withdrawn`；未知值作为资料校验错误。
- `supersedes: string[]` 仅填写文档明示被替代的 `version_id`；同一逻辑文档的新日期不自动构成替代关系。
- `priority: integer | null` 仅填写资料明确声明且在相同范围内可比较的等级，数值越大等级越高；未声明或比较范围不一致时不做等级裁决，不根据日期、文件名或检索分数生成。
- `sections[].text` 保留原文，不在 loader 中改写；规范化文本只用于检索，不用于引用。
- 资料格式错误应在启动/加载时失败并指出行号、文档 ID 和字段，不得静默丢弃。

## 4. Agent 状态

```text
AgentState {
  request_id: string
  tenant_context: TenantContext
  intent_assessment: IntentAssessment | null
  routing: RouteDecision | null
  embedding_calls_used: integer
  question: string
  conversation_history: Message[]
  normalized_question: string
  observations: Observation[]
  evidence: ReadEvidence[]
  conflict_assessments: ConflictAssessment[]
  pending_gaps: string[]
  step_index: integer
  model_steps_used: integer
  tool_calls_used: integer
  max_steps: integer
  max_tool_calls: integer
  lifecycle_state: LifecycleState
  snapshot_id: string | null
}
```

`lifecycle_state` 只允许 `RECEIVED`、`VALIDATED`、`DECIDING`、`OBSERVING`、`RESULT_VALIDATION`、`TERMINATED`。最终业务状态只写入 `AnswerResult.status`，二者不共用字段。状态只在一次请求内存在；不把任意模型输出直接写入持久知识。

应用服务在解析输入前分配一次 `request_id` 与 `trace_id`，记录最小生命周期信封；AgentState 在输入与身份合法后创建，在 VALIDATED 阶段分类并绑定知识快照。非事实模板及前置拒绝可 `snapshot_id=null`；进入 DECIDING 及调用工具后该字段必须非空。

每次知识请求的 `KnowledgeSnapshot` 在 L2 完成身份/路由准备后创建一次并以只读依赖注入 Search、Read、EvidenceService。其 manifest 包含 `tenant_id`、`snapshot_id`、`dataset_version`、`index_version`、embedding 配置、发布状态和 `document_versions[]`。SearchCandidate、ReadEvidence、ToolResult、AnswerResult 和 TraceEvent 都携带同一 `tenant_id + snapshot_id`；候选和证据 ID 不得跨请求或跨租户复用。非事实模板可不创建 snapshot。

## 5. 模型动作接口

### 5.1 DecisionModel

```text
decide(state: DecisionContext) -> AgentAction
```

`DecisionContext` 至少包含当前问题、必要的对话历史、可用动作/参数说明、此前动作和观察摘要、已有 Evidence ID、剩余预算。不得将无关文档或任意文件内容默认塞进上下文。

### 5.2 动作联合类型

```json
{"type":"search_documents","query":"报销申请期限","filters":{"scope_any":["差旅报销"]}}
```

```json
{"type":"read_document","candidate_id":"C1"}
```

```json
{"type":"ask_clarification","question":"请确认你询问的是差旅报销还是其他类型的报销？","gap":"适用范围未明确"}
```

```json
{"type":"finish","status":"completed","answer":"...","claims":[{"text":"...","evidence_ids":["E1"]}],"gaps":[],"follow_up_question":null}
```

允许动作仅有：

- `search_documents`：查询本地知识快照。
- `read_document`：必须以当前请求 Search 生成的 `candidate_id` 授权读取。
- `ask_clarification`：生成澄清候选结果；不直接终止，必须经过统一结果校验。
- `finish`：生成包含状态、答案、主张、缺口和追问字段的候选结果；必须经过统一结果校验。

模型不得直接提交引用原文、文档版本和定位字段；这些元数据由 Evidence 仓库根据 `evidence_ids` 补齐。动作结构不合法时归类为 `invalid_model_action`，本版不做解析修复重试，不执行近似猜测的工具调用。`finish.status` 允许 `completed | needs_clarification | insufficient_evidence | execution_error`；模型主动因可恢复错误放弃时可提出 execution_error，应用层从真实 Observation 补齐错误码。预算、输入错误和模型协议错误由运行时产生，不由模型自报。

## 6. 工具接口

### 6.1 SearchDocuments

```text
search_documents(
  query: string,
  filters?: {
    scope_any?: string[],
    scope_all?: string[],
    document_ids?: string[],
    status?: ("active" | "withdrawn")[],
    version_date_from?: date,
    version_date_to?: date,
    effective_on?: date
  },
  limit?: integer
) -> ToolResult
```

```text
SearchResult {
  status: "ok" | "no_evidence"
  candidates: SearchCandidate[]
  coverage: { candidate_truncated: boolean, topic_complete: boolean }
  retrieval_mode: "hybrid" | "lexical_fixture" | "vector_fixture"
  embedding_revision: string
  snapshot_id: string
}

SearchCandidate {
  candidate_id: string
  request_id: string
  document_id: string
  version_id: string
  version_date: date
  effective_from?: date | null
  effective_to?: date | null
  title: string
  scope: string[]
  status: "active" | "withdrawn"
  supersedes: string[]
  priority: integer | null
  section_id: string
  snippet: string
  lexical_score: number | null
  tenant_id: string
  vector_score: number | null
  vector_rank: integer | null
  lexical_rank: integer | null
  retrieval_score: number
  retrieval_mode: "hybrid" | "lexical_fixture" | "vector_fixture"
  retrieval_query: string
  retrieval_rank: integer
  snapshot_id: string
}
```

`scope_any` 表示与候选 `scope` 至少有一个交集，`scope_all` 表示过滤列表中的每个值均包含于候选 `scope`；两者同时提供时取 AND；值按去首尾空白后的精确字符串匹配，不作同义词猜测。空过滤列表按未提供处理；空候选 scope 不匹配显式 scope filter。`effective_on` 只匹配两端均明确声明且包含该日期的区间；缺失端点的文档保留在未加该过滤的候选中，不能自动判为无效。应用服务默认施加 `status=["active"]`；用户明确询问历史规定且确认参考日期时，才可生成包含 withdrawn 的过滤。`version_date` 过滤不是有效期过滤，两者不得替代。

过滤器分为不可放宽的应用约束与动作提出的限制条件。L2 从用户明确条件/固定配置生成应用约束并记录来源，L5 执行前取交集；未经用户确认的 scope/date 条件只可作为检索建议，不能用于排除冲突来源或最终适用性裁决。metadata 中的 scope/status 只能来自 loader，不从标题推测；不允许模型以命中一份资料为由推定其他范围不存在。

`no_evidence` 是成功执行但无候选；加载错误、检索异常或参数错误必须使用错误结果，不能返回空命中冒充成功。排序分值只用于候选排序，不代表事实可信度、制度优先级或回答置信度。

filters 未提供时仍应用 L2 的默认约束；limit 未提供时由 L2 的固定检索配置补齐（首版默认 5），不让模型决定上限。未知字段一律拒绝。SearchCandidate 注册在请求级知识/证据服务中，Observation 保存其 ID，后续 Read 和 DecisionContext 都通过该注册表回查，不依赖模型重述来源元数据。

### 6.1.1 统一工具返回与观察

```text
ToolResult {
  tool_name: string
  tool_call_id: string
  status: "ok" | "no_evidence" | "not_found" | "error"
  data: object
  evidence: ReadEvidence[]       // Search 固定为空数组
  error?: ToolError              // status=error 时必填
  metadata: { tenant_id: string, request_id: string, snapshot_id: string, elapsed_ms: integer }
}

Observation {
  observation_id: string
  tenant_id: string
  request_id: string
  tool_call_id: string
  tool_name: string
  status: ToolResult.status
  summary: string
  candidate_ids: string[]
  evidence_ids: string[]
  error_code?: string
  snapshot_id: string
}
```

L3 将每个 ToolResult 转换为 Observation 后追加到 AgentState；不论成功、空命中、not_found 或 error，均先登记。request/snapshot 不匹配属于来源完整性错误。Observation 的 summary 是展示摘要，不是下轮决策的唯一输入；L3 按 ID 将当前请求的 Candidate、ReadEvidence、ToolError（含 retryable）和 ConflictAssessment 投影到 DecisionContext。

### 6.2 ReadDocument

```text
read_document(candidate_id: string) -> ToolResult
```

`candidate_id` 是唯一授权输入，且必须属于当前请求的 Search Observation 和当前 `snapshot_id`。工具根据候选记录解析文档、版本和片段；不得接受模型指定的 `document_id`、`version_id`、`section_id` 来扩大或替代授权范围。来源不存在返回 `not_found`，不返回近似版本代替。

```text
ReadResult {
  status: "ok" | "not_found"
  candidate_id: string
  source?: { document_id, version_id, section_id, heading, text, metadata }
  snapshot_id: string
}
```

`SearchResult` 和 `ReadResult` 是 L6 的内部返回；不是与 ToolResult 并存的第二套模型接口。映射固定为：

- SearchResult → `ToolResult.status=result.status`、`data.candidates/coverage/retrieval_mode/embedding_revision` 按同名字段映射、`evidence=[]`。
- ReadResult(ok) → EvidenceService 根据 source 原文创建 ReadEvidence → `ToolResult.status=ok`、`data` 为来源/原文、`evidence=[ReadEvidence]`。
- ReadResult(not_found) → `ToolResult.status=not_found`、`data.candidate_id` 保留、`evidence=[]`，不附 ToolError。
- 内部异常 → `ToolResult.status=error`、`data={}`、`evidence=[]`、`error=ToolError`。
- ToolResult → L3 Observation → AgentState → 下一轮 DecisionContext。成功引用证据只在 L6 创建，不在转换 Observation 时创建。

## 7. Evidence、主张与引用

```text
ReadEvidence {
  evidence_id
  request_id
  source_candidate_id
  tenant_id
  document_id
  version_id
  version_date
  effective_from?
  effective_to?
  title
  scope[]
  status
  supersedes[]
  priority
  section_id
  quote
  snapshot_id
}

ConflictAssessment {
  assessment_id
  tenant_id
  request_id
  snapshot_id
  candidate_ids[]
  evidence_ids[]
  relation
  basis_fields[]
  unresolved_gaps[]
}

Claim {
  text
  evidence_ids[]
}

Citation {
  evidence_id
  document_id
  version_id
  version_date
  section_id
  quote
}
```

SearchCandidate 以第 6/11 节的统一定义为准；`Evidence` 若出现在说明性文字中，均指 ReadEvidence。首版每次读取完整 section，`quote=sections[].text`；因此去重键为当前请求内的 `tenant_id + snapshot_id + document_id + version_id + section_id`，不引入未定义的 `quote_span`。

重复读取相同 section 可复用已创建的 evidence_id；source_candidate_id 保留首次创建时的授权候选，服务同时保存后续 candidate→evidence 的映射。它们必须指向完全相同的来源身份，不能把不同版本、不同 section 或不同请求合并。

- Search 只产生 `SearchCandidate`；snippet 只用于决定是否读取，不作为最终 Citation。
- Read 成功后由 EvidenceService 创建 `ReadEvidence`；只有其 `evidence_id` 可进入最终 Claim。
- EvidenceService 是证据转换、去重、来源回查与快照一致性校验的唯一责任方。
- 不同来源版本不得去重合并。冲突比较由同层 `ConflictAnalyzer` 根据已读取 Evidence 和显式元数据生成 `ConflictAssessment`，编排层只传递结果。
- `Claim.evidence_ids` 仅引用当前请求中存在的 `ReadEvidence`。
- 输出层从 ReadEvidence 展开 Citation，不接受模型自填引用元数据。
- `completed` 的事实主张不能是空证据引用；纯澄清/拒答不需要虚构 Citation。
- 程序可确定性检查来源存在、版本匹配、原文一致；“语义上足以推出主张”需人工/语义评估，不能由字符串校验冒充。

## 8. 最终结果

L3 返回的不是已经校验的最终答案，而是运行结果：

```text
CandidateResult {
  status
  answer_kind: "nonfactual" | "factual" | "clarification" | "fallback"
  answer: string
  claims: Claim[]
  gaps: string[]
  follow_up_question?: string
  error_code?: string
  stop_reason
}

AgentRunResult {
  request_id
  tenant_id
  routing: RouteDecision
  snapshot_id
  candidate_result: CandidateResult
  evidence: ReadEvidence[]
  conflict_assessments: ConflictAssessment[]
  observations: Observation[]
  model_steps_used
  tool_calls_used
  embedding_calls_used
}
```

L2 只调用一次 ResultAssembler/ResultValidator 完成对外退出；文档中的运行结果类型统一称 `AgentRunResult`，不另设 `RunResult`。`AnswerResult.conflict_assessments`、`warnings` 始终为数组，无内容时为空。Trace 写盘失败只在 warnings 中记录 `trace_write_failed`，不修改业务 status；`trace_id` 仍是关联 ID，不保证对应持久文件存在。

模型的 finish/ask 先映射为 CandidateResult；stop_reason、request/snapshot/trace ID、引用元数据及 conflict_assessments 由运行时和可信证据服务补齐，模型不得自填。ask 的 question→follow_up_question、gap→gaps，并设置 needs_clarification。正常澄清没有事实性 claims 时为空数组，已有未决冲突仍按 assessment 展开引用。

```json
{
  "request_id": "req-...",
  "tenant_id": "tenant-A",
  "answer_kind": "clarification",
  "routing": {"route":"deep_query","reason":"资料冲突","faq_topic_id":null,"attempted_route":"deep_query","effective_route":"fallback"},
  "status": "needs_clarification",
  "claims": [],
  "answer": "资料存在冲突，当前无法确认唯一适用期限。",
  "citations": [
    {"evidence_id":"E1","document_id":"D01","version_id":"D01-v1","version_date":"2026-03-01","section_id":"4.2","quote":"员工应在费用发生后30天内提交报销申请。"},
    {"evidence_id":"E2","document_id":"D02","version_id":"D02-v1","version_date":"2026-05-01","section_id":"2.1","quote":"员工应在费用发生后15天内提交报销申请。"}
  ],
  "gaps": ["缺少适用范围或制度优先级"],
  "conflict_assessments": [
    {"assessment_id":"CA1","tenant_id":"tenant-A","request_id":"req-...","snapshot_id":"snap-...","relation":"unresolved_conflict","candidate_ids":["C1","C2"],"evidence_ids":["E1","E2"],"basis_fields":["scope","priority"],"unresolved_gaps":["适用范围或制度优先级未声明"]}
  ],
  "follow_up_question": "请确认适用的报销类型或制度版本。",
  "error_code": null,
  "stop_reason": "clarification_required",
  "snapshot_id": "snap-...",
  "trace_id": "req-...",
  "warnings": []
}
```

状态枚举：

| 状态 | 语义 |
|---|---|
| `completed` | 事实答案有充分无未解冲突的证据；或为非事实白名单模板 |
| `needs_clarification` | 缺少可由用户补充的范围/条件，或有未决冲突 |
| `insufficient_evidence` | 正常执行但资料不能支持答案，且暂无可有效追问的条件 |
| `execution_error` | 必要工具、provider 或内部执行失败，任务不能正常完成 |
| `incomplete` | 达到预算上限或被明确中断，结果未完成 |
| `invalid_request` | 输入契约无效，未启动 Agent |
| `invalid_model_action` | 模型动作不符合结构/白名单/来源授权契约 |

`status`、`error_code`、`stop_reason` 语义分离。`error_code` 可空，只承载机器可读诊断码；`stop_reason` 必填，取 `finished_with_evidence`、`finished_nonfactual`、`clarification_required`、`no_supporting_evidence`、`recoverable_error_exhausted`、`execution_failed`、`budget_exhausted`、`request_rejected`、`action_rejected` 或 `interrupted`。发生未决冲突时，`conflict_assessments` 必填并覆盖冲突双方的 candidate/evidence；授权撤销时仅输出无正文错误信封。

最终状态与停止原因的映射：

| 触发 | 最终 status | error_code | stop_reason |
|---|---|---|---|
| 证据充分、无未决冲突 | completed | null | finished_with_evidence |
| 非事实白名单模板 | completed | null | finished_nonfactual |
| 条件缺失/正常结束时存在未决冲突 | needs_clarification | null | clarification_required |
| 正常执行后资料不足 | insufficient_evidence | null | no_supporting_evidence |
| 不可恢复工具/模型/加载错误 | execution_error | 真实执行诊断码 | execution_failed |
| 可恢复错误后明确放弃且无成功恢复 | execution_error | 最近相关执行诊断码 | recoverable_error_exhausted |
| not_found 后无法恢复且明确放弃 | execution_error | source_unavailable（最终诊断，不是 ToolError.code） | recoverable_error_exhausted |
| 步数/工具预算耗尽 | incomplete | null | budget_exhausted |
| 显式外部中断 | incomplete | null | interrupted |
| 输入无效 | invalid_request | invalid_arguments | request_rejected |
| 可信身份缺失/无成员资格 | invalid_request | access_denied | request_rejected |
| 执行期授权撤销 | execution_error | access_denied | execution_failed |
| 模型动作/候选违反契约 | invalid_model_action | invalid_arguments / provider_response_invalid / invalid_result | action_rejected |
| 内部结果/来源损坏 | execution_error | invalid_result / internal_error | execution_failed |

优先表达使任务停止的真实原因：输入/动作拒绝、不可恢复执行错误、预算/中断优先于业务候选；不能为了输出 needs_clarification 掩盖执行失败。若运行已正常走到业务 finish/ask 且有未决冲突，则必须 needs_clarification；异常/超限结果仍携带已有冲突 assessments 和双边引用，但不得作确定制度裁决。可恢复错误后若成功读取足够证据，允许正常完成，Trace 保留之前的失败；不能把未恢复错误当成资料不足。

## 9. Trace 契约

一条 JSONL 记录对应一个外部事件；一次决策步骤可包含 decision、tool_result、conflict_assessed 等多个事件，不预写 next_action：

```text
TraceEvent {
  event_id
  event_type
  request_id
  snapshot_id?
  step_index
  timestamp
  action_type?
  action_arguments?
  decision_basis_summary?
  decision_source: "model" | "mock_policy" | "runtime"
  model_mode
  adapter_id?
  policy_id?
  fixture_id?
  tool_name?
  tool_call_id?
  tool_status?
  result_summary?
  observation_id?
  observation_status?
  input_observation_ids[]
  candidate_ids[]
  conflict_assessment_ids[]
  evidence_ids[]
  next_action?
  remaining_steps
  remaining_tool_calls
  stop_reason?
}
```

Trace 只记录能复核的外部事实和简短依据摘要，不记录 hidden chain-of-thought。密钥不得进入 Trace；真实用户数据模式下需最小化问题和 quote 的持久化。

`decision` 事件通过 `input_observation_ids` 关联已知观察；`tool_result` 事件通过 `observation_id/tool_call_id` 关联结果；`conflict_assessed` 事件关联 assessment ID。Mock 的 decision_source 为 mock_policy，且 policy_id 或 fixture_id 至少有一个；运行时停止事件用 runtime，不冒充模型决定。provider 传输/超时失败没有后续模型动作，记录 runtime 终止而非虚构 next_action。

## 10. 错误对象

```text
ToolError {
  code: "invalid_arguments" | "knowledge_load_error" |
        "timeout" | "provider_unavailable" | "provider_response_invalid" |
        "internal_error" | "access_denied" | "embedding_unavailable" |
        "vector_store_error" | "index_version_mismatch"
  message: safe_user_message
  retryable: boolean
}
```

`not_found` 是 ToolResult/Observation 状态，不是 ToolError.code。`invalid_result` 是 ResultValidator 的校验诊断码，不是 AnswerResult.status。业务错误与基础设施异常分离。工具不得自动重试或重放；可恢复错误形成 Observation 后，模型可在预算内选择新的动作。`not_found` 只描述本次 candidate 读取未找到，不代表知识库全局无命中。Candidate 和 ReadEvidence 绑定 `request_id + snapshot_id`；跨请求使用统一返回 `not_found`，不泄露其他请求是否存在该 ID。工具失败若不可恢复，最终状态为 `execution_error`。

错误码字面值可以被不同适配器共享，但对象不能混用：L4 返回 `DecisionError(code, message, retryable)`，L5 返回 ToolResult.error。Provider 不可达/超时按 execution_error；响应到达但 JSON/动作不符合协议按 invalid_model_action + provider_response_invalid。本版不自动重试模型请求，不降级 Mock。Read 的未知 candidate 字符串属于合法形状，交由工具返回 not_found；只有字段缺失、类型不对或额外来源参数属于动作校验拒绝。Claim 引用旧 evidence ID 则在结果校验中被拒绝为 invalid_model_action，而不是把最终答案伪装成工具 not_found。

## 11. 路由、租户和向量契约

### 11.1 TenantContext

```text
TenantContext {
  principal_id: string
  tenant_id: string
  roles: ("knowledge_reader" | "knowledge_editor")[]
  authz_version: string
  source: "trusted_process_config" | "trusted_http_middleware"
}
```

`TenantContext` 只能由 L2 的可信身份入口创建；`AskRequest`、conversation history、AgentAction、Search filters 均不得携带可改变当前身份的 tenant_id。问题中出现的租户名只能作为普通文本，不能覆盖 TenantContext。缺失或无效身份在知识快照创建前形成 `invalid_request`，`error_code=access_denied`、`stop_reason=request_rejected`，不泄露其他租户数据。

### 11.2 RouteDecision 与 IntentAssessment

```text
IntentAssessment {
  assessment_id: string
  intent: greeting | capability | knowledge_question | out_of_scope | unknown
  depth: shallow | deep | uncertain
  confidence?: number
  missing_conditions: string[]
  classifier_id?: string
  decision_source: "rule" | "model" | "mock_policy"
}

RouteDecision {
  route: "fast_nonfactual" | "fast_faq" | "deep_query" | "fallback"
  reason: string
  faq_topic_id: string | null
  attempted_route: "fast_nonfactual" | "fast_faq" | "deep_query" | "fallback"
  effective_route: "fast_nonfactual" | "fast_faq" | "deep_query" | "fallback"
}
```

路由分类不是 `AgentState.lifecycle_state`，也不是 `AnswerResult.status`。`confidence` 只做观测，不直接越过证据校验；`unknown/uncertain` 不得直接生成制度事实。`fast_nonfactual` 可在无 snapshot 时完成固定模板，制度事实的 `fast_faq` 仍需 Search→Read→Finish。快转深沿用同一 request、tenant、snapshot、预算和 Trace，不重新开始任务。

route 保留最初选定路径，attempted_route 记录最近实际尝试的路径，effective_route 表示当前/最终路径；快转深把后两者置 deep_query，深查失败兜底则 attempted_route 保持 deep_query、effective_route=fallback。无效输入尚未分类时 routing=null，不伪造分类结果。规则来源映射到 Trace.decision_source=runtime，分类模型来源为 model，Mock 为 mock_policy。分类不是 AgentAction，不扩展动作白名单。

L2 给 AnswerResult 增加 `tenant_id: string | null`、`answer_kind` 和 `routing: RouteDecision | null`，不接受模型自填租户/路由。有效身份的结果 tenant_id 来自 TenantContext；身份拒绝则为 null。answer_kind 由受信任路径及结果组装确定，模型不能用 nonfactual 标签规避事实引用校验。非事实模板 stop_reason=finished_nonfactual 已加入第 8 节映射；其他 completed 必须 finished_with_evidence。前置问题澄清为 clarification，错误/不足兜底为 fallback，已完成制度答案为 factual。

RouteContext 为 question + 受限历史 + 标签/策略版本；不含可写 tenant/source 字段。IntentAssessment.assessment_id 由运行时分配，用于关联 Trace，不接受分类模型自填。分类调用计入全局 model_steps_used；query embedding 另计 embedding_calls_used，每个 Search 至多一次。二者的真实调用数均由运行时写入 Trace/AgentRunResult。

### 11.3 KnowledgeChunk 与索引清单

```text
KnowledgeChunk {
  point_id: string
  tenant_id: string
  snapshot_id: string
  document_id: string
  version_id: string
  section_id: string
  chunk_id: string
  char_start: integer
  char_end: integer
  text_for_embedding: string
  faq_topic_ids: string[]
  embedding_model_id: string
  embedding_revision: string
  vector_dimension: integer
  index_version: string
  scope: string[]
  status: "active" | "withdrawn"
}

FaqEntry {
  tenant_id: string
  snapshot_id: string
  faq_topic_id: string
  canonical_question: string
  aliases: string[]
  required_conditions: string[]
  source_refs: { document_id, version_id, section_id }[]
  topic_complete: boolean
}

KnowledgeSnapshot {
  snapshot_id: string
  tenant_id: string
  dataset_version: string
  index_version: string
  embedding_model_id: string
  embedding_revision: string
  vector_dimension: integer
  publication_state: "BUILDING" | "READY" | "ACTIVE" | "RETIRED" | "FAILED"
  document_versions: string[]
}
```

L2 在知识路径完成身份与路由准备后调用 `load_snapshot(tenant_context) -> KnowledgeSnapshot`，仅绑定该可信租户的当前 ACTIVE manifest。存储根目录来自固定应用配置，不是模型参数；后续文档读取方法均绑定返回的租户快照实例，不接受调用方重新指定租户或路径。请求绑定后不修改 manifest；普通更新后在途请求可继续使用已绑定旧版本，撤权/隐私删除则按 10 的流程阻断。

Point payload 不承担引用原文；最终 quote 必须从同一 tenant/snapshot 的原文 section 回查。向量配置、维度、切片规则或查询前缀变化必须形成新的 `index_version` 并重建，禁止混写不同 revision。Qdrant collection 名、point_id、tenant_id、snapshot_id、过滤条件由受信任代码生成，模型不得指定。

char_start/char_end 为 Python 原文 Unicode 字符下标，采用 [start,end)；不使用 UTF-8 byte offset。manifest 另包含 embedding 查询前缀/normalize/distance、chunk 配置版本、tenant source_refs 及 FAQ 来源清单，均由发布者生成。索引维度/revision 不一致返回 index_version_mismatch。所有模型权重 revision 与依赖版本在首次实现时锁定，当前不能伪造具体已验证 commit。

### 11.4 Search/Read 增补

文档级 faq_topic_ids 为编辑显式标注的 FAQ 主题，默认空；默认作用于全部 section，sections[] 可显式声明列表覆盖文档缺省。FaqEntry.source_refs 必须覆盖该 tenant/snapshot 中全部标注该 topic 的 section/version，不能排除冲突版本。topic_complete 仅在发布校验通过且编辑声明来源完整时置 true；未编目资料不自动获得“全库无冲突”保证，仍参与普通混合召回。

向量 payload 日期采用距 1970-01-01 的整数天，领域对象仍保留 ISO 日期。version_date 范围转为包含端点的数值 Range；effective_on 要求 from_day≤day 且 to_day≥day，缺端点不命中。scope_any 为任一 keyword 匹配，scope_all 为逐值 AND；不同字段以及 tenant/snapshot 均 AND 合并，不能透传模型自由数据库表达式。

`SearchCandidate` 的完整字段统一列于第 6.1 节，包含 tenant_id、retrieval_mode、nullable 的 vector_rank/lexical_rank 及对应分数；本节只细化约束，不另维护第二份字段列表。

`ToolResult.metadata` 增加 `tenant_id`，且必须与 request context、snapshot 和 Evidence 一致。Search 的 `filters` 在 L5 与应用约束取交集，强制注入 `tenant_id`、`snapshot_id`；L6 不接收可放宽租户边界的自由过滤表达式。外租户 point 在生成 Candidate 前视为来源完整性错误；外租户 candidate 对 Read 返回 `not_found`；外租户 evidence 进入 Claim 时由 ResultValidator 拒绝。

coverage 放在 SearchResult/ToolResult.data 顶层，不在各 Candidate 重复。Candidate 内保留分支排名、分数和 retrieval_mode，无该分支命中时对应 score/rank 为 null，rank 序列化为 nullable integer。SearchResult/ToolResult.data 另含 retrieval_mode、embedding_revision，工具 metadata 与 Observation 均带 tenant_id。租户整体绑定涵盖 ReadResult、Evidence、ConflictAssessment；Citation 从当前请求绑定的 Evidence 展开，不接受外部租户元数据。

### 11.5 Trace 与兜底契约

`TraceEvent` 增加：

```text
intent_assessment_id?
route?: "fast_nonfactual" | "fast_faq" | "deep_query" | "fallback"
route_changed?: boolean
answer_kind?: "nonfactual" | "factual" | "clarification" | "fallback"
tenant_ref?: string
authz_version?: string
embedding_model_id?
embedding_revision?
retrieval_mode?
model_steps_used?: integer
tool_calls_used?: integer
embedding_calls_used?: integer
```

Trace 不持久化明文 principal_id；tenant_ref 使用可信配置中的合成/脱敏映射，实际导出还须按受信身份授权。Trace 事件新增 intent_assessed、route_selected、route_changed、fallback_selected、identity_rejected，均记录真实来源。分类结束、工具返回及最终终止事件记录当时的 model_steps_used/tool_calls_used/embedding_calls_used；零调用必须记为 0，不由模型自报。`fallback` 是路由/表达分类，不是最终 status，不能覆盖 invalid_request 或其他真实错误。非事实固定模板可为 `status=completed` 且 claims/citations 为空，但必须标记 `answer_kind=nonfactual`。
