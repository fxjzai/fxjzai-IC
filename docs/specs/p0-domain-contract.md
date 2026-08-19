# P0 规范数据契约

- 状态：Draft
- 日期：2026-08-19
- 适用范围：IC P0 的领域角色、最小字段、ID、去重、溯源与保留约束

本文是调研期的规范 interface。文中的“必须 / 不得 / 可以”分别表示 MUST / MUST NOT / MAY。数据库表结构、模块目录和具体 Python 类型不属于本契约。

## 权威性与分期

| 阶段 | 规范实体 | 产品边界 |
| --- | --- | --- |
| P0 | `Source`、`SourceEndpoint`、`SourcePolicy`、`RawDocument`、`Item`、可选 `Issue`、`Score`、`Digest`、`Feedback` | 可信采集、幂等归一化、可解释精选、回源与反馈 |
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
| `Item` | `id`、`source_id`、`issue_id?`、`title`、`authors[]`、`published_at?`、`discovered_at`、`canonical_url?`、`language`、`excerpt?`、`content_hash`、`body_ref?`、`provenance[]`、`status` | 规范内容原子；provenance 至少关联 RawDocument，有条件时带原文 selector/span |
| `Score` | `id`、`item_id`、`dimensions`、`total?`、`reason_codes[]`、`rationale?`、`model_id`、`prompt_version`、`policy_version`、`input_hash`、`created_at` | 一次版本化判断，不是 Item 的固有属性 |
| `Digest` | `id`、`kind`、`period_start`、`period_end`、`timezone`、`policy_version`、`status`、`entries[]`、`generated_at`、`published_at?` | 精选流、日报和 Top 3 的统一产物；entry 至少含 `item_id`、`score_id?`、`rank`、`channel?`、摘要与推荐理由 |
| `Feedback` | `id`、`actor_id`、`target_type`、`target_id`、`label`、`reason_code?`、`comment?`、`created_at`、`applied_policy_version?` | 人对 Item 或 Digest entry 的追加式校准事件；label 至少支持 `useful/useless/misclassified` |

`SourceEndpoint.kind` P0 至少允许 `rss/email/web/api`；`wechat` 只有存在通过合规评估的真实 adapter 时才启用，不能因枚举存在就宣称支持。

## 关键不变量

1. 一个 Source 可以有多个 SourceEndpoint；端点失败、替换或降级不得改变 Source 身份。
2. RawDocument 与 hash 必须追加记录，不得覆盖。保留期届满可以删除 payload，但必须留下 hash、来源、时间和删除原因的 tombstone。
3. 一个 Item 可以关联多个 RawDocument，表达 Email、RSS、Web 等跨端点重复到达；任何摘要、评分和推荐理由必须沿 `Item → provenance → RawDocument/source_url` 回源。
4. 普通 RSS、网页文章和 GitHub release 不得为满足统一结构而伪造 Issue。
5. 模型不得修改 Source、RawDocument 或 Item 的事实字段。解析修正形成可审计 revision；Score、Digest 和 Feedback 均追加保存。
6. Score 必须绑定完整输入 hash、模型、提示词和策略版本；新评分不得覆盖旧评分。
7. Digest 在 draft 状态可以重算；发布后冻结，修正产生新版本。每个 entry 必须同时给出回源、摘要和入选理由。
8. Feedback 不得直接改写历史 Score 或 SourcePolicy，只能进入人工处置、策略提案或重评分流程。
9. 跨 Source 的语义相似内容在 P0 不得合并成一个 Item；后续由 Story 表达事件聚类，避免把“相似”误当成“同一内容”。

## ID 与去重所有权

IC 拥有全部内部 ID、canonicalization、去重和幂等规则。Fetch adapter 只报告外部标识、原始字节和传输元数据，不得决定 Item ID、合并结果或最终 Score。

- 内部 `id` 使用无业务含义、不可变的全局 ID；URL、标题和内容 hash 不直接充当主键；
- Source 由人工审核的 `canonical_key` 唯一标识；
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
  → 追加 Score
  → 生成并发布 Digest
  → 追加 Feedback
  → 人工评估的新策略版本 / 重评分
```

每次处理必须使用当时生效的 SourcePolicy 并保留版本。Source 停用后历史 provenance 不得消失；若政策要求删除，则保留最小审计 tombstone，并按政策隐藏、删除或降级派生产物。

## 默认全文策略

来源未完成授权审核时采用最小权限默认值：

- 原始与规范化全文只用于内部瞬时处理，最多保留 **7 天**；
- 团队全文展示、外部共享、镜像、再分发和训练默认关闭；
- 长期仅保留来源身份、标题、作者、时间、canonical URL、短摘录、内容 hash、采集时间、内部 Score/Digest/Feedback 和 provenance；
- 付费墙、个人订阅邮件和微信公众号不得因“技术上可获取”放宽策略；
- 只有经审核的 SourcePolicy 新版本可以延长保留或扩大使用范围，且不能追溯性地为过去未获授权的处理补权。

## P0 验收

- 重放同一 endpoint 不产生重复 Item；
- 跨端点命中同一 Item 时 provenance 完整；
- 每个 Digest entry 可回溯到 Item、Score、策略版本和原始来源；
- 发布后的 Digest、历史 Score 和 Feedback 不被覆盖；
- RawDocument payload 到期删除后仍可解释其来源、处理时间和删除原因；
- 中文与国际来源遵守相同契约，不通过特例绕开 SourcePolicy。

