# 交付信息、要求对照与待补项

## 1. 提交信息

| 字段 | 当前值 |
|---|---|
| 选题 | 企业知识问答 Agent |
| 题号 | 03 |
| 实际用时 | 约 5 小时，提交者提供；无分项计时记录 |
| GitHub URL | 待真实仓库创建或确认后填写 |
| 最终 commit SHA | 待最终提交后填写 |
| 私库评审权限 | 如使用私有仓库，须授予指定评审账号读取权限 |
| 运行方式 | CLI，原生 Python；离线标准库 Mock |
| 模型/框架 | 规则 OfflineModel；非第三方 Agent 框架；真实 provider 适配器未运行 |

当前完成的是本地交付材料，GitHub 提交信息与真实模型效果证据仍待补充。
考核要求摘录已收录在 `docs/00-delivery-requirements.md`。

## 2. 个人贡献摘要

本项目的个人贡献不以“逐行编写代码”表述，而以可核对的需求、架构和交付决策表述：

- 主导首版需求范围，记录实际投入约 5 小时，并明确有限测试与真实验证的区别；
- 明确要求向量数据库、意图识别、多租户隔离和浅层/深层/兜底回复，避免漏掉系统性能力；
- 提出逐层、逐状态、逐工具详细设计，组织子 agent 审查与修复，推动架构边界收敛；
- 要求围绕问题覆盖补充参考资料，并按 `要求.md` 形成最小示例和实际运行材料；
- 组织“工作区文档优化 → 提交区迁移 → 提交区独立复现”的交付顺序；
- 在 AI 辅助完成代码和文档的过程中持续确认完成度，最终材料保留真实模型、CUDA、
  Qdrant 和生产数据的未验证声明。

详细说明见 [`docs/04-ai-usage.md`](04-ai-usage.md) 和
[`docs/05-retrospective.md`](05-retrospective.md)。

## 3. 要求与材料映射

| 要求 | 材料 |
|---|---|
| 环境、依赖和可复制命令 | `README.md`、`requirements.txt`、`.env.example` |
| 需求与成功标准 | `docs/01-requirements.md` |
| 六层设计、模型与工具循环、取舍 | `docs/02-solution.md`、`架构文档/` |
| 运行记录、预期与实际、失败和限制 | `docs/03-evaluation.md`、`examples/minimal/` |
| AI 使用和个人贡献 | `docs/04-ai-usage.md` |
| 难点、收获、下一步 | `docs/05-retrospective.md` |
| 资料及版本元数据 | `data/*.jsonl`、`config/demo-faqs.json` |
| 代码和工具接入 | `src/knowledge_agent/` |
| 可执行测试与输出 | `tests/`、`examples/minimal/test-results.txt` |
| 两种观察驱动路径 | `examples/minimal/01-normal/`、`02-conflict/` 的 Trace |
| 正常、资料不足、工具失败 | 六组 input/output/Trace，路径由 `summary.json` 指定 |
| 六项题目验收 | 最小示例的 `01` 至 `06` 场景 |
| 执行预算与未完成 | `runtime.py`、`test_minimal_example.py` |
| 原文替换影响结果 | 聚焦测试中的 30 天 → 45 天用例 |
| 题目与考核规范原文 | 根目录 `题目.md`、`要求.md` |

## 4. 交付范围

提交目录保留可运行代码、测试、合成数据、配置、主要文档、详细架构和开源参考资料。
旧示例及旧测试摘录仅作为历史记录，不与最新最小示例测试次数合并。
不迁移 Python 缓存、真实 `.env`、模型权重或本地向量数据库；不包含外部凭据。

## 5. 尚未关闭的事项

- 补齐 GitHub URL、最终 commit SHA；如使用私有仓库，再确认评审权限；
- 提交者按真实工作过程核对个人贡献与约 5 小时估计；
- 真实 LLM 驱动 ReAct 尚无效果验收；规则 Mock 不可冒充真实模型；
- CUDA、Qdrant、真实 provider、通用语义冲突与生产性能均不在已验证结论中。
