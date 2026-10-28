# 06 逐状态详细设计

本系统状态分为两个维度：

1. **Agent 生命周期状态**：表示一次问答目前处于哪个阶段；
2. **最终业务状态**：表示用户最终得到的是确定答案、需澄清、资料不足、工具失败还是未完成。

`fast_nonfactual`、`fast_faq`、`deep_query`、`fallback` 是 RouteDecision，不新增生命周期状态；其字段见 02 第 11 节。

## 1. 状态总表

| 状态 | 类型 | 是否终止 | 允许进入来源 |
|---|---|---:|---|
| `RECEIVED` | 生命周期 | 否 | 新请求 |
| `VALIDATED` | 生命周期 | 否 | `RECEIVED` |
| `DECIDING` | 生命周期 | 否 | `VALIDATED`/`OBSERVING` |
| `OBSERVING` | 生命周期 | 否 | 任一工具已返回 ToolResult，在本状态登记成功/no_evidence/not_found/error Observation |
| `RESULT_VALIDATION` | 生命周期 | 否 | 模型选择 `finish`/`ask_clarification` 或运行时产生候选结果 |
| `TERMINATED` | 生命周期 | 是 | 候选结果/执行结果完成统一校验 |

最终业务状态只写入 `AnswerResult.status`，枚举为 `completed`、`needs_clarification`、`insufficient_evidence`、`execution_error`、`incomplete`、`invalid_request`、`invalid_model_action`。`error_code` 是可选诊断码，`stop_reason` 是必填停止原因；二者都不是状态。`TERMINATED` 后不可重新进入 `DECIDING`。一次请求只能有一个最终业务状态。

## 2. 通用状态处理模板

下文第 8–14 节的标题表示 `AnswerResult.status` 对应的业务结果类别，不是额外的生命周期状态；生命周期唯一终态仍为 `TERMINATED`。`INVALID_REQUEST`、`NEEDS_CLARIFICATION` 等大写标签只为说明性标题，实际序列化值使用前述小写状态枚举。

每个状态处理必须回答：

- 进入条件是什么？
- 允许读取哪些状态字段？
- 可以执行什么副作用？
- 成功后转到哪里？
- 失败后转到哪里？
- 必须写入哪些 Trace？
- 哪些状态不允许进入？

## 3. `RECEIVED`

### 进入条件

CLI/调用方提交请求对象，但尚未进行业务校验。

### 允许动作

- 创建 `request_id`。
- 记录接收时间和运行模式。
- 读取输入字段，不读取知识、不调用模型。
- 校验可信身份及租户成员资格；拒绝结果为 invalid_request/access_denied，经统一出口，不披露外租户是否存在。

### 退出条件

- 输入字段合法 → `VALIDATED`。
- 输入为空、类型错误、历史不合法、预算非法 → 构造 invalid_request 候选 → RESULT_VALIDATION → TERMINATED；不进入 VALIDATED/DECIDING。

### Trace

记录 `request_id`、输入字段摘要、mode、配置摘要。默认不记录完整敏感问题。

### 禁止事项

- 不调用任何工具。
- 不根据问题内容预判答案。

## 4. `VALIDATED`

### 进入条件

输入通过 schema 和业务基本校验。

### 处理

- 创建带 TenantContext 的初始 AgentState，初始化模型/工具/embedding 计数为 0，证据与 assessments 为空。
- L3 先处理全句非事实模板规则；其他请求通过 L4 IntentClassifier 分类一次，调用前检查并增加 model_steps_used。
- 纯非事实模板及前置澄清/题外/分类失败可直接形成候选；知识问题绑定一次当前租户快照，再检查 FAQ 资格并记录 RouteDecision。
- 快照加载前 snapshot_id 为 null，成功后不可再改变；索引、原文和 FAQ 都属于同一 tenant/snapshot。

### 退出条件

- 快照加载成功 → `DECIDING`。
- 非事实模板/明确题外/有效追问/分类错误 → RESULT_VALIDATION；不启动工具循环。
- 快照失败 → execution_error / knowledge_load_error 候选 → RESULT_VALIDATION → TERMINATED；snapshot_id=null，不调用后续决策模型/工具，已发生的分类计数仍保留。

### 不变量

- `question` 非空。
- `max_steps > 0`、`max_tool_calls >= 0`。
- Agent 尚未执行工具。

## 5. `DECIDING`

### 进入条件

首次初始化或工具观察完成，且仍有预算。

### 处理

1. 检查模型步数预算；调用 DecisionModel 前递增 `model_steps_used`，所有决策尝试都计数。
2. 构造 `DecisionContext`。
3. 调用 `DecisionModel.decide()`。
4. 解析并验证动作。

### 退出条件

| 动作 | 下一状态 |
|---|---|
| `search_documents`/`read_document` | 执行工具后 `OBSERVING` |
| `ask_clarification` | `RESULT_VALIDATION` |
| `finish` | `RESULT_VALIDATION` |
| 无效动作 | 构造 `invalid_model_action` 候选结果后进入 `RESULT_VALIDATION` |
| provider 不可达/超时 | 构造 execution_error 候选结果后进入 RESULT_VALIDATION |
| provider 响应无法解析/不合动作协议 | invalid_model_action + provider_response_invalid 候选 → RESULT_VALIDATION |
| 无剩余预算 | 生成 `incomplete` 候选结果后进入 `RESULT_VALIDATION` |

### Trace

必须记录动作类型、动作参数摘要、决策依据摘要、剩余预算、模型模式、decision_source 和 input_observation_ids。Mock 还需 policy_id/fixture_id；runtime 停止不冒充模型动作。

### 禁止事项

- 不直接修改 Evidence。
- 不直接写最终 Citation。
- 不执行未通过参数校验的动作。

## 6. `OBSERVING`

### 进入条件

工具已返回 ToolResult；在 OBSERVING 中统一登记 Observation。成功、no_evidence、not_found 和 error 均可进入，不以成功为前置条件。

### 处理

- 将每种工具返回（`ok`、`no_evidence`、`not_found`、`error`）转换为 Observation。
- 仅接受知识与证据层产生的可信 ReadEvidence/ConflictAssessment；编排层不创建或去重 Evidence。
- 记录工具状态、返回摘要、Evidence ID。
- 将工具错误分类，不把错误转为空命中。

### 退出条件

- 工具返回 `ok` / `no_evidence` → `DECIDING`，由模型依据 Observation 决定下一动作。
- 工具返回 `not_found` → `DECIDING`，可重新搜索或结束；不得解释为全库无命中。
- 工具失败且可恢复 → `DECIDING`，错误 Observation 交给模型决定新的动作；不得自动重放原动作。
- 工具失败且不可恢复 → 先登记 error Observation，再构造 `execution_error` 候选并进入 `RESULT_VALIDATION`。
- 工具返回来源不满足契约 → `RESULT_VALIDATION`，最终状态 `execution_error`。
- FAQ 条件不完整、候选截断/来源不唯一或出现冲突：记录 route_changed 为 deep_query，再按同一 Observation 决策；不重新开始请求，不重置预算。

### 不变量

- Observation 必须对应当前步骤的一个工具调用。
- Evidence 只能来自当前知识快照。
- 工具结果原文不改变，摘要只是 Trace 展示。

## 7. `RESULT_VALIDATION`

### 进入条件

模型选择 `finish` 或 `ask_clarification`，或运行时产生必须返回的结构化执行结果，形成候选答案和主张。

### 校验项目

1. `status` 是否与 answer、gaps、follow-up、`error_code`、`stop_reason` 一致；
2. 每个事实性 Claim 是否引用存在的 Evidence ID；
3. Citation 是否能回查文档版本、section 和原文；
4. completed 是否存在未解决冲突；assessment 的 request/snapshot、candidate/evidence 关联是否合法；
5. needs_clarification 是否有缺口和追问；未决冲突是否保留全部双边引用；
6. `incomplete` 是否有 `stop_reason`，`execution_error` 是否有 `error_code`。
7. tenant/request/snapshot 和 authz_version 是否满足绑定；撤权不输出已有正文/引文。
8. 非事实 completed 是否来自固定模板，answer_kind=nonfactual 且无制度主张；知识 completed 必须有证据。错误兜底不得修改真实失败类型。

### 退出条件

- 校验通过 → 生命周期进入 `TERMINATED`，最终业务状态保留在 `AnswerResult.status`。
- 校验失败 → 丢弃不可信答案/引用，使用固定结构的错误信封：模型候选违规为 invalid_model_action，内部来源损坏为 execution_error；error_code=invalid_result，原始失败和工具错误保留 Trace。
- 错误信封仅作字段/来源结构检查，仍在 RESULT_VALIDATION 内完成；不重新生成业务答案、不再次调用模型、不无限重试校验，之后进入 TERMINATED。

### 禁止事项

- 不替模型补充缺失引用。
- 不将冲突自动改成“最新文档有效”。
- 不把校验失败的结果标记为 completed。

## 8. `NEEDS_CLARIFICATION`

### 进入条件

- 模型选择 `ask_clarification`；
- 或 Evidence 显示多个冲突结论，且资料没有明确适用范围/优先级；
- 或问题缺少决定答案所需的业务范围。

### 必须包含

- 面向用户的简洁说明；
- `gaps`，说明缺什么；
- 如有冲突，双方/多方 Citation；
- 与全部冲突 Evidence 关联的 ConflictAssessment（无冲突的普通澄清则为空数组）；
- 一个最小、可回答的追问。

### 退出

此业务结果在 `AnswerResult.status=needs_clarification` 中表达；生命周期先经过 `RESULT_VALIDATION`，再进入 `TERMINATED`。用户下一轮提交新 `AskRequest`，不能在本请求内部隐式等待。

### 验证

- 冲突场景不得只输出一个数值。
- 追问不得暗示系统已经选择某一制度。
- 若有证据，引用必须完整保留。

## 9. `COMPLETED`

### 进入条件

- 有充分证据；或属于经过白名单确认的非事实固定模板；
- 适用范围与问题匹配；
- 没有未决冲突；
- 结果校验通过。

### 必须包含

- 明确回答；
- 支持事实主张的 Citation；非事实模板的 claims/citations 为空并标记 answer_kind=nonfactual；
- 必要的适用范围/版本说明；
- `status=completed`。

### 禁止事项

- 不能出现 Evidence 未支持的新事实；
- 不能把检索分数写成事实置信度；
- 不能引用不存在或未读取的片段。

## 10. `INSUFFICIENT_EVIDENCE`

### 进入条件

- 检索和允许的改写/读取后，仍无支持性资料；
- 或资料只命中相关主题，但没有回答问题所需的事实。

### 必须包含

- 明确说明资料不足；
- `gaps` 说明缺少什么资料/条件；
- 不制造确定答案；
- 若存在相关但不足的 Evidence，可引用并说明其局限。

### 与 `NEEDS_CLARIFICATION` 的区别

- `needs_clarification`：用户提供范围/条件后可能继续得到答案；
- `insufficient_evidence`：当前资料本身没有可支持答案的内容，追问不一定能补齐。

## 11. `execution_error` 结果

### 进入条件

- 知识加载失败；
- provider 不可达/超时；响应不可解析属于 invalid_model_action，不在此类；
- 工具内部异常；
- 工具返回结果违反来源契约且无法安全恢复。

### 必须包含

- `status=execution_error` 和 `error_code`；
- 安全错误说明；
- 已完成步骤和已有 Evidence；
- `stop_reason`；
- 明确说明未能完成，而不是资料不存在。

### 禁止事项

- 不将异常重写成 `no_evidence`；
- 不自动切换 Mock；
- 不在错误后继续调用未必要工具。

## 12. `incomplete` 结果

### 进入条件

- 达到最大步骤或工具调用数；
- 外部中断；
- 预算不足导致无法继续恢复；若模型明确放弃仍未恢复的执行错误，用 execution_error 而非 incomplete。

### 必须包含

- 已执行 Trace；
- 已收集 Evidence；
- `stop_reason=budget_exhausted` 或具体中断原因；
- 尚未完成的 gap。

### 验证

- 工具调用计数不得超过上限。
- 最后一条 Trace 能解释为何停止。
- 不输出看似完整但没有证据绑定的答案。

## 13. `invalid_request` 结果

### 进入条件

请求在 Agent 启动前失败。

### 必须包含

- 字段级错误；
- 不调用模型、不调用工具；
- 明确提示调用方修正输入。

## 14. `invalid_model_action` 结果

### 进入条件

- 动作类型不在白名单；
- 必填参数缺失或类型错误；
- read_document 参数结构非法或多带文档/版本/章节字段；合法形状但未知 candidate 字符串由工具返回 not_found，不直接当作动作错误；
- `finish` 引用了不存在的 Evidence；
- 模型返回无法解析的自由文本。

### 必须包含

- 原始动作的安全摘要；
- 具体契约错误；
- 是否已执行工具（通常为否）；
- `stop_reason=action_rejected`，error_code 说明 invalid_arguments、provider_response_invalid 或 invalid_result。

## 15. 状态转换不变量

1. 所有工具调用只能从 `DECIDING` 发生。
2. 所有工具结果（含错误和 not_found）都先转换为 Observation，再进入 `OBSERVING`。
3. 所有最终答案必须经过 `RESULT_VALIDATION`。
4. 终止状态不可再次调用模型。
5. 任何状态都不能凭空新增 Evidence。
6. `execution_error`、`insufficient_evidence`、`incomplete` 语义不可互换；`not_found` 不等于 `no_evidence`。
7. 前置校验失败和知识快照加载失败不得绕过 `RESULT_VALIDATION`；它们生成候选结果后进入统一校验。
