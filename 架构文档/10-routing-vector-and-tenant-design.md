# 10 向量检索、意图路由与多租户设计

## 1. 本轮范围变更

根据用户新增要求，以下能力从“首版范围外”调整为当前设计必须覆盖的交付范围：

- 向量数据库和语义检索；
- 意图识别及浅层/深层问题分流；
- 多租户数据、检索、证据和运行记录隔离；
- 浅层问题快速回复、深层问题查询后回复、无法安全回答时的兜底回复。

本轮先同步架构、接口、状态机、逐层/逐工具设计及验收矩阵，不把设计修改表述为代码已经实现。

## 2. 不可退让的边界

- 快速回复不等于绕过事实证据：制度、产品规则等事实性结论仍须来自当前租户可回查的知识来源。
- 租户身份只能由可信入口确定，不能由模型动作或用户问题中的 tenant_id 切换。
- 快速路径、深度查询和兜底路径共用结果契约；不能把检索失败、执行错误或权限拒绝伪装成正常答案。
- 旧版“无需向量数据库/意图识别/多租户”的范围说明需要同步修改，避免新旧文档互相覆盖。

## 3. 修订落点

本文件用于细化分流、向量索引和租户上下文；01–08 同步更新对应职责、字段、状态、工具和验收，09 保留上一轮修复历史并登记本轮范围变化。

## 4. 本轮执行与完成口径

成功标准：六项新增必需能力均落实到层级职责、输入输出、状态迁移、工具权限、失败处理及可检查的验收场景，并清除现行设计中与新范围冲突的表述。

工作顺序：核对题目、要求与 02/06/07 基线；完成本专题设计；同步关联文档；进行一次必要的范围与契约一致性复核。所有变更仅在工作区开展，提交区不自动覆盖。设计与运行验证分开记录，当前不得据此声称业务能力已实现。

## 5. 方案选择与分层

本项目选择 **Qdrant 本地持久化 + CUDA embedding + 词项/向量混合召回 + 单 Agent 三路响应**，不增加独立路由 Agent、生产认证平台或写入型业务工具。

| 方案 | 取舍 | 本轮决定 |
|---|---|---|
| 只有词项检索 | 依赖少，但不能满足当前向量数据库必做要求 | 只作为混合召回分支和对照，不作为完整交付 |
| Qdrant local + 本地原文快照 | 无需部署数据库服务，适合小型合成数据；本地目录不能视为多用户安全边界 | 当前采用，限定单进程、受信任运行账户 |
| PostgreSQL/pgvector 或 Qdrant server | 更适合已有数据库/服务部署，但本项目尚无相应基础设施 | 不同时引入；未来迁移不改变 Search/Read 契约 |

Qdrant 官方 client 提供本地持久化模式；官方多租户资料给出 payload 分区过滤方式。**本项目的租户认证、强制过滤和证据授权仍由应用代码负责，不把向量库的 tenant 字段当作自动认证。**来源见第 15 节 S1–S3。

| 层 | 新增职责 | 边界 |
|---|---|---|
| L1 交互 | 接收受信任启动配置对应的身份，显示路径、状态与缺口 | 问题/历史中的 tenant_id 不改变身份 |
| L2 应用服务 | 解析 TenantContext、授权、编排路由准备和快照获取、统一出口 | 不分类业务事实、不直接调用检索 |
| L3 Agent 编排 | 意图/深度路由、快转深、受限查询循环、兜底候选 | 路由不是最终状态，不重置预算 |
| L4 模型适配 | IntentClassifier、DecisionModel、EmbeddingModel 三种接口 | 分类器不执行工具；embedding 不生成答案 |
| L5 工具执行 | 两个只读工具；注入租户/快照约束；调用 embedding 接口取得查询向量 | 不接受模型指定租户、collection、路径 |
| L6 知识与证据 | 摄取、chunk、索引、混合排序、FAQ 目录、原文、证据/冲突 | 只接收向量数据，不调用模型 SDK |

L4 与 L5 仍为并列组件；L5 可依赖独立 `EmbeddingModel` 协议适配器提供的查询向量，不调用 `DecisionModel` 或 `IntentClassifier`。L6 不调用 L4。摄取由 L2 管理入口协调 L4 编码和 L6 存储，不是问答模型动作。

## 6. 多租户上下文与授权

### 6.1 信任来源和拒绝行为

`TenantContext` 由 L2 身份解析器创建，字段规范集中在 02 第 11 节。首版保留 CLI：进程启动时，运维配置把受信任 OS 运行账户映射到固定 `principal_id/tenant_id/roles`，用户问题 JSON 不接收可写租户字段。验收用两个明确标记的合成身份配置模拟 tenant A/B，不能宣传为真实认证效果。

同一 OS 账户能编辑配置或访问数据库目录时属于受信任运维方；本地 CLI 不防御本机管理员，也不对外暴露共享服务。若接 HTTP，必须先增加认证中间件，将已校验的身份传入 L2；不可直接相信 header、body 或模型中的 tenant_id。当前不假设 HTTP 已实现。

- 未绑定/失效身份、无当前租户成员资格：`invalid_request / access_denied / request_rejected`，不加载知识、不调模型/工具，外部不说明其他租户是否存在。
- 权限为 `knowledge_reader`、`knowledge_editor`；editor 摄取入口须显式持有写权限，reader 不能借问答执行写入。
- 首版隔离单位是整个租户；不混用 `scope`（制度适用范围）与访问权限，不实现租户内部逐文档 RBAC，也不开放跨租户公共知识池。
- 模型上下文不需要真实 principal_id；由运行时保存身份绑定，必要时只给出“当前授权知识域”。

### 6.2 全链路隔离点

| 对象/操作 | 强制约束 | 拒绝/失败 |
|---|---|---|
| 原文、FAQ、词项索引 | 按 tenant + snapshot 取仓库；ID 唯一性在租户内 | 不跨租户补资料 |
| Qdrant 查询 | 执行前注入 `tenant_id AND snapshot_id AND 应用过滤 AND 已确认动作过滤` | tenant 条件不可移除，禁止全库召回后过滤 |
| 摄取/更新/删除 | 写上下文决定 tenant；源资料 tenant 与写上下文一致 | 不匹配直接拒绝，不能按正文改目标 |
| Candidate/Read/Evidence/Conflict | 绑定 tenant + request + snapshot | 外租户 candidate 返回 not_found；外租户 evidence 拒绝输出 |
| 对话历史 | 由调用方在当前身份域内提供，仅作为不可信文本 | 任意旧答案、外租户 ID 不获得证据授权 |
| 缓存 | 首版不缓存答案、查询或跨请求证据；只复用公开模型权重 | 未来缓存至少以 tenant/snapshot/model/config/授权版本分区 |
| Trace/结果文件 | 目录由受信任 tenant 映射与 request ID 生成；不以用户字符串拼路径 | 读取/导出仍校验身份；不返回全局 Trace 清单 |

数据库返回外租户 point 是内部完整性错误：在生成 snippet、Candidate 或模型上下文前阻断，`execution_error / invalid_result`；不能删除违规项后冒充成功。共享向量库不意味着共享检索结果，词项分数也只基于当前租户快照计算。

## 7. 意图识别与深度判定

### 7.1 分类维度

意图与复杂度分开：`intent=greeting | capability | knowledge_question | out_of_scope | unknown`；`depth=shallow | deep | uncertain`。FAQ 是知识问题的处理方式，不是独立权限或事实来源。

| 输入特征 | 路由决策 | 不能据此推定 |
|---|---|---|
| 全句只是问候/感谢，或系统能力说明请求 | fast_nonfactual，固定模板 | 夹带制度问题的“你好，报销几天”不是纯闲聊 |
| 当前租户 FAQ 的标准问句/显式别名精确命中，单事实、条件齐全 | fast_faq，仍走 Search→Read→Finish | 高相似度不自动获得 FAQ 快答资格 |
| 比较、多条件、跨文件、版本冲突、依赖上下文、改写问题 | deep | 短句不等于浅层，长句不一定需要多步 |
| 合法分类但 uncertain/unknown，有知识问题线索 | deep | 不能凭低置信度生成制度答案 |
| 语义不完整且用户能补足对象 | fallback，澄清 | 不猜用户意图 |
| 确定为非企业知识任务 | fallback，范围说明 | 不调用网络搜索补足题外能力 |

先应用全句匹配的非事实模板规则；其他请求在 VALIDATED 阶段由 L3 调用 L4 `classify(context)` 一次。分类结果只含标签、缺失条件和简短依据，不含最终答案或工具指令；L3 根据合法标签和当前 FAQ 目录决定 RouteDecision。Mock 分类必须标记 mock_policy，不能伪装真实意图模型。

fast_nonfactual 的 completed 资格只来自全句白名单规则；分类模型单独声称 greeting/capability 而未命中白名单时，可返回固定能力边界兜底或继续澄清，但不能生成任意 completed 答案。带知识诉求或不能排除知识诉求时进入 deep_query。topic_complete 来自编辑发布的显式来源组，新增同 topic 资料必须同步扩充来源组，否则发布校验失败；未编目自然语言资料不自动获得“全库无冲突”的保证。

`confidence` 只用于分析，首版不以未经校准的数值阈值开通事实快答；`uncertain` 走深查/澄清。格式不合法为 invalid_model_action，分类器超时/不可达为 execution_error，均生成固定失败说明，不静默假装已分类。

### 7.2 上下文、提示词和预算

分类器只读取当前问题及受限历史，历史不能改身份。分类提示采用“冻结标签/安全约束/结构化样例前缀 + 当前问题/历史后缀”；与决策提示分别版本化，不保存思维链。`normalized_question` 补指代但不增添未确认条件。

`model_steps_used` 包括分类与动作决策；分类调用前检查并计数。固定规则分类不计模型步数，但写 runtime 来源。默认总上限仍为 6 次模型调用、4 次业务工具调用；快转深共用计数，不重新开一轮预算。embedding 不是决策模型步数，另计 `embedding_calls_used`：每个 Search 至多一次 query 编码且不得超过 max_tool_calls；摄取编码不属于问答请求。

## 8. 三类回复路径

### 8.1 浅层快速回复

**非事实快答**：问候、能力说明用白名单固定模板；`answer_kind=nonfactual`，claims/citations/assessments 为空，status=completed，stop_reason=finished_nonfactual。不加载知识快照、不调用 embedding/工具/决策模型，snapshot_id 可为 null；不能在模板中提供报销天数等制度事实。

**FAQ 快答**：先在当前租户快照的 FAQ 目录匹配标准问句/别名。目录只保存问题、来源指针和 topic 组，不保存可直接输出的最终答案。候选 FAQ 不等于 Evidence；模型仍须选择 search_documents、read_document、finish，保留模型观察循环。

- Search 合并向量/词项候选和命中的 FAQ topic 来源组，不能以 FAQ 精确命中强制 limit=1 排除其他版本。
- 快答资格要求 topic 来源清单在当前发布快照中完整、只有一个适用 section，且用户条件齐全；该完整性是编辑发布约束，不宣称自动发现了全部潜在自然语言冲突。
- 模型读到缺少条件、其他适用来源、截断候选或疑似冲突时，转 deep；不直接从 snippet 回答。
- 理想路径是 1 Search + 1 Read + 1 Finish；这是目标而非强制脚本。不为快答增加自动预读或答案缓存。
- 快答预期更少调用，但不在无运行数据时宣称毫秒级性能或降低引用标准。

### 8.2 深层问题查询回复

```text
完成身份/分类/快照准备
  → 模型 Search（必要时改写 query）
  → OBSERVING（候选、排序、覆盖信息）
  → 模型选择 Read 原文
  → OBSERVING（ReadEvidence + ConflictAssessment）
  → 模型补检索/读其他来源/澄清/Finish
  → L2 统一组装与校验
```

多子问题可由模型在 query 和缺口中拆分，但每次动作仍只执行一个现有工具，不引入并行 Agent 或隐藏多轮检索。深查也必须完成证据核验；不是“搜索一下后让模型自由发挥”。

### 8.3 兜底回复

兜底是**真实原因的受约束表达**，不是统一业务状态，也不是另一次无预算的模型调用。保留 attempted_route，effective_route=fallback；在已有执行错误、预算退出或资料不足等真实结果上，用固定说明和结构化 gaps/follow_up 展示，不覆盖真实状态、不丢弃有效引用。

| 原因 | status / error_code / stop_reason | 回复要求 |
|---|---|---|
| 缺对象、范围、参考时间 | needs_clarification / null / clarification_required | 询问最小条件 |
| 已读资料冲突未决 | needs_clarification / null / clarification_required | 保留双方原文/assessment，不选胜者 |
| 正常查询后没有支持证据、题外任务 | insufficient_evidence / null / no_supporting_evidence | 明确资料或能力边界，不虚构人工客服联系方式 |
| 身份无效/无权限 | invalid_request / access_denied / request_rejected | 通用拒绝，不泄漏其他租户信息 |
| 分类/embedding/向量查询/读取失败 | execution_error / 实际诊断码 / execution_failed | 明确未能完成；错误不是无资料 |
| 模型协议/动作/引用错误 | invalid_model_action / 实际诊断码 / action_rejected | 不输出不可信结论 |
| 预算耗尽/中断 | incomplete / null / budget_exhausted 或 interrupted | 保留可用证据、列未完成部分 |

错误摘要不包含原始异常路径、连接凭据或外租户 ID。模型已经提交的无效答案不能通过“加一句仅供参考”变成合法兜底。

## 9. 向量库与 embedding 详细设计

### 9.1 存储结构

Qdrant 本地持久化承载 dense vectors；原文保存在 L6 不可变知识包，向量 payload 不作为最终 quote 来源。首版共享一个固定 collection `enterprise_knowledge_bge_zh_v1_5`，tenant/snapshot 双重过滤；collection 名不来自模型。每个 point 使用摄取器生成的 UUID，不能直接用可冲突的 D01。

collection 固定 512 维和 cosine；未来更改维度要新建固定配置 collection 并重建快照，不能向旧 collection 混写不兼容向量。revision 变更也必须重新构建完整索引。日期/业务过滤的数据库转换规范见 02 第 11.4 节。

规范对象 `KnowledgeChunk` 与 manifest 字段见 02 第 11 节。payload 最少含 tenant_id、snapshot_id、document_id、version_id、section_id、chunk_id、char_start/char_end、scope、status；日期等过滤由 L6 依据规范 schema 转换，不直接把任意模型 JSON 发给数据库。

本地模式不依赖 HNSW、payload index 性能承诺或分片功能；服务端的 is_tenant 优化不是授权替代品。只在未来 server 部署时配置相应索引，不为本地原型制造集群。

### 9.2 编码与切片

项目选用 `BAAI/bge-small-zh-v1.5`，输出 512 维，cosine 距离；通过 Sentence Transformers 的 torch backend 显式使用 `device="cuda"`、`normalize_embeddings=True`。官方能力依据见 S4/S5；这是中文合成制度项目的初始选择，不代表已在本项目证明最佳效果。

- 查询前缀固定为 `为这个句子生成表示以用于检索相关文章：`；文档不加查询指令。前缀和模型 revision 一起写入索引配置。
- 文档编码输入为 title + heading + 原文 chunk；按实际 tokenizer 计数，连同 special tokens 不超过模型上限。初始正文窗口 320 tokens、重叠 48 tokens，标题过长时缩短正文窗口；禁止无声截断原文。
- 切片不跨 section/版本/租户，记录原文字符区间；chunk 只用于召回，Read 仍返回完整 section。
- 未安装权重、CUDA 不可用或显存不足时报 `embedding_unavailable`；不静默切 CPU、不生成随机向量冒充语义检索。显存不足时由操作者显式调整摄取批量后重跑。
- 离线 Mock 模式可用明确标记的 fixture vectors 验证流程与租户过滤，不证明向量召回质量。真实 embedding 权重下载完成后可离线 CUDA 编码；不能混用 fixture 与真实索引。
- 依赖及模型的实际版本在实现首次安装时锁定；本轮未安装、未下载，不虚构已验证版本组合。

### 9.3 混合召回

1. L5 将相同 tenant/snapshot/显式条件用于所有分支；获取一次查询 embedding。
2. L6 分别取向量 Top-10、租户内词项 Top-10；FAQ 命中时加入完整 topic 来源组。
3. 先按 document/version/section 聚合 chunk；同一 section 只占一个候选名额，保留各分支最佳 rank/score。
4. 项目使用等权 rank 融合：`retrieval_score = Σ 1/(60 + rank_branch)`；rank 从 1 开始，无该分支命中则贡献 0。FAQ 来源组为覆盖补充，不人为赋权威分。
5. 默认输出 Top-5；分数相同按 document_id/version_id/section_id 稳定排序，不因同文重复片段吞掉其他版本。
6. 返回 `coverage={candidate_truncated, topic_complete}`；topic_complete 仅对已声明且发布验证完整的 FAQ topic 为 true，其他为 false。来源组超 limit 时保留截断标记，快答转 deep，不宣称检索覆盖全库。
7. 高分不是充分证据，首版不伪造统一“可回答阈值”；合法检索无候选才 no_evidence，有不相关候选则读后按不足结束。

向量或 embedding 分支失败时整个 Search 返回 error，不在同一次调用中偷偷只用词项回答。词项-only 实验是显式对照配置，必须标记检索模式，不算完成向量能力验收。

## 10. 摄取、发布、更新与删除

管理入口是 L2 的 `ingest/publish/delete` 应用服务，不在模型白名单中。首版串行执行，不建设异步队列：

```text
校验 editor 身份与 tenant
  → 读取该租户合成资料，校验版本/日期/section/FAQ 来源清单
  → 创建新 snapshot_id，状态 BUILDING
  → 生成原文包、词项索引、chunk
  → L4 CUDA 编码，L6 写入该 tenant + 新 snapshot 的 Qdrant points
  → 核对来源映射、向量维度及入库数量，READY
  → 应用控制的单个 active manifest 引用切换，ACTIVE
  → 旧版 RETIRED；无正在使用的请求后清理派生索引与原文包
```

BUILDING/READY/ACTIVE/RETIRED/FAILED 是**知识发布状态**，不是 Agent 生命周期或回答 status。构建失败为 FAILED，旧 ACTIVE 不变；没有旧 ACTIVE 则知识查询明确失败，不读半成品。Qdrant、原文和词项无需跨存储分布式事务：只有全部就绪才能发布引用；请求固定该引用，不分别读取“最新”的组件。

初版采用每租户完整重建，数据小，不做增量复用。更新/删除创建新快照，新请求只能读新 ACTIVE；旧请求在普通内容更新时可完成原快照。删除含敏感内容/撤权时，先禁用该租户旧快照的新读写和结果输出，受影响在途请求以 execution_error/access_denied 退出，再删除原文、向量、FAQ/词项和有关 Trace；不承诺仅向量删除就已擦除全部副本。结果输出前 L2 复核上下文 authz_version，已撤权则拒绝输出既有正文/引文。

embedding revision、维度、指令或切片规则变更必须重建，不能只更新 manifest 标签。首版单进程同一时间只允许一次发布，读取引用切换是本地应用原子动作；多进程并发、跨节点强一致及备份擦除策略不在当前部署承诺内。

当前擦除用 `blocked.json` 累积旧快照禁用记录，并以 `erase.json` 保存原快照、目标文档、待清理旧快照和替代快照 ID。旧 READY 也禁止再次发布；仅允许指定替代快照发布。清理失败保留进度，下一次同文档 `delete --erase` 可续做。替代包发布后可供新请求读取；旧包/向量/默认 Trace 物理清理尚未完成时不得报告擦除完成。临时包文件也随旧包清理；外部 Trace 副本和备份不属于当前自动擦除范围。

## 11. 生命周期与路由位置

不增加 ROUTING/FAST_REPLY 等生命周期枚举；RouteDecision 是 VALIDATED 的产物，不是与状态机竞争的第二套状态。

| 生命周期 | 新增行为 | 出口 |
|---|---|---|
| RECEIVED | 输入/可信身份校验；未授权不碰知识 | VALIDATED 或统一拒绝结果 |
| VALIDATED | 建立带 tenant 的 State；规则/一次模型分类；非事实模板可无快照；知识路径绑定一次快照与 FAQ 资格 | 快答非事实/澄清/错误→RESULT_VALIDATION；知识→DECIDING |
| DECIDING | 模型依据路径和 Observation 选择现有动作；保留全局预算 | 工具→OBSERVING；候选/错误→RESULT_VALIDATION |
| OBSERVING | 成功/失败都登记；FAQ 无法满足快答条件时记录 route_changed 事件，effective_route=deep_query | DECIDING 或不可恢复错误→RESULT_VALIDATION |
| RESULT_VALIDATION | 校验租户、授权版本、request/snapshot/证据及 answer_kind；应用真实状态兜底 | TERMINATED |
| TERMINATED | 输出有效结果和对应 Trace，不再次调用模型 | 结束 |

路由失败、授权失败、非事实快答均不能绕过 L2 统一出口。只有纯非事实快答/前置兜底允许 snapshot_id=null；任何 Search/Read 或知识事实结果必须有当前租户快照。

## 12. 工具与内部接口

模型工具仍然只有 Search/Read；下列为非模型工具接口，详细字段集中在 02：

| 接口 | 所属 | 输入/输出 | 失败语义 |
|---|---|---|---|
| resolve_identity | L2 | 可信启动上下文→TenantContext | access_denied，不调模型 |
| classify | L4，由 L3 调用 | RouteContext→IntentAssessment | provider 错误/协议错误按原契约退出 |
| embed_query/embed_documents | L4，L5/摄取入口协调 | 文本/固定配置→vectors + model revision | embedding_unavailable |
| hybrid_search | L6 | query vector、tenant/snapshot、已合并过滤→SearchResult | vector_store_error/index_version_mismatch |
| ingest/publish/delete | L2 管理入口 | editor context + 合成数据→发布结果 | 未授权拒绝；失败不发布 |

不得新增 `quick_answer` 直接输出制度、`query_any_tenant`、任意数据库 SQL 或模型可调用的索引删除工具。

## 13. 观测、验收与交付材料

Trace 新增 intent_assessed、route_selected、route_changed、fallback_selected、identity_rejected 事件；字段、来源及预算规范见 02 第 11 节。公开 examples 只用 A/B 合成租户、合成身份；不可泄漏真实 principal、绝对存储路径、凭据或未授权内容。

| 编号 | 场景 | 可检查预期 |
|---|---|---|
| N01 | 纯问候；“你好，报销期限多久” | 前者非事实快答零工具；后者必须进入知识路径 |
| N02 | 标准 FAQ，来源唯一且条件齐全 | fast_faq，Search→Read→Finish，当前租户引用 |
| N03 | FAQ 同 topic 出现 30/15 天；或候选截断 | 升 deep，共享预算，不按 Top-1 答复；冲突双边引用 |
| N04 | 改写问句/跨制度比较/上下文追问 | deep，保留动态工具观察循环 |
| N05 | 合法 uncertain 分类与不完整提问 | deep 或澄清，不因低置信度直接答事实 |
| N06 | 资料外问题/无支持证据 | fallback + insufficient_evidence，不能伪造来源 |
| N07 | 分类器/embedding/向量库失败 | 各自真实错误；不改成无命中、不静默词项或 Mock 降级 |
| N08 | A/B 都有 D01，但答案和原文不同 | 各自只见自身 Candidate/Read/引用/Trace；词项与向量都隔离 |
| N09 | 外租户 ID、问题中换 tenant、伪造 history | 身份不变；Read not_found，非法最终 evidence 被拒绝 |
| N10 | reader 试写、源资料伪造 tenant、库返回污染 point | 拒绝写入或阻断输出，不将污染内容发给模型 |
| N11 | 更新/删除/构建失败/在途撤权 | 新请求看新 ACTIVE；失败不发布；撤权不输出已有引文 |
| N12 | 维度/revision 不匹配、长 section、多 chunk | 不混索引，不静默截断，Read 精确回查 section |
| N13 | 快转深后耗尽预算 | incomplete，分类已计数，不重置计数 |
| N14 | 非事实/知识/拒绝/异常所有分支 | 同一 ResultValidator，status、route、answer_kind 不混用 |
| N15 | CPU-only 或 fixture 模式 | 真实编码明确报 CUDA 不可用；fixture 只证明流程 |

上述场景并入 04 验收，逐项映射见 04 第 5.1 节，不另造重复测试系统。后续记录分层召回、分类混淆/事实误快答率、快答升级率、证据支持度、租户泄漏数、三路调用数和耗时；只记录实测值，不设伪造达标率。事实误快答、跨租户内容输出和错误伪装成功均为不可抵消失败。

## 14. 本轮状态与后续实现顺序

| 能力 | 设计 | 实现 | 运行证据 |
|---|---|---|---|
| 向量数据库/摄取发布/混合召回 | defined + contracted | 适配器/管理入口已写，真实接入待验证 | 摄取/发布/hybrid 仅有替身证据；真实运行 runtime_evidence_pending |
| 意图与深度识别 | defined + contracted | implemented_locally：规则 Mock + provider 适配器 | 离线路由有局部证据，真实分类 runtime_evidence_pending |
| 多租户全链路隔离 | defined + contracted | implemented_locally：身份、候选、证据、输出等绑定 | 部分离线证据；真实向量及删除/擦除 runtime_evidence_pending |
| 浅层快答/深层查询/兜底 | defined + contracted | implemented_locally：Mock 循环与统一结果 | 已有合成场景记录；真实模型质量 runtime_evidence_pending |

依赖顺序：身份/数据契约 → 原文与向量索引摄取 → 租户内 Search/Read → 意图路由与三路策略 → 统一结果/Trace → 真实样例验证。此顺序不允许“先用全库检索，最后再补租户过滤”。

设计阶段未执行代码验收；后续实现阶段已有 32 项局部测试与七组 Mock 场景记录，见 `../docs/03-evaluation.md`。没有安装依赖、调用外部业务 LLM 或下载模型；提交区保持不变。原考核的真实模型动态决策效果仍待证明，不能以规则 Mock、问候快答或固定兜底替代。显式条件入口已接线，自然语言条件自动确认仍未实现。

## 15. 一手资料登记（2026-10-28）

下列来源核对官方支持的基础能力，不构成本项目运行证明；架构、参数初值和隔离策略是本项目决策。

本轮检索工具未返回可读取正文，改用只读 HTTP 获取官方 client README、Qdrant 两页官方文档、BAAI 模型卡和 Sentence Transformers API，均得到 HTTP 200；读取到 local 持久化示例、租户分区及模型使用说明。这里只确认文档能力，不确认本机库版本、CUDA 可用性或本地运行表现。

| 来源 | 官方地址 | 核对内容/使用边界 |
|---|---|---|
| S1 Qdrant Python client | `https://github.com/qdrant/qdrant-client` | local 持久化模式适合小型原型，不需数据库服务 |
| S2 Qdrant multitenancy | `https://qdrant.tech/documentation/manage-data/multitenancy/` | payload tenant 分区；不把过滤当身份认证 |
| S3 Qdrant filtering | `https://qdrant.tech/documentation/search/filtering/` | 强制条件组合；项目转换过滤字段而非透传任意 JSON |
| S4 BAAI 模型卡 | `https://huggingface.co/BAAI/bge-small-zh-v1.5` | 中文 embedding、512 维、查询指令；不直接引用外部榜单证明本项目效果 |
| S5 Sentence Transformers API | `https://www.sbert.net/docs/package_reference/sentence_transformer/model.html` | 显式 CUDA、归一化和 revision 配置；不声称已验证本机环境 |
