# 00 交付要求摘录与逐项核对

本文件把评审所需的根目录要求整理到项目内部，便于提交区脱离上级目录后独立审阅。
它是要求摘录和材料索引，不替代 `01-requirements.md` 的产品需求说明。

## 一、考核交付要求

- 选择一个题目，提供可运行项目并用测试或运行材料证明核心链路；
- 运行方式从 CLI、HTTP API、Notebook 中选择一种，并提供可复制命令；
- 模型不可用时必须提供明确标注的离线 Mock 入口；
- 至少实现“模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束”的循环；
- 至少保留两条因工具观察不同而产生不同后续动作的运行记录；
- Trace 记录步骤、工具参数、返回摘要、后续动作和简要决策依据，不要求提交模型内部思维链；
- 设置模型步数或工具调用上限，超限返回明确未完成状态；
- 项目包含 README、src、tests、examples、data、五份 docs、依赖文件和 `.env.example`；
- examples 至少包含正常、资料或信息不足、工具失败三类实际材料，并覆盖题目验收点；
- 如使用 AI，记录辅助范围、个人贡献、实际验证和限制；
- GitHub URL、最终 commit SHA、题号和私有仓库权限必须按真实提交信息填写。

## 二、本题验收要求

题目为“企业知识问答 Agent”，必须支持：

- 问题和可选对话历史；
- 带文档 ID、版本日期的合成 FAQ 或制度；
- 文档检索和片段读取工具；
- 回答、引用文档 ID 和原文片段、资料缺口、任务状态；
- 根据检索结果选择补充检索、读取原文、追问或停止；
- 文档冲突时保留冲突、查证并澄清，不凭空选择一个答案；
- 明确答案、文档冲突、资料外问题、改写相似问题、上下文追问和工具失败六项验收。

经典冲突是 D01 的 30 天和 D02 的 15 天；资料没有范围和优先级时，必须保留双方
引用并请求确认，不能擅自回答 15 天。审核可能替换原文，答案和动作应随依据改变。

## 三、当前材料映射

| 要求 | 位置 |
|---|---|
| 可复制 CLI 和离线 Mock | `README.md`、`examples/minimal/README.md` |
| 模型动作循环和路径差异 | `src/knowledge_agent/orchestrator.py`、`examples/minimal/*/runtime/` |
| 步数/工具上限 | `src/knowledge_agent/runtime.py`、`tests/test_minimal_example.py` |
| 六项题目场景 | `examples/minimal/01-normal` 至 `06-tool-failure` |
| 完整输入、输出和 Trace | 各场景 `input.json`、`output.json`、`runtime/` |
| 设计和逐层/逐状态/逐工具文档 | `docs/02-solution.md`、`架构文档/` |
| AI、个人贡献、约 5 小时 | `docs/04-ai-usage.md`、`docs/05-retrospective.md` |
| GitHub/commit/题号待补项 | `docs/12-submission.md` |

## 四、明确未验证

离线材料不能证明真实 LLM ReAct、CUDA embedding、Qdrant、真实 provider、生产性能
或通用语义冲突裁决。提交材料对此保持明确降级，不把代码接线写成真实验收结果。
