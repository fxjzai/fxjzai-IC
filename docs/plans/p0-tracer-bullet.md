# P0 第一发 tracer bullet

- 状态：Draft / 等待两个选择
- 日期：2026-08-19
- 目的：用最少变量验证 Ingest seam 与 `Ingest → Snapshot → Select → Digest → Feedback` 闭环

## 这次验证什么

1. `NativeRssFetchAdapter` 与一个 Horizon fetcher 能否通过相同 Ingest 契约；
2. RawDocument、Item revision、确定性去重和 provenance 是否可靠；
3. CandidateSnapshot 是否持久化、封闭且可重放；
4. Score/Digest 是否绑定 Item content hash；
5. 过期正文回放是否明确 degraded；
6. Feedback 是否回到原 Digest entry、Score 和策略版本。

## 这次不验证什么

- 不接 Email、Gmail OAuth 或 MIME；
- 不接微信公众号或未完成合规审核的中文源；
- 不选择生产任务队列，先由 CLI/测试 harness 直接触发 worker 入口；
- 不做 Story、Claim、Wiki、权限和 UI；
- 不做 500–2,000 条/日压测。

第一发通过后，再分别验证中文来源、Email、任务队列和目标负载，避免失败时无法定位是哪一层。

## 选择 1：具体来源组合

### A. 官方新闻 + 社区信号（推荐）

- Native RSS：[OpenAI News RSS](https://openai.com/news/rss.xml)，2026-08-19 实测 HTTP 200；
- Horizon：Hacker News fetcher。

优点：无登录、无邮箱权限、来源类型差异明显；可以直接检验 Horizon fetcher 能否保留 RawDocument/provenance，而不是包装 `ContentItem`。缺点：两个来源主题密度和内容形态不同，不适合比较召回率，只适合验证 interface。

### B. 产品发布 + 开源发布

- Native RSS：[Product Hunt Atom](https://www.producthunt.com/feed)，2026-08-19 实测 HTTP 200；
- Horizon：GitHub Releases fetcher。

优点：更贴近产品和开源发布情报，后续 Digest 更直观。缺点：GitHub 限流/Token 和 Product Hunt 使用约束增加变量；必须只按 SourcePolicy 保存允许字段。

### C. 中文优先

- Native adapter：一个完成 ToS、版权、保留期与稳定性审核的中文官方源；
- Horizon：Hacker News fetcher。

优点：更早验证产品核心诉求。缺点：当前没有已冻结的中文候选，会把来源合规审计与 adapter seam 同时放进第一发，不推荐作为故障定位起点。

## 选择 2：第一发如何暴露 endpoint health

### A. 只进入 IngestResult + 日志/指标（推荐）

第一发记录成功率、最后观察时间、失败类型、重试时间和延迟，不新增运维读取 interface。验证 health 语义稳定后再决定是否产品化。

### B. 增加最小内部 ops 读取

增加只读 `get_endpoint_health(endpoint_ref)`，返回最后成功、当前状态、连续失败数、错误类型和建议重试时间。适合调试，但会扩大第一发 interface。

### C. 直接做运维页面

同时建设来源列表和健康页面。可见性最好，但会引入 UI、授权和查询设计，不推荐用于第一发。

## 执行序列

### TB1：两个 Fetch adapter

1. 为两个 Source 建立 SourcePolicy 与 SourceEndpoint；
2. 各运行 Ingest 两次；
3. 模拟重复响应、超时、限流、损坏内容和内容修订；
4. 比较两个 adapter 的 IngestResult 与持久化结果。

成功判据：

- 重复投递不产生重复 Item；
- 真实修改产生新 revision，旧 provenance 不丢失；
- 单文档失败进入隔离区，不回滚其他内容；
- checkpoint 只在 RawDocument、Item 和 provenance 提交后推进；
- 两个生产 adapter 通过同一组 Ingest interface 测试。

### TB2：Snapshot → Digest

1. 分别创建 `daily` 与 `stream` SelectionScope；
2. 持久化 CandidateSnapshot，确认每个 ref 绑定 `item_id + content_hash`；
3. 用固定策略生成 Digest；
4. 重放相同 snapshot/policy；
5. 修改一个 Item 形成 revision 后再次评估；
6. 模拟 RawDocument 正文已过 7 天保留期。

成功判据：

- 相同 snapshot/policy 不生成第二份 Digest；
- daily 按 timezone + period 封闭，stream 按 watermark + limit 封闭；
- 一次 select 只生成一种 Digest kind；
- Item revision 后旧 Score 标 stale，历史 Digest 不变；
- 缺少全文的新评估标记 degraded，不假装完整精评。

### TB3：读取与 Feedback

1. 通过 `get_item` 与 `get_digest` 读取结果；
2. 对一个 Digest entry 记录“有用”，对另一个记录“误判”；
3. 重复提交同一幂等键；
4. 生成反馈聚合报告并执行一次人工策略回放。

成功判据：

- ItemView 显示 content hash、Source、Score 版本、provenance 与 payload 可用状态；
- Feedback 固化 `digest_entry + item/content_hash + score + policy`；
- 重复反馈只产生一条记录；
- Feedback 不同步修改线上策略；新策略必须经过回放和人工激活。

## 失败即停止条件

- Horizon fetcher 无法在不伪造 RawDocument 的情况下保留原始响应/provenance；
- 两个 adapter 需要不同的 Ingest 外部 interface；
- 重放会产生重复 Item、Digest 或推送；
- CandidateSnapshot 无法持久化封闭候选集；
- Digest entry 无法稳定绑定 Item revision 和 Score；
- 7 天删除策略导致系统无法诚实表达 degraded。

出现停止条件时先修改 Draft 契约或缩小 Horizon 复用范围，不继续 scaffold 上层功能。

