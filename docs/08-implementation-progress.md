# 分阶段实现与局部验证记录

> 历史开发记录：下文“只在工作区”“提交区未修改”等约束反映当时阶段，不描述
> 当前交付状态。现行方案、运行结果和提交信息以 docs/02、03、12 为准。

## 实现约束

- 依据架构文档 01–08/10 顺序实现，仅在工作区修改，不同步提交区。
- 用户允许只做部分测试，不要求最终端到端跑通；局部通过不代表真实模型、真实资料或整体验收通过。
- 不调用外部业务 LLM，不下载模型，不自动安装依赖；真实 embedding 只允许 CUDA，缺失时明确报错。
- 合成 fixture 只用于局部验证，不作为真实制度资料或真实向量效果的证明。
- 不创建新 Git 仓库、不提交或推送；直接使用既有工作区。

## 顺序与文件职责

1. **领域契约与身份**：`src/knowledge_agent/contracts.py`、`identity.py`、`runtime.py`；标准库 dataclass/enum，可信身份映射、调用预算和 Trace。
2. **知识与证据**：`knowledge.py`、`retrieval.py`；租户快照、版本/章节定位、候选注册、ReadEvidence 和显式冲突分析。
3. **向量与编码适配**：`embedding.py`、`vector_store.py`、`ingestion.py`；延迟导入可选依赖、CUDA 编码、Qdrant local、串行全量发布及失败保留旧版。
4. **模型与路由**：`models.py`、`routing.py`；结构化分类/动作协议、离线策略、可选 provider 适配、非事实/FAQ/深查/兜底。
5. **工具、应用与 CLI**：`tools.py`、`results.py`、`service.py`、`cli.py`；Search/Read 白名单、观察循环、统一结果校验和 JSON 输出。
6. **使用说明与交付状态**：工作区 README、依赖配置、本记录和架构目录状态说明；区分已写代码、局部验证、尚未验证。

## 局部验证范围

- 第一组：可信租户身份、预算延续、跨租户/跨请求候选和证据拒绝。
- 第二组：合成知识下的 FAQ 证据路径、冲突转深查、无证据兜底及执行失败不伪装成无命中。
- 不要求跑完整 CLI 链路、不做真实 provider 请求或真实 CUDA/Qdrant 集成验收。
- 使用标准库 unittest，无须为测试额外安装依赖；每组只运行必要的小集合，发现实际失败时修复相应问题。

## 状态

2026-10-28：基础契约、知识证据链、路由/工具循环、CLI 与可选适配器代码已落盘。此前记录的 18 项测试仅对应适配器加入前的代码，不代表当前版本验证结果。

### 阶段 1–2：契约、身份与知识证据

- 已实现 `TenantContext`、`KnowledgeSnapshot`、SearchCandidate、ReadEvidence、ConflictAssessment、Result 与工具契约。
- 已实现租户/请求/快照绑定、授权撤销、有限预算、JSONL 原文和词项 fixture 召回。
- 已实现 Search→Read→Evidence→Citation，未知候选不回退到整篇读取，冲突保留双方引用。

### 阶段 3–4：路由、循环、结果与 CLI

- 已实现非事实模板、FAQ 快答、深查与真实原因兜底；分类与动作共用模型预算。
- 已实现 Observation 驱动的 OfflineModel 循环、Search/Read 白名单、统一结果组装与引用回查。
- CLI 已接入可信身份配置、FAQ 配置、可选历史文件和 `fixture/hybrid` 显式检索选择；`--tenant` 不再自行建立成员资格。
- 纯问候与身份拒绝不加载 fixture；模型模式不自动退回 Mock。

### 阶段 5：可选适配器与管理入口

- 已写 CUDA-only BGE、Qdrant local、hybrid/RRF、HTTPS JSON provider；不安装依赖、不下载权重。
- 新增 `admin.py` 的 `ingest/publish/delete` 入口，模型仍只能使用 Search/Read。
- 摄取→READY→发布→重载→hybrid 查询及擦除失败后的续清理使用替身做了局部验证；真实 CUDA/Qdrant/provider、跨进程删除和管理 CLI 本身尚未运行。

### 阶段 6：材料和验证状态

- 补齐 `docs/01-requirements.md`、`03-evaluation.md`、`04-ai-usage.md`、`05-retrospective.md`。
- 七组 Mock 场景已实际运行，输入、实际输出和 Trace 位于 `examples/01-normal` 至 `examples/07-tenant-b`。
- 本轮新增 8 项针对性测试，最终标准库局部回归：`Ran 32 tests in 0.089s`，`OK`；不包含真实后端验收。
- 只做本轮缺口所需的局部测试和一次回归，不增加性能测试或外部调用。

| 能力 | 代码状态 | 验证边界 |
|---|---|---|
| 契约、身份、预算、Trace | `implemented_locally` | 有局部测试；不是生产认证 |
| 离线 Search/Read、冲突、引用 | `implemented_locally` | 合成资料局部证据 |
| 意图、三路回复、Mock 循环 | `implemented_locally` | 规则 Mock，不证明真实分类或 ReAct 效果 |
| CLI/FAQ/可信配置 | `implemented_locally` | 问候、FAQ、身份拒绝有测试；history 参数未单独测 |
| 摄取、发布、hybrid 接线 | `implemented_locally` | StubEmbedding/StubVectorStore；未验证真实后端 |
| CUDA/Qdrant/provider 适配器 | 代码已写，真实集成待验证 | `runtime_evidence_pending` |
| 删除/擦除、显式条件入口 | `implemented_locally` | 替身局部证据；自然语言确认和真实擦除仍 `runtime_evidence_pending` |

提交区未修改；未创建或推送 Git 提交。完整限制与当前未测路径以 `docs/03-evaluation.md` 为准。
