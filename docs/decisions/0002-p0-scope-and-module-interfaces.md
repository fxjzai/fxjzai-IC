# ADR-0002：P0 范围与深模块 interface

- 状态：Proposed
- 日期：2026-08-19
- 决策范围：IC 的 P0 产品闭环、核心 module、interface 与 seam

## 决策

P0 只承诺一条可验证的情报闭环：

```text
已审核 Source
  → SourceEndpoint
  → Ingest
  → 不可变 RawDocument + 规范 Item
  → Select
  → 可解释 Digest
  → 统一只读能力面
  → Feedback
  → 人工校准与新策略版本
```

`Story` 属于 P0.5，在真实跨源聚类质量通过验证后加入。`Claim / KnowledgeCard / KnowledgeIndex` 属于独立的 P1 知识编译产品环。`Concept / OpenQuestion / 多租户 Subscription` 属于 P2。

字段、ID、去重与保留策略由 [P0 规范数据契约](../specs/p0-domain-contract.md)定义，本 ADR 只规定 module interface 和行为不变量。

## 深模块

P0 只有两个核心写入 module：

1. **Ingest**：隐藏抓取、原始保存、解析、规范化、确定性去重、provenance、策略执行、检查点与失败恢复。
2. **Select**：隐藏候选快照、预筛、评分、摘要、推荐理由和 Digest 生成。

另外提供一个统一只读能力面和一个最小 Feedback interface。`domain` 只能保存这些 module 共用的类型与不变量，不能成为“所有逻辑都可以放入”的第四个 module。worker、队列和时钟是运行机制，不是领域 module。

## P0：Ingest

### 外部 interface

```python
async ingest(endpoint: SourceEndpointRef) -> IngestResult
```

调用方只需提交已登记的 endpoint。Ingest 自己负责：

- 读取并执行 `SourcePolicy`；
- 选择 fetch adapter，处理鉴权、限流和增量 checkpoint；
- 保存不可变 `RawDocument`；
- 解析并规范化一个或多个 `Item`；
- canonical ID、内容指纹、跨端点确定性去重和 provenance；
- 逐文档失败隔离、重试、隔离区与运行状态。

调用方不得逐步编排 `fetch → parse → normalize → deduplicate → persist`。这些行为属于 Ingest 的 implementation。

`Issue` 只是 Newsletter、期刊等天然存在“期次”时的可选父级。普通 RSS 文章不得为了统一结构而伪造 Issue。

`IngestResult` 至少说明本次 run 的 checkpoint 前后值、新 Item、新 revision、重复、隔离区、provenance、类型化错误与 endpoint health observation。调用方不得通过查询数据库猜测运行结果。

### 内部 Fetch seam

Horizon 位于 Ingest implementation 内部的 seam：

```python
class FetchAdapter(Protocol):
    async def fetch(endpoint: SourceEndpoint) -> AsyncIterator[RawDocument]: ...
```

Fetch adapter 只负责协议访问、鉴权、限流、原始响应 provenance 和统一抓取错误。它不得决定：

- IC ID、Item 合并与去重；
- Score、摘要和推荐理由；
- Story、Claim、Digest 或持久化语义。

Horizon 是 `HorizonFetchAdapter` 的候选实现。若某个 Horizon fetcher 只输出丢失原始响应和增量 provenance 的扁平 `ContentItem`，它不能被伪装成 `RawDocument`；应提取更低层能力、编写原生 adapter，或放弃复用。

第一发 tracer bullet 固定使用 `NativeRssFetchAdapter` 作为非 Horizon 生产 adapter；Email 与中文来源另行验证。只有它与 Horizon adapter 均通过同一 Ingest 契约测试后，才可宣称该 seam 真实且 Horizon 可替换。只有一个生产 adapter 时，不建立复杂注册框架。

### 错误与幂等

- 网络失败、限流和 5xx：类型化为可重试失败并保留建议重试时间；
- 鉴权失效或策略禁止：停止自动重试，等待配置或人工处理；
- MIME 不支持、损坏或解析失败：进入隔离区，不推进该文档 checkpoint；
- 重复内容：成功结果，不视为异常；
- 数据不变量破坏：终止当前作业并告警；
- checkpoint 只能在 RawDocument、Item 与 provenance 可靠提交后推进；
- worker 按至少一次投递设计，安全重放不得产生重复 Item；
- 真实内容修改形成显式 revision，不覆盖历史；
- 单源失败不回滚其他来源的已提交结果。

Ingest 只在 worker 作业中执行；网络期间不持有数据库长事务。并发必须有界，并按 host/endpoint 限流和施加背压。

## P0：Select

### 外部 interface

```python
async create_candidate_snapshot(scope: SelectionScope) -> CandidateSnapshotRef
async select(snapshot: CandidateSnapshotRef, policy: PolicyRef) -> Digest
```

调用方不需要了解候选查询、模型层级或评分流水线。Select 自己负责：

- 按 scope 建立并持久化有界、封闭、可重放的 CandidateSnapshot；
- 过滤不可用、重复或策略不允许处理的 Item；
- 低成本预筛与必要的强模型精评；
- 追加带模型、提示词、输入 hash 和策略版本的 `Score`；
- 生成摘要、推荐理由和选择/排除 reason codes；
- 持久化 `Digest` 并避免重复发布。

精选流、日报和 Top 3 是同一个 Digest 的不同 `kind/policy/view`，不是三个 module。

CandidateSnapshot 的 Item ref 必须绑定 `item_id + content_hash`。`select` 与策略回放只接收 snapshot id，不得现场重查。日报 snapshot 按 timezone 和固定周期封闭；增量精选 snapshot 按同 kind 上次已发布 Digest watermark 之后的新 Item、稳定排序与硬上限封闭。一个 snapshot 只对应一种 Digest kind。

### 错误与幂等

- 无候选内容：返回合法空结果或“不发布”，不抛系统异常；
- 策略不存在或失效：返回不可重试配置错误；
- 模型临时不可用：按策略降级、延迟或重试，并标记 degraded；
- 证据不足：排除候选或降低置信度，不生成无来源结论；
- 单个候选失败：隔离该项，其余继续；
- 相同候选快照、策略版本、模型/提示词版本和运行目的只能产生一份 Digest；
- 重评必须创建新运行或策略版本；
- Digest 先持久化，再由推送 adapter 发布。

候选范围必须有时间和数量上限；强模型只处理预筛后的小集合；记录候选压缩率、强模型调用量、延迟、成本与失败恢复。

Score 和 Digest entry 均绑定 Item 的 content hash。Item revision 变化后旧 Score 标记 stale、保留不改；全文超过保留期后的策略回放只使用 excerpt 与元数据，并将结果标记 degraded。

## P0：统一只读能力面

HTTP、内部 Web、未来 MCP 与 Skill 必须读取相同的 canonical ID、排序、授权和可见性语义。RSS 是 Digest 的只读投影，不重建一套筛选逻辑。

候选 interface：

```python
class ReadIntelligence:
    async search(query: SearchQuery) -> SearchPage
    async get_item(item_ref: ItemRef) -> ItemView
    async list_digests(query: DigestQuery) -> DigestPage
    async get_digest(digest_ref: DigestRef) -> DigestView
```

`get_story` 只有 Story 达到 P0.5 门槛后才加入，不能先提供空占位。

读取路径不得同步调用 LLM；搜索必须分页并使用稳定游标。不存在、因保留策略删除、无权访问和索引暂不可用必须可区分。

`ItemView` 必须表明具体 content hash、Source、canonical URL、时间、短摘录、当前 Score 版本、provenance 与原始 payload 可用状态；默认不返回 RawDocument 全文。

如果 P0 只有 HTTP，不为了假想传输创建多层 port；当 HTTP 与 MCP/Web 等第二个真实 adapter 共用同一语义后，传输 seam 才成立。

## P0：Feedback 校准闭环

```python
async record_feedback(target: FeedbackTarget, verdict: FeedbackVerdict) -> FeedbackReceipt
```

P0 verdict 至少包含“有用、无用、误判”。FeedbackTarget 明确区分 Digest entry 与直接 Item；implementation 负责固化当时的 `item_id + content_hash + score_id + policy_version`，并完成幂等追加与审计。

一次 Feedback 不得同步修改 Source 等级、模型权重或在线策略。校准流程是：

```text
Feedback
  → 聚合报告
  → 人工提出候选策略版本
  → 历史候选与反馈回放
  → 人工激活新策略版本
```

在反馈量足够前，不创建假想 `CalibrationAdapter`。未来若增加模型辅助校准，它只能产出候选策略，不能直接修改线上策略。

## P1：Compile

长期知识编译与日报选择是不同产品环，不进入 P0 主路径：

```python
async compile(
    accepted_claims: ClaimSnapshot,
    knowledge_target: KnowledgeTargetRef,
) -> KnowledgeCardDiff
```

Compile 隐藏 Claim 对齐、实体归并、新旧证据比较、冲突识别、引用和 diff 生成。它只返回可审核 diff，不直接发布知识页。

只有“团队持续阅读 Digest”和“人工接受 Claim/审核 Wiki”两种行为均被真实使用证明后，才 scaffold Compile。此前不建立 `knowledge` 目录或公开 interface。

## 不创建的假想 seam

- 不为唯一 PostgreSQL implementation 增加通用 Repository interface；
- 不把 `intelligence/knowledge/scheduler` 目录名当成 module；
- 不同时保留 `connectors` 与 `horizon_adapter` 两套采集入口；Horizon 只是 Fetch adapter；
- 不让 HTTP、RSS、MCP 各自查询数据库或重算 Digest；
- 不为尚未启用的 Story、Compile 或多模型路由暴露占位 interface。

module 测试应从 `ingest`、`select`、统一读取 interface 和 `record_feedback` 观察结果，而不是穿透 interface 断言内部阶段。

## 转为 Accepted 的验证门槛

1. Horizon 与 Native RSS 两个生产 Fetch adapter 通过同一 Ingest 契约测试；
2. 重复投递、崩溃、限流、解析失败和 checkpoint 恢复不制造重复 Item；
3. 相同选择运行重放不生成重复 Digest 或重复推送；
4. 每条 Digest entry 可追溯到 Item、Score、策略和原始内容；
5. HTTP 与至少一个 Agent/产品 adapter 读取相同结果；
6. Feedback 能回到原选择上下文，并支持一次策略版本回放；
7. 团队真实使用 Digest 后，再决定是否启用 Story；
8. 团队形成 Claim 接受和 Wiki 审核行为后，再启动 Compile。

## 仍未决定

- PostgreSQL queue 的具体实现库；
- Horizon 采用内部 fork、vendored subset，还是仅参考重写 fetcher；
- 微信公众号 connector 是否进入 P0，还是在合规与多上游方案成立后进入 P0.5；
- 第一发 tracer bullet 的具体 Native RSS 与 Horizon 来源组合；
- endpoint health 第一发只进入日志/指标，还是增加最小 ops 读取 interface；
- 只读能力面的第二个真实传输 adapter 是 Web 还是 MCP。
