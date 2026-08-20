# P0 规范数据契约

- 状态：Draft
- 日期：2026-08-19
- 适用范围：IC P0 的领域角色、最小字段、ID、去重、溯源与保留约束

本文是调研期的规范 interface。文中的“必须 / 不得 / 可以”分别表示 MUST / MUST NOT / MAY。数据库表结构、模块目录和具体 Python 类型不属于本契约。

## 权威性与分期

| 阶段 | 规范实体 | 产品边界 |
| --- | --- | --- |
| P0 | `Source`、`SourceEndpoint`、`SourcePolicy`、`RawDocument`、`Item`、可选 `Issue`、`CandidateSnapshot`、`Score`、`Digest`、`Feedback` | 可信采集、幂等归一化、可回放筛选、可解释精选、回源与反馈 |
| P0.5 | `Story` | 聚类质量通过真实数据验证后加入；不得成为 P0 Digest 的前置条件 |
| P1 | `Claim`、`KnowledgeCard`、`KnowledgeIndex` | 独立的长期知识编译产品环 |
| P2 | `Concept`、`OpenQuestion`、多租户 `WorkspaceSubscription` | 不进入 P0 interface |

`SelectionDecision` 可以作为 Select implementation 的内部审计记录，但当前不是跨 module 的规范实体。`Source Card` 只是 `ItemView` 的产品表现，不是新实体。

所有实体至少包含 `id`、`schema_version` 和 `created_at`。派生产物还必须记录 `producer`、`producer_version`、`input_ids` 与 `policy_version`，确保可重建和审计。

## P0 最小字段

| 实体 | 最小字段 | 语义 |
| --- | --- | --- |
| `Source` | `id`、`canonical_key`、`name`、`category`、`languages[]`、`status` | 内容生产者或出版品牌的稳定身份，不是 RSS、邮箱或网页地址 |
| `SourceEndpoint` | `id`、`source_id`、`kind`、`locator`、`credentials_ref?`、`priority`、`status`、`cursor?`、`last_observed_at?` | 同一 Source 的可替换入口；凭据只保存引用，不写入记录 |
| `SourcePolicy` | `id`、`source_id`、`version`、`acquisition_basis`、`permitted_actions`、`sharing_scope`、`fulltext_retention_days`、`effective_at` | 对采集、模型处理、团队查看、派生输出、外部输出和保留期的版本化约束 |
| `RawDocument` | `id`、`endpoint_id`、`observed_at`、`upstream_id?`、`source_url?`、`media_type`、`payload_ref?`、`payload_hash`、`byte_size`、`availability`、`policy_version` | 一次采集得到的原始字节或 MIME；是溯源起点，不等于规范内容 |
| `Issue`（可选） | `id`、`source_id`、`edition_key`、`title?`、`published_at?`、`canonical_url?`、`raw_document_ids[]` | 只表示来源中真实存在的 Newsletter/期刊一期 |
| `Item` | `id`、`revision`、`source_id`、`issue_id?`、`title`、`authors[]`、`published_at?`、`discovered_at`、`canonical_url?`、`language`、`excerpt?`、`content_hash`、`body_ref?`、`provenance[]`、`status` | 规范内容原子；事实变化产生新 revision；provenance 至少关联 RawDocument |
| `CandidateSnapshot` | `id`、`kind`、`scope`、`item_refs[]`、`query_version`、`created_at`、`closed_at` | 持久化、封闭的候选集合；item ref 必须绑定 `item_id + content_hash`，Select 与策略回放只接收 snapshot id |
| `Score` | `id`、`item_id`、`item_content_hash`、`dimensions`、`total?`、`reason_codes[]`、`rationale?`、`model_id`、`prompt_version`、`policy_version`、`input_hash`、`input_availability`、`status`、`created_at` | 对某个 Item revision 的一次判断；正文不可用时必须标记 degraded，不是 Item 固有属性 |
| `Digest` | `id`、`kind`、`snapshot_id`、`period_start`、`period_end`、`timezone`、`policy_version`、`status`、`entries[]`、`generated_at`、`published_at?` | 精选流、日报和 Top 3 的统一产物；entry 至少绑定 `item_id + item_content_hash`、`score_id?`、rank、摘要与推荐理由 |
| `Feedback` | `id`、`actor_id`、`target_type`、`target_id`、`label`、`reason_code?`、`comment?`、`created_at`、`applied_policy_version?` | 人对 Item 或 Digest entry 的追加式校准事件；label 至少支持 `useful/useless/misclassified` |

`SourceEndpoint.kind` P0 至少允许 `rss/email/web/api`；`wechat` 只有存在通过稳定性、多上游和来源风险验证的真实 adapter 时才启用，不能因枚举存在就宣称支持。

## Module 出入参

这些字段属于调用方和契约测试必须知道的 interface，不规定 Python 类或数据库表。

### Ingest

`IngestResult` 必须返回：

- `run_id`、`endpoint_id`、`started_at`、`completed_at`；
- `checkpoint_before` 与可靠提交后的 `checkpoint_after`；
- 新建 Item、产生新 revision、命中重复、进入隔离区的数量和引用；
- RawDocument 引用与各结果对应的 provenance；
- 本次 endpoint health observation：成功、degraded、失败类型、重试时间；
- 不变量失败或策略拒绝等类型化错误。

重复 Item 是成功结果；单文档隔离不得抹掉同一 run 中其他已提交结果。

### CandidateSnapshot 与 Select

`SelectionScope` 只负责封闭候选集合：

- `daily`：由 `timezone + [period_start, period_end)` 定义固定周期；周期结束后封闭；
- `stream`：由同 kind、同 Source/语言过滤条件的上次已发布 Digest watermark 之后的新 Item、稳定排序和硬上限定义；超过上限时必须拒绝封闭或使用可续传游标，不得推进 watermark 后跳过候选；
- scope 还可以限制 Source 集合和语言，但不得使用无界历史查询。

创建 snapshot 后必须持久化 `CandidateSnapshot`。`select` 和策略回放只接收 `snapshot_id + policy_version + purpose`，不得现场重新查询 Item。`purpose=publish` 只接受已激活或显式 bootstrap 的策略；`purpose=replay` 产生不设置 `published_at`、不推进 watermark 的评估 Digest。一个 snapshot 只能产生一种 Digest kind；同一次 select 不得同时追加精选流并重算日报。

### 读取与 Feedback

`ItemView` 至少包含：

- `item_id + content_hash`、Source、标题、作者、发布时间与发现时间；
- canonical URL、短摘录、当前内容可用状态和可见 provenance；
- 当前有效 Score 摘要与其策略/模型版本；
- 原始 payload 是否仍可访问；默认不返回原始全文。

`FeedbackTarget` 必须包含 target kind 与 id。针对推荐结果的反馈优先绑定 `digest_id + digest_entry_id`，并在 Feedback 中固化当时的 `item_id + item_content_hash + score_id + policy_version`；直接 Item 反馈请求必须携带调用方看到的 `item_content_hash`，服务端验证该 revision 存在后再记录，避免落到错误 revision。

## 关键不变量

1. 一个 Source 可以有多个 SourceEndpoint；端点失败、替换或降级不得改变 Source 身份。
2. RawDocument 与 hash 必须追加记录，不得覆盖。保留期届满可以删除 payload，但必须留下 hash、来源、时间和删除原因的 tombstone。
3. 一个 Item 可以关联多个 RawDocument，表达 Email、RSS、Web 等跨端点重复到达；任何摘要、评分和推荐理由必须沿 `Item → provenance → RawDocument/source_url` 回源。
4. 普通 RSS、网页文章和 GitHub release 不得为满足统一结构而伪造 Issue。
5. 模型不得修改 Source、RawDocument 或 Item 的事实字段。解析修正形成可审计 revision；Score、Digest 和 Feedback 均追加保存。
6. CandidateSnapshot 必须持久化并绑定每个 Item 的 content hash；Select 与策略回放不得用相同查询条件重新构造候选集。
7. Score 与 Digest entry 必须绑定 `item_id + item_content_hash`。Item 事实变更后，旧 Score 标记 stale，但不得删除或改写；旧 Digest 保持历史真实性。
8. Score 必须绑定完整输入 hash、模型、提示词和策略版本；新评分不得覆盖旧评分。
9. Digest 在 draft 状态可以重算；发布后冻结，修正产生新版本。策略回放 Digest 必须使用独立状态，不得设置 `published_at` 或进入线上 watermark。每个 entry 必须同时给出回源、摘要和入选理由。
10. Feedback 不得直接改写历史 Score 或 SourcePolicy，只能进入人工处置、策略提案或重评分流程。
11. 跨 Source 的语义相似内容在 P0 不得合并成一个 Item；后续由 Story 表达事件聚类，避免把“相似”误当成“同一内容”。

## ID 与去重所有权

IC 拥有全部内部 ID、canonicalization、去重和幂等规则。Fetch adapter 只报告外部标识、原始字节和传输元数据，不得决定 Item ID、合并结果或最终 Score。

- 内部 `id` 使用无业务含义、不可变的全局 ID；URL、标题和内容 hash 不直接充当主键；
- Source 由人工确认的 `canonical_key` 唯一标识；
- SourceEndpoint 按规范化 `kind + locator` 唯一；
- RawDocument 优先按 `endpoint_id + upstream_id/version` 幂等，无稳定上游 ID 时使用 `endpoint_id + payload_hash`；
- Issue 在同一 Source 内按稳定 edition ID 唯一，后备键为 canonical URL；
- Item 优先使用同一 Source 内稳定上游 ID，其次使用规范 canonical URL，最后使用内容指纹；
- 跨端点命中同一 Item 时追加 provenance，不丢弃任一路径；
- 标题相似、embedding 相近或 LLM 判断相似只能产生候选关系，不得作为 P0 的确定性合并依据。

## 生命周期

```text
Source + SourcePolicy
  → Endpoint 捕获 RawDocument
  → 可选 Issue + Item 归一化
  → 封闭 CandidateSnapshot
  → 追加 Score
  → 生成并发布 Digest
  → 追加 Feedback
  → 人工评估的新策略版本 / 重评分
```

每次处理必须使用当时生效的 SourcePolicy 并保留版本。Source 停用后历史 provenance 不得消失；若政策要求删除，则保留最小审计 tombstone，并按政策隐藏、删除或降级派生产物。

## 内部使用与默认全文策略

P0 不把逐源重型法律审查作为 tracer bullet 的前置条件；来源未设置明确 SourcePolicy 时采用以下内部研究默认值：

- 标题、作者、时间、canonical URL、短摘录、中文摘要、推荐理由和其他派生分析可以在团队内部共享；
- 原始与规范化全文只用于内部采集处理、故障排查和短期复核，最多保留 **7 天**，`ItemView` 默认不返回全文；
- 常驻全文展示、公开分享、镜像、团队外再分发和训练默认关闭；
- 不绕过登录、付费墙、CAPTCHA 或其他访问控制，订阅凭据不得随 RawDocument 保存；
- SourcePolicy 可以进一步缩短保留期或隐藏派生产物；延长全文保留或扩大共享范围必须由项目负责人显式激活新版本；
- Source 停用或收到删除请求时必须能删除 payload，并保留说明来源、hash、时间和删除原因的最小 tombstone。

超过全文保留期后的策略回放，只能使用仍合法保留的 excerpt、元数据、hash 和历史派生记录。此类新 Score 与 Digest 必须标记 `degraded` 和缺失输入原因，禁止伪装为基于完整正文的精评。

## P0 验收

- 重放同一 endpoint 不产生重复 Item；
- 跨端点命中同一 Item 时 provenance 完整；
- 每个 Digest entry 可回溯到 Item、Score、策略版本和原始来源；
- 相同 CandidateSnapshot、策略版本与运行目的重放不现场重查，也不产生第二份 Digest；
- 回放不发布、不推进 watermark，未激活策略不得以 publish 目的运行；
- Item revision 变化后旧 Score 被标记 stale，历史 Digest 不变；
- 过期全文回放只能生成明确标记 degraded 的结果；
- 发布后的 Digest、历史 Score 和 Feedback 不被覆盖；
- RawDocument payload 到期删除后仍可解释其来源、处理时间和删除原因；
- 中文与国际来源遵守相同契约，不通过特例绕开 SourcePolicy、保留期或删除能力。
