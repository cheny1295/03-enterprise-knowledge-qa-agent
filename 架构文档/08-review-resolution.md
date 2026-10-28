# 08 架构审查问题收敛记录

> 状态：已同步到 01–07/10 的规范性设计决策。02 是字段/错误映射的单一规范入口，本文汇总边界和约束；09 是修复与覆盖记录，不增设“新文档覆盖旧文档”的兼容接口。

## 1. 统一架构口径

系统固定为六个业务层：

1. 交互层（CLI）；
2. 应用服务层；
3. Agent 编排层；
4. 模型适配层；
5. 工具执行层；
6. 知识与证据层。

`AnswerResult`、`ResultValidator`、`TraceWriter`、配置和错误归一化是横切能力，不单列为第七层。模块可以在代码中合并，但逻辑职责不变。

## 2. 统一状态与结果契约

`AgentState.lifecycle_state` 只表示运行阶段：

```text
RECEIVED
VALIDATED
DECIDING
OBSERVING
RESULT_VALIDATION
TERMINATED
```

最终 `AnswerResult.status` 只使用以下枚举：

```text
completed
needs_clarification
insufficient_evidence
execution_error
incomplete
invalid_request
invalid_model_action
```

三个不同语义字段不得混用：

- `status`：用户/调用方可判断的最终业务结果；
- `error_code`：可选诊断码，如 `provider_unavailable`、`invalid_arguments`、`internal_error`；
- `stop_reason`：循环为何停止，如 `finished_with_evidence`、`clarification_required`、`budget_exhausted`。

`AnswerResult` 最小必需形状：

```text
AnswerResult {
  request_id
  tenant_id: string | null
  routing: RouteDecision | null
  answer_kind
  status
  answer
  claims[]
  citations[]
  gaps[]
  follow_up_question?
  conflict_assessments[]
  warnings[]
  error_code?
  stop_reason
  snapshot_id?
  trace_id
}
```

`ask_clarification` 不是直接终止状态，而是生成 `CandidateResult(status=needs_clarification)`，与 `finish` 生成的候选结果一样必须经过 `RESULT_VALIDATION`。

工具返回状态与最终状态分离：`ToolResult.status` 仅为 `ok | no_evidence | not_found | error`；`ToolError.code` 承载执行诊断；所有工具返回先成为 Observation。`not_found` 不是错误码，`invalid_result` 只是 ResultValidator 的诊断码。最终状态与停止原因按 02 第 8 节映射，不另设枚举。

## 3. 统一证据身份链

```text
SearchDocuments
  → SearchCandidate(candidate_id, tenant/request/snapshot, snippet, source identity)
  → ReadDocument(candidate_id)
  → ReadEvidence(evidence_id, source_candidate_id, exact quote)
  → Claim(evidence_ids=[ReadEvidence.evidence_id])
  → Citation(expanded from ReadEvidence)
```

- SearchCandidate 的 snippet 只可用于选择是否读取，不可作为最终 Citation。
- `read_document` 必须携带当前请求 Search 产生的 `candidate_id`。
- Read 成功后 EvidenceService 创建 `ReadEvidence`；原文引文保持原样。
- 最终 Claim 仅能引用本次请求、当前快照中的 ReadEvidence。
- EvidenceService 是证据创建、去重、来源回查和快照一致性校验的唯一责任方。
- Candidate、ReadEvidence 和 ConflictAssessment 均绑定 `tenant_id + request_id + snapshot_id`；Read 使用跨请求/租户 candidate 返回 not_found，结果引用外请求/租户 evidence 则校验拒绝为 invalid_model_action，不输出外域资料。
- 程序验证“引用可回查”；语义支持度作为独立评测项，不宣称字符串匹配即可证明蕴含。

## 4. 冲突职责与裁决规则

知识与证据层提供：

```text
ConflictAssessment {
  assessment_id
  tenant_id
  request_id
  snapshot_id
  candidate_ids[]
  evidence_ids[]
  relation: consistent | scope_disjoint | explicit_supersession |
            explicit_priority | unresolved_conflict | insufficient_data
  basis_fields[]
  unresolved_gaps[]
}
```

- `AgentState.conflict_assessments` 保存运行期 assessments；`AgentRunResult` 与 `AnswerResult` 传递其内容，Trace 以 `conflict_assessment_ids` 关联。
- 未决冲突时，Assessment 必须覆盖冲突双方 candidate/evidence；正常业务退出必须 needs_clarification 并提出追问。不可恢复错误或预算耗尽优先表达真实停止原因，仍保留 assessments 和双方引用，任何情况下都不得 completed。
- `ConflictAnalyzer` 只能依据已读取 Evidence 和资料显式字段形成候选冲突分类。
- 语义是否为“同一规则”可由模型提出判断，但模型不得判定来源权威性。
- 自动消解只允许基于明确且相符的 `scope`、`effective_from/effective_to`、`supersedes` 或显式 `priority`。
- `version_date`、检索分数、文件顺序、模型置信度不能单独构成优先级。
- 条件不明确或冲突分类不确定时，不自动选胜者；同一规则的相反结论归为 unresolved_conflict。证据尚未读全时为 insufficient_data，继续读/搜或按缺口结束，不能声称完成了冲突查证。
- ResultValidator 不独立推理冲突；它依据 ConflictAssessment 阻止 unresolved 情况被标为 `completed`。

`withdrawn` 资料默认不参与活动制度回答；若用户询问历史规定，可在显式时间范围过滤后作为历史证据返回，并清晰标注非当前有效。仅有较新日期不能推导旧文档已撤销。

ConflictAnalyzer 的运行接口是 `assess(read_evidence, confirmed_conditions) -> ConflictAssessment[]`，由 L3 在每次新增 ReadEvidence 后调用。它只处理当前问题已经读取的资料，不扫描或调用模型；读取不全、相关性/规则对应关系不清楚时保守标记 insufficient_data 或 unresolved_conflict，不假定“未发现”即“已证明一致”。模型可在答案/简短依据中指出疑似同规则，但该意见不是权威/优先级字段。首版合成语料中的明确重复、相反数值和显式范围/替代关系是验收对象，不宣称具备通用自然语言冲突检测能力；语义判断是否充分需在 E4/E5 中单独评价。

L6 为一次证据集合和已确认条件生成当前 assessment；后续读取补齐同组证据时替换该组当前评估，旧 assessment 保留在 Trace，不让已过期的 unresolved 标记永久阻止完成。ResultAssembler 的 Citation 集合是 Claim.evidence_ids 与所有当前冲突 Assessment.evidence_ids 的并集，由 EvidenceService 回查展开；即使纯澄清 claims=[]，也不会遗漏双方引用。

## 5. 快照创建与一致性

- 应用服务在知识路径完成身份/路由准备后调用一次 `load_snapshot(tenant_context)`，绑定该可信租户的当前 ACTIVE 快照；规范签名见 02 第 11.3 节。
- 纯非事实模板和前置拒绝无需快照；知识请求必须在可信身份授权后绑定一次当前租户 ACTIVE 快照。
- 返回的 `KnowledgeSnapshot` 对该请求不可变，并注入 SearchDocumentsTool、ReadDocumentTool、EvidenceService。
- 工具不可隐式重新加载资料。
- 最小 manifest：

```text
KnowledgeSnapshot {
  snapshot_id
  tenant_id
  dataset_version
  index_version
  embedding_model_id
  embedding_revision
  vector_dimension
  publication_state
  document_versions[]
}
```

- snapshot_id 由发布入口分配的唯一标识与 manifest 绑定，不要求内容哈希；同一租户、数据集、索引和文档版本清单保持不可变关联。
- SearchCandidate、ReadEvidence、ToolResult、AnswerResult、TraceEvent 均携带同一 snapshot_id。
- Candidate、ReadEvidence 和 ConflictAssessment 同时绑定 `tenant_id + request_id`；跨租户、request 或 snapshot 的 ID 不可复用。
- 更新资料只影响后续新请求；不得跨请求复用 candidate/evidence ID。

## 6. 工具契约与结果示例规则

统一 `ToolResult.evidence` 类型为 ReadEvidence 对象数组，Search 固定为空；ID 列表只能命名为 evidence_ids 或 candidate_ids。完整 ToolResult 示例必须包含非空 tool_call_id、metadata.tenant_id/request_id/snapshot_id、elapsed_ms。L6 SearchResult/ReadResult 到 ToolResult、再到 L3 Observation 的唯一定义见 02 第 6/11 节。

read_document 唯一模型参数为 candidate_id；document/version/section 由 Candidate 解析，额外字段即使值一致也拒绝，不设调试兼容层。未知 candidate 字符串返回 not_found，不回退读取；缺失/类型错误则拒绝模型动作。

## 7. 预算与错误恢复

计数规则：

1. 决策前先检查 `model_steps_used < max_steps`；达到上限则 `incomplete`。
2. 每次开始调用 DecisionModel 前，先将 `model_steps_used` 加一；有效、无效或 provider 失败的决策请求都计数。
3. 模型返回无工具动作（finish/ask）时，即使工具预算为零仍允许完成结果校验。
4. 模型返回工具动作时，执行前检查 `tool_calls_used < max_tool_calls`；达到上限则不执行工具，结果 `incomplete`。
5. 每次进入工具执行函数即将 `tool_calls_used` 加一，包括失败和 not_found。

分类调用也计入 model_steps_used；纯非事实规则模板不计模型调用。查询 embedding 单独记录 embedding_calls_used，每次 Search 至多一次，不产生 AgentAction。快转深不重置任何预算。

工具重试：

- 禁止编排器自动重放同一动作。
- 工具失败形成 Observation；若错误标为可恢复且仍有预算，模型可决定一个新的动作（可不同于原动作），新决策/工具调用照常计数。
- `not_found` 只表示当前 candidate/来源读取失败，不等于知识库无命中；可由模型决定重新搜索或终止。
- 不可恢复内部错误先登记 error Observation，再返回 execution_error 候选，经 RESULT_VALIDATION 结束。
- `ToolResult.status` 仅为 `ok | no_evidence | not_found | error`；`ToolError.code` 是诊断命名空间；`AnswerResult.status` 只使用本文件列举的最终枚举；`stop_reason` 只描述终止原因。
- `not_found` 是工具/Observation 状态，不是 `ToolError.code`；`invalid_result` 是 `ResultValidator` 诊断码，不是最终结果状态。
- 未恢复错误按实际 Search query/filters 或 Read source_ref 匹配；无关成功不能冲销。模型 completed 还必须没有已知未读候选和截断标记，结果校验独立检查这些条件。

## 8. Result/Trace 责任

- `ResultAssembler` 只把候选结果与可信 ReadEvidence/ConflictAssessment 组装，不代替模型生成业务答案。
- `ResultValidator` 对所有结果路径执行同一契约校验，包括澄清动作。
- L3 只返回 AgentRunResult，L2 是唯一的结果组装/校验和最终状态出口。输入拒绝从 RECEIVED 进入校验，快照加载失败从 VALIDATED 进入校验；二者均可 snapshot_id=null。
- `TraceWriter` 由应用服务统一写入；编排器发出事件，工具返回自己的结构化摘要。
- 所有最终结果都过 `RESULT_VALIDATION` 后进入 `TERMINATED`。

## 9. 动态决策的验收契约

必须有一项相同问题/不同观察的对照测试：

- 固定 question、history、model mode 和初始状态；
- 分别注入单一命中、冲突命中、no_evidence、not_found、可恢复 error 观察；不可恢复工具错误和 provider 错误单独验证 runtime 终止；
- 断言后续动作与观察类别一致且至少两个观察触发不同动作；
- Trace 记录 Observation ID/状态、后续动作、动作来源。
- Trace 中 `decision_source` 必填；Mock 决策记录 `policy_id` 或 `fixture_id`，模型决策记录 `model_mode` 与适配器标识。

Mock 可证明编排器把观察传入决策并执行不同路径，不证明真实模型决策质量。真实模型结果需单独记录，不得以 Mock 代替。

## 10. 评测硬失败

以下任何一项都不能被总体评分抵消：

- 无来源或不存在的 Citation；
- 未读取候选被用作事实性最终引用；
- 未决冲突中只输出一个值或隐式选择优先级；
- 工具异常伪装成 `no_evidence`；
- 越过工具白名单、快照或 candidate 授权；
- 超过步骤/工具调用预算；
- provider 模式静默降级为 Mock；
- 恶意文档诱导执行未注册工具。

## 11. 新范围决策：向量、意图、多租户与三路回复

用户明确上述六项为当前必做能力，不再以“题目未要求”或“后续企业化”将它们排除。设计细节见 10，字段与错误枚举集中于 02 第 11 节，验收见 04 E13–E23。

1. 使用 Qdrant local 持久化、CUDA 中文 embedding 和词项混合召回；原文、FAQ、词项与向量索引绑定 tenant/snapshot 并完整发布，不读半成品。
2. L2 可信身份解析与授权；L3 意图/深度路由和动态循环；L4 分类/决策/embedding 协议；L5 强制约束；L6 存储与证据。L5 只调用 embedding 协议，不调用决策模型；L6 不调用模型 SDK。
3. RouteDecision 不增设生命周期/最终状态。非事实模板可 completed 且无快照/证据；事实 FAQ 仍 Search→Read→Finish；缺条件/冲突/截断转 deep_query。
4. 分类计入同一模型预算，快转深不重置预算；embedding 按 Search 次数单独计数；兜底不隐藏追加调用。
5. tenant/request/snapshot 绑定贯穿数据、候选、证据、冲突、Trace 和输出；scope 是业务适用条件，不是权限。模型不可输入 tenant/collection/path。
6. 无效身份 invalid_request/access_denied；执行期撤权 execution_error/access_denied；跨租户 candidate not_found，非法 evidence 引用拒绝。兜底不能把这些原因改写为资料不足或 completed。

上述是目标设计，不表示代码、真实模型或两租户运行已通过；09 的旧修复历史保留，新范围另行登记。
