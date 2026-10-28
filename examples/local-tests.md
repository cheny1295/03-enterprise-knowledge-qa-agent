# 局部测试运行摘录

日期：2026-10-28。未安装额外依赖，未请求真实 provider、Qdrant 或 CUDA 模型。

执行：

```powershell
$env:PYTHONPATH = '.\src'
& 'D:\Anaconda3\envs\myenv_1\python.exe' -m unittest discover -s tests -v
```

实际末尾输出：

```text
Ran 32 tests in 0.089s

OK
```

这是加入最小 example 之前的历史离线集合摘录，不是全部架构场景、真实模型效果或生产
验收结果；不能与后来的 3 项聚焦测试合并成最新全量已重跑结论。
`test_hybrid_wiring.py` 仅使用明确标记的 StubEmbedding/StubVectorStore。
