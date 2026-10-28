# 03 Agent 运行时与决策策略

## 1. 运行循环

Agent 使用单一、有限步数循环。循环骨架固定，动作选择由 `DecisionModel` 根据当前状态作出：

```text
L2: allocate request_id/trace_id; lifecycle = RECEIVED
L2: reject invalid input -> invalid_request CandidateResult -> finalize
L2: bind trusted TenantContext; unauthorized -> invalid_request/access_denied -> finalize
L2: lifecycle = VALIDATED; initialize AgentState with empty evidence and counters
L3 preparation:
    pure nonfactual full-message template -> completed/nonfactual CandidateResult -> finalize
    otherwise check global model budget; model_steps_used += 1; classify intent/depth once
    classification transport/protocol failure -> mapped error CandidateResult -> finalize
    out-of-scope or missing object -> fallback CandidateResult -> finalize
L2: bind immutable tenant KnowledgeSnapshot once; failure -> execution_error -> finalize
L3: resolve FAQ eligibility and RouteDecision; invoke knowledge loop
L3:
    while true:
        if model_steps_used >= max_steps: return incomplete AgentRunResult
        lifecycle = DECIDING
        model_steps_used += 1
        action = DecisionModel.decide(DecisionContext(state))
        if provider transport failure: return execution_error AgentRunResult
        if response/action invalid: return invalid_model_action AgentRunResult
        if action is finish or ask_clarification:
            return AgentRunResult(candidate_result, evidence, assessments, observations)
        if tool_calls_used >= max_tool_calls: return incomplete AgentRunResult
        tool_calls_used += 1 at tool entry
        result = ToolRouter.execute(action)       // L6 creates evidence, if any
        lifecycle = OBSERVING
        append Observation for every ToolResult and append TraceEvent
        store trusted ReadEvidence and ConflictAssessment from L6
        if error is not recoverable: return execution_error AgentRunResult
        continue                                 // includes not_found and recoverable error
L2 finalize:
    recheck authz_version; revoked -> execution_error/access_denied without source text
    assemble candidate with trusted evidence/assessments and trusted routing/answer_kind
    lifecycle = RESULT_VALIDATION
    run shared ResultValidator
    produce validated AnswerResult or validated structural error envelope
    lifecycle = TERMINATED
    serialize Trace; if writing fails, append warning only
```

不能以固定“先检索、再读取、再回答”的硬编码步骤冒充动态决策。可用规则对非法动作作拒绝和终止，但正常的下一步动作由模型/Mock Policy 根据观察决定。

## 2. 状态机

```text
RECEIVED
  ├─ invalid input → RESULT_VALIDATION → TERMINATED
  └─ VALIDATED
       ├─ nonfactual/clarification/out-of-scope/classification error → RESULT_VALIDATION → TERMINATED
       ├─ snapshot/config error → RESULT_VALIDATION → TERMINATED
       └─ DECIDING
            ├─ SEARCH/READ → OBSERVING
            │                 ├─ ok/no_evidence/not_found/recoverable error → DECIDING
            │                 └─ fatal error → RESULT_VALIDATION → TERMINATED
            └─ ASK/FINISH/provider/action/budget exit → RESULT_VALIDATION → TERMINATED
```

`TERMINATED` 是唯一生命周期终态；具体业务结果只写入 `AnswerResult.status`。最终状态为 `completed`、`needs_clarification`、`insufficient_evidence`、`execution_error`、`incomplete`、`invalid_request`、`invalid_model_action`。正常业务退出有未决冲突时为 needs_clarification；执行错误/预算退出不能被冲突状态掩盖，但必须保留已读双方引用。未恢复工具失败不得标成 insufficient_evidence。`status`、`error_code`、`stop_reason` 各自独立，映射见 02 第 8 节。

## 3. 预算和终止

首版建议默认 `max_steps=6`、`max_tool_calls=4`，配置由运行参数/常量提供，不让模型修改。这里是实现初值，需通过样例确保正常路径能完成并限制无休止循环。

- 每次开始调用 DecisionModel 前先检查并递增 `model_steps_used`，provider 失败、无效动作、`finish` 和 `ask_clarification` 均计一次。
- VALIDATED 阶段的 IntentClassifier 调用也检查并计入同一 model_steps_used，规则模板不计模型调用。embedding 另记 embedding_calls_used，每个 Search 至多一次，最多 max_tool_calls 次。
- `tool_calls_used` 在进入工具执行函数时递增，包括失败和 `not_found`；动作校验失败且未进入工具不计工具调用。
- 达到模型步数上限则 `incomplete`。工具预算耗尽只禁止执行新的工具动作；仍允许在模型步数预算内选择 `finish` 或 `ask_clarification`，并完成结果校验。
- 预算终止时保留已收集 Evidence 和 Trace；状态为 `incomplete`，明确剩余工作/资料缺口。
- 预算超限不触发隐藏自动重试，也不得越界执行工具动作。
- 快答转深查沿用同一请求/快照/预算；兜底不增加隐藏生成调用，按固定说明保留真实 status。route 序列化值为 fast_nonfactual、fast_faq、deep_query、fallback。

## 4. 动作决策策略

### 4.1 Search

当当前证据不足以回答，或现有资料可能不覆盖问题时选择搜索。查询可以是原问题、上下文补全后的问题或改写查询；Trace 必须记录实际提交给检索器的 query。scope/date/document filters 只能来自用户明确上下文、资料规则或系统固定范围，不得凭模型猜测。

### 4.2 Read

当候选结果不足以验证结论、冲突双方需要核对原文、或 snippet 缺少限定条件时选择读取。读取目标必须来源于本次搜索结果；Agent 不可自由猜测文档 ID。

### 4.3 Ask clarification

适用于缺失信息可由用户补足的情况，例如报销类型、地区、员工类别或适用制度。追问应只询问最关键缺口，不预设答案。若资料本身没有提供任何可追问的消歧条件，则以 `insufficient_evidence` 说明。

### 4.4 Finish

满足以下任一条件即可结束：

- 证据足够、相互一致、适用范围明确；
- 识别到无法消解的冲突，已整理双方证据并需要用户确认；
- 资料中无支持性信息；
- 工具/模型错误或预算约束导致无法继续。

模型提出 finish 或 ask_clarification 后，L3 返回 AgentRunResult，L2 调用统一结果组装器/验证器检查候选结果、状态和 Evidence 绑定。验证器不改写业务答案、不替模型决定优先级。`AgentState.conflict_assessments → AgentRunResult.conflict_assessments → AnswerResult.conflict_assessments` 保留评估内容，Trace 通过 ID 关联。所有最终结果都必须经过 RESULT_VALIDATION。

## 5. 意图识别与检索路由

意图与深度采用独立标签；分类只产出 RouteDecision，不产出答案、不改变用户身份。全句纯问候/能力说明使用非事实模板；其余请求通过 L4 IntentClassifier 分类。含制度子问题的寒暄语不能命中纯问候快捷模板。classifier 的 uncertain/unknown 按深查或澄清处理，不因置信分数直接快速回答。

FAQ 快答只在当前租户快照内的标准问句/别名目录命中，且来源清单完整、唯一适用 section、用户条件齐全时标为 fast_faq；仍须模型选择 Search、Read、Finish。FAQ 目录不能作为 Citation。若检索截断、原文出现限定条件/其他版本或冲突，记录 route_changed 事件并设置 effective_route=deep_query，沿用同一 snapshot 和剩余预算。

混合检索、租户过滤、embedding 配置、发布生命周期和兜底映射见 `10-routing-vector-and-tenant-design.md` 第 5–13 节。统一不变量：向量/词项分数用于召回排序，不证明事实、制度权威或证据充分性；任何事实主张仍须绑定 ReadEvidence。

## 6. 证据评估与冲突分类

### 6.1 证据充分性

判断顺序：

1. 是否存在与问题实体/规则相关的 Evidence？
2. 原文是否直接支持将要输出的事实主张？
3. 适用范围、时间和对象是否适配问题上下文？
4. 是否存在反向证据或明确替代关系？
5. 所有最终主张是否有来源绑定？

明确答案必须通过这些检查。检索命中或模型自信不能代替证据充分性。

### 6.2 冲突类型

- **表面冲突**：相同规则不同措辞，规范化后结论一致。
- **数值/规则冲突**：同一适用条件下出现相反结论。
- **范围差异**：不同部门、地区、产品或人员类别各自适用不同值。
- **版本演进**：有明确生效期或 `supersedes` 替代关系。
- **未决冲突**：资料没有提供足够范围或优先级信息。
- **证据质量问题**：片段残缺、来源未读、文档状态不明。

### 6.3 处理规则

- 只有文档明确给出的 scope、有效状态、替代关系或优先级可用于消歧。
- `version_date` 本身不能自动选新文档；新日期不等于已废止旧文档。
- 有明确范围时，回答应说明适用范围，并只引用适用来源；若用户范围仍缺失则追问。
- 未决冲突须并列展示双方来源和原文，列出缺失的规则维度，返回 `needs_clarification`。
- 对适用范围相同/未分离的来源，明示 `supersedes` 或可比较 `priority` 可选取适用结论；关系双方仍保留为 Citation/ConflictAssessment。相互替代或循环关系不能产生确定赢家。
- 不得通过平均、投票、检索分数或模型置信度合并冲突结论。

## 7. 多轮上下文策略

- 每个 CLI 请求只处理一轮；调用者可将历史显式传入下一次请求。
- 裸追问如“多久”在分类前可用最近明确的 user 对象补全；不从 assistant 历史生成制度 Evidence。
- 历史仅帮助解析指代、范围和用户已明确确认的条件，不是制度来源。
- 生成独立 `normalized_question`，并在 Trace 中保留原 query 和改写 query。
- 新轮次重新检索当前知识快照，不能沿用上轮未验证的结论作为事实。
- 如果历史中的 assistant 内容与当前证据冲突，当前文档证据优先于历史说法，并说明需要重新核对。
- 历史长度受配置上限约束；裁剪时优先保留最近用户确认的条件，不做自动摘要伪装为原始事实。

## 8. 两条必要差异路径

### 路径 A：证据充分，读取后完成

```text
问：差旅报销的提交期限是多久？
决策 1：search_documents(query=..., filters.scope_any=["差旅报销"])
观察：candidate C1 对应 D01，片段显示 30 天，范围匹配
决策 2：read_document(candidate_id=C1)
观察：原文确认员工应在费用发生后 30 天内提交
决策 3：finish(completed, claim→E1)
```

### 路径 B：检索结果冲突，读取后追问

```text
问：报销期限是多久？
决策 1：search_documents(query=...)
观察：C1/D01=30 天、C2/D02=15 天；范围未说明
决策 2：read_document(candidate_id=C1)
观察：E1 原文支持 30 天
决策 3：read_document(candidate_id=C2)
观察：E2 原文支持 15 天；ConflictAssessment 无优先级或范围消解依据
决策 4：finish(needs_clarification, 双方证据 + gap)
结果：并列披露，不擅自选一个，询问适用制度
```

若第一次搜索无命中，Agent 可基于观察改写查询后再搜；`read_document` 的 `not_found` 只表示当前候选读取失败，不代表知识库无命中，模型可选择重新搜索或结束；若工具异常，不得伪装成路径 A 的无命中结果。编排器禁止自动重放同一动作。

实现按操作键跟踪未恢复失败：Search 使用 query + 实际合并 filters，Read 使用当前快照 source_ref（未知 candidate 使用其 ID）。同一来源新 candidate 成功读取可恢复旧 Read，其他无关成功不能清除失败。已知未读候选、召回截断或未恢复失败都不得 completed；这不代表检索已覆盖全库或自动证明语义充分性。

## 9. Mock 与真实模型边界

### Mock

- 输入请求与 `AgentState`，返回与场景绑定的结构化动作。
- 场景应由工具结果触发动作差异，不允许直接返回预写最终 Trace。
- 记录 `model_mode=mock` 与 Mock policy/fixture ID。
- 只用于流程、数据契约和边界测试，不用于宣称模型具备推理能力。

### 真实模型

- 仅实现 `DecisionModel` 适配器；使用结构化 JSON 输出或等效解析。
- 系统策略与工具结果明确分区，检索文本按 untrusted data 呈现。
- API Key 仅由环境变量读取；日志、异常、Trace 不输出密钥。
- 响应结构错误诊断码为 `provider_response_invalid`，最终为 `invalid_model_action`；传输/超时失败为 `execution_error`。不把自然语言猜测为工具调用。
- 真实运行 Trace 标注 provider/model/配置版本，但不得保存隐式思维链。
