# 企业知识问答 Agent

## 1. 项目简介

本项目选择“企业知识问答 Agent”题目，帮助员工在当前租户已发布的制度或 FAQ 中
查询可靠答案。系统采用 CLI 作为交互方式，使用合成资料和标准库离线 Mock 验证
Agent 决策循环、证据引用、冲突处理、资料缺口、上下文追问和工具失败。

本项目实际投入时间：**约 5 小时**（提交者估计，未单独统计各阶段用时）。

当前交付版本的真实边界是：离线 `OfflineModel`、合成资料和 fixture 检索已有运行
证据；真实 LLM、真实 CUDA embedding、Qdrant 和外部 provider 尚未运行验证。不能把
Mock 结果表述为真实模型效果。

## 2. 快速运行

环境：Python 3.10+、标准库；本地验证使用 Windows 11/PowerShell 和现有 Conda 环境。
无需安装额外依赖或配置密钥；不使用第三方 Agent 框架。在项目根目录执行：

```powershell
python .\examples\minimal\run.py
```

本机也可将 `python` 替换为 `& 'D:\Anaconda3\envs\myenv_1\python.exe'`。
`requirements.txt` 的离线依赖为空；`python -m pip install -r requirements.txt` 可省略。

该命令一次运行六类验收场景，并将同一轮生成的 `input.json`、`output.json` 和
Trace 保存到 `examples/minimal/`，汇总见 `examples/minimal/summary.json`。

运行聚焦测试：

```powershell
python .\tests\test_minimal_example.py
```

## 3. 六项题目验收

| 场景 | 预期结果 |
|---|---|
| 明确答案 | `completed`，引用 D01 原文“30天” |
| 文档冲突 | `needs_clarification`，保留 D01 的 30 天和 D02 的 15 天 |
| 资料外问题 | `insufficient_evidence`，不编造引用 |
| 改写后的相似问题 | 返回同一份已读取原文 |
| 上下文追问 | 使用历史用户问题补足指代，助手历史不作为证据 |
| 工具失败 | `execution_error`，保留 `source_unavailable` |

Trace 记录模型选择的动作、工具参数、观察结果、下一步动作和决策依据摘要，不记录
模型内部思维链。默认限制为 6 次模型调用和 4 次工具调用，超限返回
`incomplete / budget_exhausted`。

## 4. 运行方式

### 4.1 纯问候

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m knowledge_agent.cli `
  --mode mock --question '你好'
```

### 4.2 单文档明确答案

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m knowledge_agent.cli `
  --mode mock --data .\data\single-document.jsonl `
  --question '报销期限多久'
```

### 4.3 多文档冲突

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m knowledge_agent.cli `
  --mode mock --data .\data\documents.jsonl --faqs .\config\demo-faqs.json `
  --question '报销期限多久'
```

身份由 `config/demo-identities.json` 中的可信配置解析；`--principal` 和 `--tenant`
不能自行授予权限。历史、显式过滤和 `hybrid` 入口的参数说明见
`src/knowledge_agent/cli.py` 和 `docs/02-solution.md`。

## 5. 代码与文档结构

```text
README.md
.env.example
requirements.txt
src/knowledge_agent/       # 契约、路由、编排、模型、工具、知识和 CLI
tests/                     # 局部自动化测试
examples/                  # 输入、实际输出、Trace、测试结果
data/                      # 合成制度资料
config/                    # 合成身份和 FAQ 配置
docs/                      # 需求、方案、评估、AI 使用和复盘
架构文档/                  # 分层、状态、工具和可靠性设计
```

## 6. 交付信息

- 选题名称：企业知识问答 Agent
- 题号：03
- 实际用时：约 5 小时
- GitHub URL：https://github.com/cheny1295/03-enterprise-knowledge-qa-agent
- 仓库可见性：公开，评审无需私库授权
- 默认分支：`main`
- 交付版本：以 `main` 分支提交为准；本地运行 `git rev-parse HEAD` 获取当前检出版本的完整 commit SHA

## 7. 个人贡献重点

本项目的个人贡献主要体现在需求定界、架构约束和交付组织，而不是把 AI 辅助生成的
每一行代码表述为个人手写成果：

- 明确向量数据库、意图识别、多租户隔离、浅层快答、深层查询和兜底回复为必做能力；
- 要求每层、每个状态、每个工具都有职责、契约、失败处理和验证入口；
- 组织参考资料补足、子 agent 架构审查、问题修复和要求逐项核对；
- 要求先优化工作区文档，再迁移到提交区，并在迁移后重新生成运行材料；
- 对真实模型和生产后端保持未验证声明，不以设计能力冒充实测效果。

详见 [AI 使用与个人贡献](docs/04-ai-usage.md) 和 [复盘与个人评价](docs/05-retrospective.md)。

## 8. 已知限制

- 所有当前运行材料使用合成资料和规则 Mock；
- 未安装或下载真实 embedding 权重，未执行 CUDA；
- 未执行真实 Qdrant 的写入、查询、删除和跨进程发布；
- 未调用真实 provider，未验证真实模型 JSON 返回和网络失败；
- 自然语言条件自动提取和通用语义冲突裁决尚未实现；
- 不能据此宣称生产级性能、召回率或真实 LLM ReAct 效果。

## 9. 评审导航

- [约 200 字架构思路](架构思路.md)
- [需求与成功标准](docs/01-requirements.md)
- [六层方案、调用流程与取舍](docs/02-solution.md)
- [预期、实际运行材料与未测范围](docs/03-evaluation.md)
- [AI 使用、贡献边界与约 5 小时用时](docs/04-ai-usage.md)
- [复盘与下一步](docs/05-retrospective.md)
- [提交信息与要求逐项映射](docs/12-submission.md)
- [详细架构：层、状态、工具](架构文档/README.md)

真实模型配置见 `.env.example`。该文件只列变量名，程序不会自动加载 `.env`；
使用 `--mode model` 前须由进程环境提供变量，配置缺失不会自动回退到 Mock。
