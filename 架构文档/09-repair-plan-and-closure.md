# 09 架构审查修复与收敛记录

## 1. 文档目的

本文件把基于 `02-solution.md`、`06-open-source-reference-survey.md`、`07-recursive-coverage-and-open-questions.md` 的子 agent 审查意见落成可执行的修复清单，并记录契约收敛。它只记录修复，不新增覆盖 01–08/10 的接口；字段、错误映射和状态由 02 统一定义，其他规范文档同步使用该定义。

## 2. 修复范围与成功标准

本轮只修复设计文档，不修改业务源代码。完成标准如下：

1. Search/Read/Evidence/Citation 的身份链只有一条规范主路径；
2. 工具状态、内部错误码、最终结果状态和停止原因属于不同命名空间，并有明确映射；
3. `ConflictAssessment` 能从知识层进入 AgentState、AgentRunResult、AnswerResult 和 Trace；
4. 所有前置失败、工具成功、`not_found`、可恢复错误和不可恢复错误都能进入闭合的生命周期路径；
5. `scope`、有效期、资料状态和默认过滤器具有明确来源与匹配语义；
6. 覆盖矩阵能区分架构定义、契约定义、实现待补和运行证据待补；
7. 文档不再保留会被误读为实现接口的旧字段、旧状态或旧调用示例。

## 3. 审查问题到修复动作

| 编号 | 审查问题 | 修复动作 | 验收证据 |
|---|---|---|---|
| R01 | Search 示例仍使用 `hits/evidence_id` | Search 只产生 `SearchCandidate(candidate_id)`；删除旧主路径 | `07` 的主路径和示例 |
| R02 | Read 同时出现 `candidate_id` 与 `evidence_id` | `read_document` 唯一授权输入为 `candidate_id`；ReadEvidence 只在成功读取后生成 | `02/07/08` 一致 |
| R03 | `ToolResult.status` 与 `AnswerResult.status` 混用 | 固定四个命名空间及映射表 | `05/08` 状态表 |
| R04 | `tool_error`、`invalid_result` 残留 | 删除旧错误状态用例，不保留兼容标签；invalid_result 仅为校验诊断码 | `02/04/05/06/08` 错误表 |
| R05 | `ConflictAssessment` 无闭环 | 增加 AgentState、AgentRunResult、AnswerResult 的 conflict_assessments 和 Trace 关联 | `02/03/05/08` 传递契约 |
| R06 | 前置错误和 Observation 错误路径不闭合 | 所有工具返回先转 Observation；可恢复错误回到 `DECIDING`，不可恢复错误生成 `execution_error` 候选并校验 | `06/08` 状态转移表 |
| R07 | 有效期字段未进入资料 schema | 在资料元数据中明确 `effective_from/effective_to` 可空且为 ISO 日期；缺失时不推导时效优先级 | `02/08` schema |
| R08 | `scope` 数组与过滤字符串语义不清 | 统一为 `scope: string[]`；过滤器采用 `scope_any/scope_all`，匹配规则显式定义 | `02/07` 工具契约 |
| R09 | 默认 `withdrawn` 过滤来源不明 | 应用服务生成默认过滤器；模型只能提出显式条件，不能绕过系统过滤 | `02/07/08` 过滤规则 |
| R10 | 动态决策 Trace 缺少来源 | 定义 decision_source、input_observation_ids、policy_id/fixture_id、事件类型 | `02/03/04/08` Trace 契约 |
| R11 | 跨请求 ID 拒绝规则不足 | request/snapshot 绑定；Read 旧 candidate 返回 not_found，最终 Claim 旧 evidence 校验拒绝 | `02/04/06/07/08` 安全规则 |
| R12 | 覆盖矩阵把设计和实现混在一起 | 分列架构、契约、实现、运行证据，缺失交付项如实登记 | 本文第 9 节及 README |

以上 R01–R12 的设计修复均已落到对应文件；“关闭”仅表示设计契约已改正，不等同业务实现或运行验收通过。

## 4. 统一命名空间

### 4.1 工具返回状态

`ToolResult.status` 只允许：

```text
ok | no_evidence | not_found | error
```

其中 `no_evidence` 是正常检索结果，`not_found` 是指定 candidate 无法读取，`error` 是工具执行失败。

### 4.2 内部工具错误码

`ToolError.code` 用于诊断：

```text
invalid_arguments
knowledge_load_error
timeout
provider_unavailable
provider_response_invalid
internal_error
access_denied
embedding_unavailable
vector_store_error
index_version_mismatch
```

`ToolError.code` 不直接写入 `AnswerResult.status`。清单与 02 第 10 节同步；最终诊断 source_unavailable 和校验诊断 invalid_result 不属于 ToolError.code。

`invalid_model_action` 属于模型动作校验/最终结果命名空间；`budget_exhausted` 属于停止原因，不是 ToolError。

### 4.3 最终结果状态

`AnswerResult.status` 只允许：

```text
completed
needs_clarification
insufficient_evidence
execution_error
incomplete
invalid_request
invalid_model_action
```

### 4.4 停止原因

`stop_reason` 描述循环停止原因，例如：

```text
finished_with_evidence
finished_nonfactual
clarification_required
no_supporting_evidence
recoverable_error_exhausted
budget_exhausted
request_rejected
action_rejected
interrupted
execution_failed
```

## 5. 冲突传递闭环

`ConflictAssessment` 是结构化运行产物，不是只写入日志的诊断信息：

```text
AgentState.conflict_assessments: ConflictAssessment[]
AgentRunResult.conflict_assessments: ConflictAssessment[]
AnswerResult.conflict_assessments: ConflictAssessment[]  // 无冲突为空数组
TraceEvent.conflict_assessment_ids: string[]
```

每个未决冲突必须满足：

1. `relation == unresolved_conflict`；
2. `candidate_ids` 和 `evidence_ids` 覆盖冲突双方；
3. 正常业务退出为 needs_clarification，不得 completed；异常/预算退出使用真实状态并保留冲突；
4. `follow_up_question` 说明需要用户确认的范围、时间或适用条件；
5. 不得只选择一个值而不说明另一组证据。

## 6. 状态路径闭合规则

统一路径为：

```text
RECEIVED
  → VALIDATED
  → DECIDING
  → OBSERVING
  → DECIDING
  → RESULT_VALIDATION
  → TERMINATED
```

补充规则：

- invalid_request 从 RECEIVED 直接生成候选，再经过 RESULT_VALIDATION；不进入 VALIDATED；
- 知识快照加载失败在应用服务层形成 `execution_error` 候选，经过 `RESULT_VALIDATION`；
- 所有工具返回（包括 `not_found` 和 `error`）先转换为 Observation；
- 可恢复错误进入 `DECIDING`，不可恢复错误进入 `RESULT_VALIDATION`；
- 任一工具调用前发生预算耗尽时，不调用工具，直接形成 `incomplete` 候选并进入 `RESULT_VALIDATION`；
- 任何路径不得绕过 `RESULT_VALIDATION` 直接进入 `TERMINATED`；
- `OBSERVING` 不要求工具成功，只要求收到并登记结构化 Observation。

## 7. 资料与过滤器口径

- `scope` 在文档元数据和 SearchCandidate 中均为字符串数组；
- `scope_any` 表示候选 scope 与过滤列表至少一个交集；
- `scope_all` 表示过滤列表中的每个值都必须存在于候选 scope；
- `effective_from/effective_to` 为可空 ISO 日期，端点包含；
- 时间有效期缺失时只能表示“未声明”，不能自动推导有效或无效；
- 应用服务默认生成 `status=["active"]`；用户明确询问历史规定时才生成包含 `withdrawn` 的显式时间过滤；
- 模型不得移除系统默认过滤器，也不得依据标题猜测 scope、status 或权威等级。

## 8. 修复后剩余边界

根据本轮新增要求，以下能力不再属于范围外，已在 10 形成详细契约并同步到 01–07：

- 向量数据库、CUDA embedding、混合检索与索引发布；
- 意图/深度识别、浅层快答、深层查询和真实原因兜底；
- 租户级全链路隔离（可信身份、检索、证据、Trace、发布与撤权）。

仍明确不在当前范围：

- 生产级认证中心、租户内逐文档 RBAC 和分布式高可用；
- OCR、MCP、外部网络资料抓取、重排模型、多 Agent；
- 真实业务数据和线上擦除/备份合规证明。

Mock 与真实模型评测不互相替代：Mock 验证编排，真实模型质量仍须单独记录；没有真实运行证据就标 runtime_evidence_pending。

## 9. 要求覆盖与交付状态

> 本节为设计修复完成时的历史快照；实现阶段的当前状态见第 12 节及 `../docs/08-implementation-progress.md`。本节“尚未创建/implementation_pending”不覆盖后续已落盘实现。

核对依据为根目录题目/要求、docs/02/06/07 以及本轮实际文件。下表将资料覆盖、设计契约、代码和运行证据分开；不把设计文档当成实现证据。

| 要求/问题域 | 架构 | 契约/验收定位 | 实现状态 | 运行证据 |
|---|---|---|---|---|
| 输入、历史、空问题 | defined | contracted：02 请求、06 RECEIVED；E6/E12 | implementation_pending | runtime_evidence_pending |
| 六层依赖与横切责任 | defined | contracted：01/05，L4/L5 并列，L2 单一退出 | implementation_pending | runtime_evidence_pending |
| 明确答案 | defined | contracted：ReadEvidence→Claim→Citation；E1 | implementation_pending | runtime_evidence_pending |
| 向量数据库/embedding/混合召回 | defined | contracted：02/07/10；N12/N15 | implementation_pending | runtime_evidence_pending |
| 意图与深度识别 | defined | contracted：02/03/06/10；N01/N05 | implementation_pending | runtime_evidence_pending |
| 多租户全链路隔离 | defined | contracted：02/04/05/07/10；N08–N11 | implementation_pending | runtime_evidence_pending |
| 浅层快答/深层查询/兜底 | defined | contracted：02/03/06/10；N01–N07/N13–N14 | implementation_pending | runtime_evidence_pending |
| 未决冲突/双方引用 | defined | contracted：08 assessment、04 E4 | implementation_pending | runtime_evidence_pending |
| 显式范围/替代关系消歧 | defined | contracted：02 元数据、08 约束；E5 | implementation_pending | runtime_evidence_pending |
| 资料外/证据不足 | defined | contracted：02 最终状态、04 E3 | implementation_pending | runtime_evidence_pending |
| 相似问题/改写检索 | defined | contracted：03 检索策略、04 E2；召回权重/阈值待实现样例确定 | implementation_pending | runtime_evidence_pending |
| 上下文追问/新轮重检索 | defined | contracted：03 多轮、04 E6 | implementation_pending | runtime_evidence_pending |
| 动态模型工具循环/两种路径 | defined | contracted：03、04/08 同问题不同观察 | implementation_pending | runtime_evidence_pending |
| Search/Read/Observation 身份链 | defined | contracted：02/07，ID 不混用 | implementation_pending | runtime_evidence_pending |
| 工具失败/模型失败分类 | defined | contracted：02 映射，04 E7a–d | implementation_pending | runtime_evidence_pending |
| 步数/调用预算与停止 | defined | contracted：03 计数，04 E8 | implementation_pending | runtime_evidence_pending |
| 注入/只读白名单/日志隐私 | defined | contracted：04/07，E9；不声称生产 ACL | implementation_pending | runtime_evidence_pending |
| 快照更新/版本/旧 ID 拒绝 | defined | contracted：02、04 E10/E11 | implementation_pending | runtime_evidence_pending |
| Trace 可复核而非预写日志 | defined | contracted：02 事件及来源字段、04 差异路径 | implementation_pending | runtime_evidence_pending |
| 至少三组输入/实际输出/Trace | defined | contracted：04 样例分组与验收矩阵 | examples/ 尚不存在 | runtime_evidence_pending |
| 可复制运行、依赖与离线入口 | defined | contracted：01/05 CLI/Mock 范围；实现后才能给真实命令 | src/、依赖文件、.env.example 尚不存在 | runtime_evidence_pending |
| 可执行测试与效果评价 | defined | contracted：04 分层指标/硬失败；03-evaluation 待创建 | tests/ 尚不存在 | runtime_evidence_pending |
| AI 使用、个人贡献、实际用时、复盘 | defined：交付责任，不是业务层 | 要求明确；须由实际工作记录填写，不能代用户编造贡献 | docs/04-ai-usage.md、05-retrospective.md 待创建 | runtime_evidence_pending |
| 需求与 GitHub 交付 | defined：不在本轮推送范围 | 题号、URL、最终 SHA、私库可读须交付前核对 | docs/01-requirements.md 尚未创建；未提交/推送 | runtime_evidence_pending |

目前磁盘上有工作区 README、三份基线文档和本目录 README/01–10；根目录题目/要求保留。架构文档 01–10 的编号与交付 docs/01–05 不是同一套编号，不得把前者冒充后者。提交区不在本轮修改范围。

## 10. R01–R12 历史修复复核记录

本轮只做文档级复核：逐项核对 R01–R12、检查旧工具签名/状态残留、核对完整 JSON 示例与本地文档引用。没有运行业务测试、请求外部模型、安装依赖或产生真实 examples；因此本文件不宣称真实模型效果、业务代码或项目最终交付已经通过验收。

2026-10-28 文档复核结果：读取架构目录及三份基线共 13 份 Markdown；11 个 JSON 示例均可解析，其中 2 个完整 ToolResult 示例包含请求/调用/快照标识，Search 不产生引用证据；13 处本地 Markdown 链接目标均存在；未发现扫描范围内的旧可执行接口示例，问题数为 0。该检查只证明上述格式/引用和指定残留项，不证明业务语义或代码运行效果。实际目录核对确认 src/tests/examples/data、依赖文件、.env.example 及交付 docs/01/03/04/05 仍未创建，与第 9 节待办一致。

必要取舍：

- 未决冲突禁止 completed，但不能掩盖 provider/预算等真实退出原因；
- 未知 candidate 是可观察的 not_found，旧 evidence 进入最终 Claim 是结果契约错误；两者不强行映射成同一个状态；
- 不为旧字段设置兼容层，旧示例直接移除；
- 问答模型仍只保留 Search/Read 两项工具；向量、意图、租户和三路回复作为基础设施/路由必做，但不添加未要求的生产认证中心、重排、多 Agent 或外部网络工具。

## 11. 2026-10-28 新范围同步记录

本次范围增补以用户明确要求为准：向量数据库、意图识别、多租户隔离、浅层快速回复、深层查询回复、兜底回复均是当前设计的必做项，不再作为未来扩展排除。

- 10 是六项能力的专题详细设计，01–08、架构目录 README、工作区 README 和 docs/02/06/07 同步引用；字段与枚举的规范入口仍为 02。
- 浅层回复拆分为纯非事实模板和有证据的事实 FAQ；后者仍走 Search → Read → Finish。深层问题走动态查询循环；兜底仅表达真实停止原因，不新增成功状态。
- 租户由可信入口绑定，向量、词项、原文、FAQ、候选、证据、冲突、Trace 和输出均保持同一租户边界；权限不能由模型或问题文本修改。
- Qdrant local、CUDA embedding、混合召回、分类预算、快转深预算延续及知识发布生命周期均已纳入设计，相关实现仍标记为 `implementation_pending`。
- 本次未安装依赖、下载模型或运行业务代码；本机 CUDA、向量检索质量、分类质量、两租户隔离和完整回答链路的运行证据均为 `runtime_evidence_pending`。
- 第 10 节保留原 R01–R12 复核的历史统计，不将其改写为新增范围的运行验收结果；提交区不在本次修改范围。

### 11.1 新范围一致性收敛与文档复核

本次进一步统一了以下口径：

- `finished_nonfactual` 与新增工具错误码在审查记录中同步；SearchCandidate 的完整字段只维护在 02 第 6.1 节，分支 rank/score 无命中时为 null。
- `load_snapshot(tenant_context)` 明确只绑定可信租户 ACTIVE 快照；纯非事实模板和前置拒绝不加载知识，存储路径不能由模型指定。
- IntentAssessment 的关联 ID 由运行时分配；Trace 分类、工具返回及终止事件记录实际模型/工具/embedding 调用计数。
- 04 第 5.1 节逐项关联 N01–N15 与主验收项，长 section、chunk、索引配置不匹配和 fixture 边界纳入 E21；不再新增独立验收编号。

文档复核结果：架构目录 README/01–10 共 11 份 Markdown 齐备；11 个 JSON fenced examples 解析错误为 0，本地 Markdown 链接目标检查错误为 0；针对排除向量、租户或将检索限为词项的旧范围口径，未发现规范性残留。这些仅为文件、格式、链接和指定口径的检查，不证明设计没有其他遗漏，更不证明业务实现、CUDA 或租户运行验收通过。

## 12. 实现阶段同步（2026-10-28）

本节记录第 9 节之后实际落盘的代码，不改写历史复核结论。

| 能力 | 代码状态 | 运行证据 |
|---|---|---|
| 契约、身份、预算、Trace | `implemented_locally` | 已有局部测试 |
| 离线知识、Search/Read、Evidence/Citation | `implemented_locally` | 知识路径测试、`examples/01-normal` |
| 意图路由、快答/深查/兜底、Mock 循环 | `implemented_locally` | 路由/结果测试、七组 Trace；非真实模型效果 |
| 租户/请求/快照绑定与撤权 | `implemented_locally` | 基础测试、CLI 越权拒绝、B 租户场景；非生产认证 |
| FAQ 配置与冲突处理 | `implemented_locally` | `examples/02-conflict`，双方引用 |
| 工具错误分类 | `implemented_locally` | `examples/03-tool-failure`，不伪装成无命中 |
| 摄取/发布/重载和 hybrid 接线 | `implemented_locally` | 两项替身测试，失败保留旧 ACTIVE |
| Qdrant/CUDA/真实 provider | 适配器代码已写 | `runtime_evidence_pending`，未使用真实后端 |
| 删除/擦除与未覆盖设计场景 | 部分代码/契约 | `implementation_pending + runtime_evidence_pending` |
| 交付 docs/01/03/04/05 和 examples | 已创建 | 主样例有实际输出和完整 Trace |

最近一次局部测试为 `32 tests / OK`；完整明细和限制以 `../docs/03-evaluation.md` 为准。尚未安装依赖、下载模型、检查 GPU、调用外部业务模型、提交或推送 Git；提交区保持不变。显式条件、历史追问、覆盖校验及擦除续清理的本轮修复见 `../docs/09-architecture-implementation-completion.md`。

