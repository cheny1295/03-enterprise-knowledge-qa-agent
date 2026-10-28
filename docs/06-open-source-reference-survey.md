# 06 开源架构参考摘要

> 本文是架构取舍的资料摘要，不是本项目的依赖清单，也不代表这些项目的能力已在本地运行。
> 完整检索过程保留在工作区；提交区保留 12 个代表项目的来源、采用的思路及未采用的复杂能力。

## 1. 参考结论

资料收集先覆盖 12 个代表项目，再按问题缺口补充来源。交付版将结果压缩为五个问题，
不再按搜集轮次罗列过程：

| 设计问题 | 代表性参考 | 对本项目的落地 |
|---|---|---|
| 检索如何与答案解耦 | Dify、RAGFlow、Haystack、LlamaIndex | Search 只返回候选，Read 才生成可引用证据 |
| Agent 如何循环和停止 | LangGraph、Haystack、FastGPT、NVIDIA RAG Blueprint | 显式 State、Action、Observation、预算和停止原因 |
| 企业资料如何隔离 | AnythingLLM、Onyx、Haystack metadata filtering | TenantContext 由可信入口提供，并贯穿检索、原文和 Trace |
| 冲突如何被发现和评测 | Google conflict work、ConfRAG | 同主题冲突保留双方证据，优先级不足时澄清，不按日期猜测 |
| 知识如何更新和评估 | Quivr、Onyx、RAGFlow、Phoenix、DeepEval | 摄取/发布与在线问答分离；评估拆成检索、证据、动作和轨迹层 |

## 2. 12 个代表项目与取舍

下列链接沿用前期收录的一手资料，未在本轮重新验证线上版本；本文不声称使用这些项目
的代码或安装了其依赖。来源保留在交付仓库内，不要求评审者访问工作区。

| 项目 / 架构入口 | 提炼的设计思路 | 本次不引入的能力 |
|---|---|---|
| [Dify · Knowledge Retrieval](https://github.com/langgenius/dify-docs/blob/main/en/cloud/use-dify/nodes/knowledge-retrieval.mdx) | 结构化候选及来源元数据 | 画布、插件市场 |
| [RAGFlow · Agent](https://ragflow.io/docs/v1.0.0-rc2/agent_overview) | 检索与生成分开诊断，保留工具轨迹 | OCR、多模态解析 |
| [FastGPT · Workflow](https://github.com/labring/FastGPT/blob/main/.agents/design/core/workflow/index.md) | 明确动作、观察与工作流职责 | 完整工作流引擎 |
| [MaxKB · Architecture](https://github.com/1Panel-dev/MaxKB/blob/v2/AGENTS.md) | 执行引擎、模型适配和工具解耦 | Django/Celery/MCP 服务 |
| [Onyx · Chat Loop](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/chat/README.md) | 请求、历史、工具与运行状态分开 | 企业连接器平台 |
| [AnythingLLM · Agents](https://docs.anythingllm.com/features/ai-agents) | 限制当前任务的知识与工具范围 | 桌面产品及工具生态 |
| [Quivr · Overview](https://github.com/The-Vibe-Company/quivr/blob/main/README.md) | 摄取、检索生成与评测分工 | 全栈多服务产品 |
| [Haystack · Agents](https://github.com/deepset-ai/haystack/blob/main/docs-website/docs/concepts/agents.mdx) | 显式状态、工具循环与停止条件 | 通用组件图框架 |
| [LlamaIndex · Agents](https://github.com/run-llama/llama_index/blob/main/docs/src/content/docs/framework/use_cases/agents.md) | 查询策略与证据处理独立表达 | 子问题/多 Agent 体系 |
| [LangGraph · Graph API](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/graph-api.mdx) | 将循环与状态转换显式化 | 分布式编排及恢复 |
| [Microsoft GraphRAG · Query](https://github.com/microsoft/graphrag/blob/main/docs/query/overview.md) | 按问题类型选择检索策略 | 图索引与社区摘要 |
| [NVIDIA RAG Blueprint · Agentic RAG](https://github.com/NVIDIA-AI-Blueprints/rag/blob/main/docs/agentic-rag.md) | 缺口驱动补检索，限制复杂路径成本 | 并行子 Agent 和多模型重规划 |

## 3. 对本项目的最终影响

资料收集最终收敛为四条架构原则：

1. **先确定访问边界，再检索**：身份、租户和快照必须先绑定；
2. **先取得证据，再生成结论**：候选不能直接成为引用；
3. **让观察驱动动作**：不同工具结果必须能够导向不同后续动作；
4. **把失败作为一等结果**：冲突、资料不足、工具失败和预算耗尽不能互相伪装。

因此，提交区的权威设计以 `架构文档/01–08` 和 `10` 为准；本文只解释设计来源，
不再重复完整问题树、逐项目资料摘录或历史修复过程。

## 4. 定向补充来源

- 评测与观测：[DeepEval Agent Evaluation](https://github.com/confident-ai/deepeval/blob/main/docs/content/guides/guides-ai-agent-evaluation.mdx)、
  [Phoenix](https://github.com/Arize-ai/phoenix)。用于拆分指标与轨迹，不代表已接入评测框架。
- 工具内容安全：[Promptfoo Indirect Prompt Injection](https://github.com/promptfoo/promptfoo/blob/main/site/docs/red-team/plugins/indirect-prompt-injection.md)。
  用于理解风险，不构成安全认证结论。
- 冲突评测：[Google Research](https://research.google/pubs/dragged-into-a-conflict-detecting-and-addressing-conflicting-sources-in-retrieval-augmented-llms/)、
  [ConfRAG](https://aclanthology.org/2026.acl-long.11/)。用于说明冲突需要独立评测，不作为本地效果证据。
- 向量与编码：Qdrant 官方文档、BAAI 模型卡、Sentence Transformers API 的来源与选型理由集中在
  [向量、路由与租户设计第 15 节](../架构文档/10-routing-vector-and-tenant-design.md)。

资料只回答“为什么这样设计”。当前实现和验证程度分别以
[方案](02-solution.md)、[评估记录](03-evaluation.md)为准，复用代码前仍需核对具体版本与许可证。
