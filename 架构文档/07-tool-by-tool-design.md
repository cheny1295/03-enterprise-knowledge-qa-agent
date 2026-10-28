# 07 逐工具详细设计

## 1. 工具范围与统一契约

问答模型只开放两个业务工具：`search_documents` 和 `read_document`。`ToolRouter`、`EvidenceService`、`ConflictAnalyzer`、意图分类、embedding、索引摄取与发布都是执行/应用基础设施，不是模型可见工具。所有数据工具隐式注入 trusted TenantContext。

```text
Tool.execute(arguments, request_context) -> ToolResult

ToolResult {
  tool_name: string
  tool_call_id: string
  status: ok | no_evidence | not_found | error
  data: object
  evidence: ReadEvidence[]       // Search 固定为空数组
  error?: ToolError              // status=error 时必填
  metadata: { tenant_id, request_id, snapshot_id, elapsed_ms }
}
```

`ToolResult.status` 不是 `AnswerResult.status`。`no_evidence` 是正常执行结果；`not_found` 仅表示指定 candidate 无法读取；`error` 表示工具失败。工具必须校验参数、只读当前不可变快照、不调用决策/分类模型、不修改资料、不接受任意路径/URL/Shell/代码、不自动重试，并返回结构化结果。Search 可通过独立 EmbeddingModel 编码查询。

所有 ToolResult（包括成功、空命中、`not_found`、`error`）必须先转换为 Observation：

```text
Observation {
  observation_id, tenant_id, request_id, tool_call_id, tool_name,
  status, summary, candidate_ids[], evidence_ids[],
  error_code?, snapshot_id
}
```

request 或 snapshot 不匹配时，只能驱动错误处理，不得继续使用其中的 candidate/evidence。L3 在 OBSERVING 中统一登记，成功与失败均有实际 observation_id。

## 2. `search_documents`

### 2.1 目的与边界

在当前租户 `KnowledgeSnapshot` 中执行向量/词项混合召回并按稳定 rank 融合候选。Search 只负责召回，不生成最终答案、ReadEvidence、Citation 或制度优先级，也不裁决冲突。

### 2.2 输入

```json
{
  "type": "search_documents",
  "query": "差旅报销提交期限",
  "filters": {"scope_any": ["差旅报销"]},
  "limit": 5
}
```

工具参数：

```text
search_documents(
  query: string,
  filters?: {
    scope_any?: string[]
    scope_all?: string[]
    document_ids?: string[]
    status?: ("active" | "withdrawn")[]
    version_date_from?: date
    version_date_to?: date
    effective_on?: date
  },
  limit?: integer,
  request_context: { tenant_id, request_id, snapshot_id, authz_version }
) -> ToolResult
```

约束：

| 参数 | 约束 |
|---|---|
| `query` | 去空白后非空；只作为检索文本，不解释为路径或指令 |
| `scope_any` | 与候选 scope 至少有一个交集 |
| `scope_all` | 过滤列表中的每个值都包含于候选 scope |
| 两种 scope filter | 同时提供时取 AND；空 scope 不匹配显式 scope filter |
| `document_ids` | 只筛当前 snapshot 内的文档 |
| `status` | 仅 `active`、`withdrawn` |
| 版本日期 | 对 `version_date` 做包含端点过滤 |
| `effective_on` | 只匹配两端均明确声明且包含该日期的生效区间；缺失端点不当作无穷区间 |
| `limit` | 正整数且不超过应用服务预算 |

未提供 filters 时仍施加应用约束；limit 未提供时使用 L2 固定配置（首版默认 5）。模型动作只含 type/query/filters/limit 或 type/candidate_id；工具层剥离 type 后接收参数，request_context 由运行时注入。

应用服务默认施加 `status=["active"]`；用户明确询问历史规定并确认参考日期时，才生成包含 withdrawn 的过滤。L2 记录已确认条件及来源，L5 取应用约束与动作条件的交集；未经确认的 scope/date 不能用于排除冲突来源。模型不得移除、放宽系统过滤器，也不得从标题猜测 scope/status。缺失有效期不推导为有效或无效，需要这些资料时不加 effective_on 过滤，读取后说明缺口。

当前入口为 AskRequest.filters 或 CLI `--filters` JSON；撤回制度另需 `historical=true` / `--historical` 和 `effective_on`。模型可省略 filters，由工具施加全部约束；若显式提交，只接受已确认字段的同值重述，拒绝擅自缩小范围来隐藏冲突。

### 2.3 执行步骤

```text
1. 校验 TenantContext、request_id、snapshot_id、参数结构和 limit
2. 保留原 query，生成仅用于召回的 normalized_query
3. 合并不可放宽的 tenant/snapshot、系统和用户确认过滤器
4. 通过 EmbeddingModel 对 query 编码一次；缺少 CUDA/model 时返回明确错误
5. 执行受 tenant/snapshot 过滤的 Qdrant dense top-10 和本地词项 top-10
6. 合并 FAQ 命中的完整来源组、按 section 聚合并使用 RRF 融合 rank
7. 按稳定身份顺序排序，生成当前 request 内唯一的 SearchCandidate(candidate_id) 和 coverage
8. 返回候选、实际检索模式、revision 和调用元数据
```

排序分数只用于候选排序，不代表答案置信度、事实可信度或制度优先级。snippet 只用于决定是否读取，不可直接进入最终 Citation。

`candidate_truncated` 也包含分支截断：词项命中超过 top-10、dense chunk 恰好达到 top-10 上限（保守标记），或融合结果超过 limit。请求内已知截断不会被后续一次窄查询覆盖成 false。它不是全库召回率指标。

### 2.4 成功返回

```json
{
  "tool_name": "search_documents",
  "tool_call_id": "tc-001",
  "status": "ok",
  "data": {
    "query": "差旅报销提交期限",
    "normalized_query": "差旅报销提交期限",
    "coverage": {"candidate_truncated":false,"topic_complete":false},
    "retrieval_mode": "hybrid",
    "embedding_revision": "synthetic-example-revision",
    "candidates": [{
      "candidate_id": "C1",
      "tenant_id": "tenant-A",
      "request_id": "req-001",
      "document_id": "D01",
      "version_id": "D01-v1",
      "version_date": "2026-03-01",
      "effective_from": "2026-03-01",
      "effective_to": null,
      "title": "费用报销管理办法",
      "scope": ["差旅报销"],
      "status": "active",
      "supersedes": [],
      "priority": null,
      "section_id": "4.2",
      "snippet": "员工应在费用发生后30天内提交报销申请。",
      "retrieval_query": "差旅报销提交期限",
      "retrieval_rank": 1,
      "lexical_score": 0.82,
      "vector_score": 0.78,
      "lexical_rank": 1,
      "vector_rank": 1,
      "retrieval_score": 0.032786885,
      "retrieval_mode": "hybrid",
      "snapshot_id": "snap-001"
    }]
  },
  "evidence": [],
  "metadata": {"tenant_id":"tenant-A","request_id": "req-001", "snapshot_id": "snap-001", "elapsed_ms": 12}
}
```

Search 只返回 `candidates`，不返回 `ReadEvidence`，不生成最终 `evidence_id`。正常零命中返回 `status=no_evidence`、`data.candidates=[]`、`evidence=[]`。

### 2.5 错误、Trace 与验证

| 场景 | 返回 | 后续 |
|---|---|---|
| 参数无效 | `error` + `invalid_arguments` | 不执行检索 |
| 快照不可用 | `error` + `knowledge_load_error` | 错误候选并统一校验 |
| 索引损坏/读取异常 | `error` + `internal_error` | 不伪装为空命中 |
| 注入的可恢复超时 | `error` + `timeout` | 错误 Observation，由模型决定新动作；本地工具不调用 provider |

Trace 记录 query、tenant_ref、过滤器摘要、limit、request/snapshot/tool_call ID、候选 ID、分支排名、模式、embedding revision、状态、错误码、耗时和后续决策来源；不记录完整资料库。验证覆盖：命中、改写查询、零命中、active/withdrawn、scope_any/all、不同版本并列、外租户 point、索引损坏、CUDA/model 缺失、恶意文本不触发工具。

## 3. `read_document`

### 3.1 目的与输入

读取本次请求 Search 返回的 candidate 对应原文，生成可供 Claim/Citation 使用的 ReadEvidence。模型唯一有效输入为：

```json
{"type": "read_document", "candidate_id": "C1"}
```

```text
read_document(
  candidate_id: string,
  request_context: { tenant_id, request_id, snapshot_id, authz_version }
) -> ToolResult
```

`candidate_id` 必须来自当前请求的 Search Observation，并绑定当前 snapshot。工具从 candidate 解析 document/version/section；这些字段不是模型授权参数，多余字段一律拒绝，不设调试兼容输入。request_context 来自运行时注入，不属于模型动作 schema。

### 3.2 前置检查与执行

前置条件：存在当前请求 Search Observation；candidate 属于当前 tenant/request/snapshot；来源仍在该租户当前快照；authz_version 未撤销；预算允许。失败时不得读取近似版本、整篇文档或任意路径。

```text
1. 校验 candidate 授权及 request/snapshot 一致性
2. 从 Search Observation 解析来源身份
3. 读取指定 document/version/section 原文
4. 保留 heading、text、定位和版本元数据
5. 由 EvidenceService 创建 ReadEvidence
6. 返回 Evidence 对象数组
```

成功结果中 `ToolResult.evidence` 为对象数组，每个 ReadEvidence 至少包含：

```text
evidence_id, tenant_id, source_candidate_id, request_id, document_id,
version_id, version_date, effective_from?, effective_to?,
title, scope[], status, supersedes[], priority, section_id, quote, snapshot_id
```

原文 quote 必须来自当前快照，保持原样。最终 Claim 只能引用 ReadEvidence 的 `evidence_id`，不能引用 candidate ID。

完整成功示例：

```json
{
  "tool_name": "read_document",
  "tool_call_id": "tc-002",
  "status": "ok",
  "data": {
    "candidate_id": "C1",
    "document_id": "D01",
    "version_id": "D01-v1",
    "section_id": "4.2",
    "heading": "提交期限",
    "text": "员工应在费用发生后30天内提交报销申请。"
  },
  "evidence": [{
    "evidence_id": "E1",
    "tenant_id": "tenant-A",
    "source_candidate_id": "C1",
    "request_id": "req-001",
    "document_id": "D01",
    "version_id": "D01-v1",
    "version_date": "2026-03-01",
    "effective_from": "2026-03-01",
    "effective_to": null,
    "title": "费用报销管理办法",
    "scope": ["差旅报销"],
    "status": "active",
    "supersedes": [],
    "priority": null,
    "section_id": "4.2",
    "quote": "员工应在费用发生后30天内提交报销申请。",
    "snapshot_id": "snap-001"
  }],
  "metadata": {"tenant_id":"tenant-A","request_id": "req-001", "snapshot_id": "snap-001", "elapsed_ms": 8}
}
```

### 3.3 `not_found` 与错误处理

| 场景 | ToolResult | 说明 |
|---|---|---|
| 未提供有效授权输入 | `error` + `invalid_arguments` | 不访问仓库 |
| candidate 不存在/属于其他 request 或 snapshot | `not_found` | 不泄露其他请求是否存在该 ID |
| 来源版本不在当前快照 | `not_found` | 不替换为新版本 |
| section 缺失/索引损坏 | `error` + `internal_error` | 不回退读取整篇文档 |
| 临时读取失败 | `error` + `timeout` | 形成 error Observation |

`not_found` 不等于全局 `no_evidence`；模型可在预算内重新搜索或结束。Trace 记录 candidate ID、授权检查、读取状态、evidence ID、来源摘要、snapshot、耗时和错误码；quote 是否持久化遵从 Trace 数据最小化配置。

验证覆盖：合法读取、未搜索 candidate 拒绝、跨请求/快照 ID 拒绝、版本不替换、section 不回退、多个来源独立 Evidence、恶意原文不影响系统指令。

## 4. 唯一工具协作主路径

```text
search_documents
  → ToolResult
  → Observation(SearchCandidate(candidate_id))
  → read_document(candidate_id)
  → ToolResult
  → Observation(ReadEvidence(evidence_id))
  → Claim(evidence_ids=[ReadEvidence.evidence_id])
  → Citation
```

- EvidenceService 是 ReadEvidence 创建、去重、来源回查和 snapshot 校验的唯一责任方。
- ConflictAnalyzer 只依据已读取 Evidence 与显式元数据生成 ConflictAssessment。
- 所有工具结果先形成 Observation；错误不能伪装成 no_evidence。
- Search 的 `candidate_id` 与 ReadEvidence 的 `evidence_id` 不可互换。
- 以下均非规范接口：Search `hits/evidence_id`、字符串形式 `evidence`、`read_document(evidence_id)`、以 document/version/section 参数授权读取。

## 5. ToolRouter 验收矩阵

| 用例 | 预期 |
|---|---|
| 合法 Search | 返回 `ok` candidates 或 `no_evidence` |
| 合法 Read | 仅读取当前请求已授权 candidate |
| 未知工具名 | `invalid_model_action`，无工具副作用 |
| 多余/缺失字段 | L3 拒绝为 invalid_model_action；直接工具单测则 error/invalid_arguments，无检索/读取副作用 |
| 工具异常 | `status=error`，保留 ToolError，不伪装 no_evidence |
| candidate 跨请求 | `not_found`，不泄露其他请求数据 |
| 超工具预算 | 不进入工具函数，最终 `incomplete` |
| Evidence 返回 | ResultValidator 可回查其来源 |
| 未决冲突 | Assessment 覆盖双方 candidate/evidence；正常业务退出为 needs_clarification，错误/超限如实标注，均不得 completed |

## 6. 非模型管理接口

`ingest/publish/delete` 只由 L2 管理入口调用，要求 knowledge_editor；接口和发布状态见 10 第 10/12 节，规范元数据见 02。reader、源资料伪造 tenant 或从问答请求发起写入均拒绝，不提供模型可调用的 SQL/collection/文件路径。

摄取按 tenant/新 snapshot 校验 source→切片→CUDA 编码→写 Qdrant/词项/原文包→READY→ACTIVE；失败为 FAILED，旧 ACTIVE 不变。更新删除不修改在用快照；授权撤销需阻断输出并清理派生数据和相应原文/Trace。首次安装锁定依赖/模型 revision，当前示例中的分数、耗时与 synthetic-example-revision 仅是契约样例，不是实测结果。

### 6.1 参数与返回

```text
ingest(context: TenantContext, package: TenantSourcePackage) -> BuildResult
publish(context: TenantContext, snapshot_id: string) -> PublicationResult
delete(context: TenantContext, document_id: string, erase: boolean) -> PublicationResult
BuildResult { tenant_id, snapshot_id, publication_state: READY | FAILED, error_code? }
PublicationResult { tenant_id, active_snapshot_id?, previous_snapshot_id?, error_code? }
```

package 包含 02 的租户文档与 FaqEntry；管理调用不返回 ToolResult、不形成模型 Observation，而是独立管理事件。publish 仅接受当前 tenant 的 READY 快照；delete 用删除后的全量包重建再发布，erase=true 时按 10 的撤权/隐私删除流程处理。无编辑权限返回 access_denied；编码/存储失败分别 embedding_unavailable/vector_store_error；版本冲突为 index_version_mismatch；不自动重试。
