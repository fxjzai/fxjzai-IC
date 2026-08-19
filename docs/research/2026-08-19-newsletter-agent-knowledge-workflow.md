# Newsletter → Agent → LLM Wiki 信息工作流

> 调研日期：2026-08-19  
> 来源：[铁锤人《我是如何改变阅读流程并且快速涨粉 5w 的？》](https://x.com/lxfater/status/2089282028240044171)，2026-08-17  
> 阶段：完成初步产品、信源与技术可行性核查
> 规范影响：本报告保存证据与长期机会，不直接定义 P0；当前规范以 ADR 与 P0 数据契约为准。

## 执行摘要

这套方法值得 IC 学习的不是某个邮箱客户端，也不只是新增 13 个 Newsletter，而是一条从“高信噪比订阅”到“长期可复用知识”的完整路径：

1. 人工维护可信、低噪声的源池；
2. 将 Newsletter 自动归档，批量压缩成一屏候选；
3. 每天精读少量有信息增量的内容，而不是重复消费主流观点；
4. 未精读内容仍进入可检索的原始资料层；
5. 长期可以经过证据化抽取和人工审核，增量更新知识库；
6. P1 写作 Agent 可以先读轻量索引，再下钻知识页和原文证据。

IC 可以支撑这套流程，但产品承诺应从“97 篇无人值守自动编译 Wiki”修正为：

> P0 让所有内容进入不可变原始记录与规范 Item，系统生成可解释 Digest；Claim 和综合 Wiki 作为独立 P1 产品环，只有人工接受与审核流程成立后才启动。

核心产品决定：

- Newsletter 成为一等 `Source`；
- Email、RSS、Web、API 是 `SourceEndpoint`，不是四个不同信源；
- “非共识”是阅读队列策略，不是内容真伪或质量的替代指标；
- 每日推荐同时保留 must-know 共识通道和 discovery 信息增量通道；
- 原始来源是事实基准；P0 的摘要、Score 和 Digest，以及 P1 的 Claim/Wiki 都必须一跳回源。

## 1. 原文方法拆解

原文描述的工作流是：

```text
100+ 候选源
  → 人工筛选并按产品 / 研发 / 增长分组
  → Newsletter 自动进入专用邮箱标签并跳过收件箱
  → Agent 将约 51 封邮件压缩成一屏清单
  → 划掉重复主流观点
  → 每日精读 3 篇非共识 / 新数据 / 陌生角度
  → 其余内容进入 LLM Wiki
  → 写作时先读 index，再按需读取知识页
  → 数字、人名、日期和引语回原文核验
```

原文还建议遇到关键疑问时直接回信询问作者。对 IC 而言，邮件草拟可以成为后续能力，但外发必须由人确认，且发送权限应与内容处理 worker 隔离。

### 值得吸收

- 用主动订阅替代算法信息流，重新获得信源选择权；
- 先做跨源压缩和去重，再投入人类注意力；
- 把“是否值得精读”和“是否值得保存”分开；
- 从一开始设计长期知识沉淀，而不是只生成会过期的日报；
- 写作时采用 index-first 分级检索并回溯证据。

### 需要修正

- “非共识”不等于正确，高噪声异见可能只是在制造争议；
- “100 篇的知识量”是作者的效果表达，不是经验证的召回或学习指标；
- 邮件订阅不自动授予团队共享、模型处理、长期保存或商业再输出权利；
- 自动把大量来源改写进综合 Wiki 会放大幻觉、冲突和错误覆盖；
- 13 个源只是一个职业画像下的优质样本，不代表全球科技情报的完整覆盖。

## 2. 13 个信息源核查

分级只表示 IC 的接入优先级：T1 为结构化、持续、适合直接验证；T2 为高价值补充、低频、重复较多或授权/接入风险较高。

| 信息源 | 主要价值 | 接入方式 | 关键限制 | 建议 |
| --- | --- | --- | --- | --- |
| [One Useful Thing](https://www.oneusefulthing.org/) | AI 工作与教育的研究型分析，低频 | [RSS](https://www.oneusefulthing.org/feed)、邮件、网页 | Substack；不应镜像正文或批量长期保存 | T2 |
| [Ben's Bites](https://www.bensbites.com/) | AI Agent、工具与产品早期信号 | [RSS](https://www.bensbites.com/feed)、邮件、网页 | 免费与付费内容并存，不得绕过权限 | T1 |
| [Product Hunt](https://www.producthunt.com/) | 每日新产品原始发布流 | [Atom](https://www.producthunt.com/feed)、[GraphQL API](https://api.producthunt.com/v2/docs) | API 默认不允许商业用途、要求归因并受限流；优先验证 Atom | T1 |
| [The Batch](https://www.deeplearning.ai/the-batch) | AI 新闻与研究解读，周更 | 邮件、网页归档；无官方 RSS | DeepLearning.AI 条款偏个人非商业；仅保留摘要和链接 | T1 内容 / T2 接入 |
| [Simon Willison](https://simonw.substack.com/) | LLM、Web、Python、SQLite 等一手工程信号 | [RSS](https://simonw.substack.com/feed)、邮件、网页 | Substack 通用限制 | T1 |
| [Interconnects](https://www.interconnects.ai/) | 模型训练、开放生态和研究战略 | [RSS](https://www.interconnects.ai/feed)、邮件、网页 | 付费深度内容不可采集或再分发 | T1 |
| [Import AI](https://importai.substack.com/) | 研究、产业与政策交叉信号，周更 | [RSS](https://importai.substack.com/feed)、邮件、网页 | Substack；部分付费/提前访问内容 | T1 |
| [Ahead of AI](https://magazine.sebastianraschka.com/) | ML/LLM 工程实现与教程，低频深度 | [RSS](https://magazine.sebastianraschka.com/feed)、邮件、网页 | Substack；代码、论文需分别核验许可证 | T2 |
| [Hugging Face Daily Papers](https://huggingface.co/papers) | 每日热门论文与社区讨论 | 网页、邮件、[`/api/daily_papers`](https://huggingface.co/api/daily_papers) | API 未正式文档化，字段和稳定性需容错；论文许可证各自独立 | T1 |
| [TLDR AI](https://tldr.tech/ai) | 工作日高频 AI 综合摘要 | 邮件、[网页归档](https://tldr.tech/ai/archives) | 未发现 RSS；内容版权保留，不应镜像全文 | T1 |
| [The Rundown AI](https://www.therundown.ai/) | 工作日 AI 新闻与应用方法 | 邮件、[Beehiiv RSS](https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml) | 上线前再次确认 Feed 归属和条款 | T1 |
| [Marcus on AI](https://garymarcus.substack.com/) | AI 可靠性、治理和反方观点 | [RSS](https://garymarcus.substack.com/feed)、邮件、网页 | 观点性强，应标为评论；付费归档不可采集 | T2 |
| [Last Week in AI](https://lastweekin.ai/) | 周度回顾与漏报检查 | [RSS](https://lastweekin.ai/feed)、邮件、网页、音频 | 更新节奏不稳定，与日更综合源重复较高 | T2 |

共同限制：上述多个 Substack 源虽公开 `/feed`，但 [Substack ToS](https://substack.com/tos) 对 crawl/scrape、大量存储、复制分发和商业利用有明确限制。RSS 可访问不等于取得正文再利用许可。

IC 默认应只长期保存：来源身份、标题、作者、发布时间、canonical URL、源自带短摘要、抓取时间、内容指纹、内部评分和结构化分析。需要处理全文时采用来源级策略和保留期，不展示镜像、不绕过登录/付费墙、不向团队外再分发。

## 3. Karpathy LLM Wiki 核实

“LLM Wiki”存在一手公开说明，但它是一个供 Agent 实例化的设计模式，不是现成、完整、带明确软件许可证的产品：

- [Karpathy 原始说明](https://x.com/karpathy/status/2039805659525644595)描述 `raw/ → LLM 编译 Markdown Wiki → index/CLI 查询 → 输出重新归档`；
- [后续回复](https://x.com/karpathy/status/2039812403962253744)明确说当前不是 fully autonomous，尤其早期逐篇加入并保留 human-in-the-loop；
- [llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)把系统分为 Raw sources、Wiki、Schema，操作包括 Ingest、Query、Lint；
- 中等规模可先读 `index.md` 再下钻；规模增长后可叠加类似 [qmd](https://github.com/tobi/qmd) 的 BM25、向量和重排检索。

因此，原文所说“概念、主张、开放问题卡片”是对该模式的进一步产品化解释，不是 Karpathy 唯一指定的数据模型。

## 4. 对 IC 规范模型的影响

Newsletter 是 `Source`；Email、RSS、Web、API 是同一 Source 的 `SourceEndpoint`。`Issue` 只有来源真实存在“期次”时才作为 Item 的可选父级，普通 RSS 文章不得硬造 Issue。

本次调研触发了分期收敛，而不是把所有知识实体塞入 P0：

| 阶段 | 实体 | 目的 |
| --- | --- | --- |
| P0 | `Source`、`SourceEndpoint`、`SourcePolicy`、`RawDocument`、`Item`、可选 `Issue`、`Score`、`Digest`、`Feedback` | 可信采集、可解释筛选、回源和校准 |
| P0.5 | `Story` | 验证跨源事件聚类 |
| P1 | `Claim`、`KnowledgeCard`、`KnowledgeIndex` | 长期知识编译与 index-first 检索 |
| P2 | `Concept`、`OpenQuestion`、多租户 `WorkspaceSubscription` | 知识本体与商业化能力 |

`SelectionDecision` 暂时只是 Select implementation 的内部审计记录。原文所谓 Source Card 是 `Item` 的可追溯读取视图，不增加新实体。完整规范见 [P0 数据契约](../specs/p0-domain-contract.md)。

## 5. P0 每日筛选设计

“反主流”不能直接当成质量。P0 先在 Item 层验证双通道选择，避免为了日报强制引入 Claim/Wiki：

- `must_know`：至少一个高影响、高可信、来源覆盖充分的内容；
- `discovery`：至少两个相对近期历史更少见、含一手证据或具有不同视角的内容；
- 使用 URL、内容 hash、标题/摘要相似度和来源家族做确定性去重与候选去重；
- 展示权威度、相关性、新颖度、证据质量和行动价值等 reason codes；
- 被排除的 Item 仍可读取和恢复，不能把“划掉”理解为删除。

新颖度只相对于团队已有 Item 与阅读历史，不是普适真值；点赞和转发量不能替代共识。Claim 级支持/冲突关系属于 P1 研究。

## 6. 分阶段处理管线

### P0 运行路径

```text
Newsletter Source
  → Email / RSS / Web / API Endpoint
  → 不可变 RawDocument
  → 可选 Issue + 规范 Item
  → 版本化 Score
  → must-know + discovery Digest
  → Item 读取视图 + 回源
  → Feedback → 人工校准与策略回放
```

每个输出记录内容 hash、模型、提示词、策略版本、token 与人工处置。邮件正文是不可信输入，处理 worker 不拥有发信、写邮箱或任意浏览器权限。

### P1 知识扩展

```text
人工接受的 Claim
  → Claim 对齐、冲突与证据比较
  → KnowledgeCard diff
  → 人工审核
  → KnowledgeIndex
  → index + BM25/embedding → 页面 → 原文
```

模型只生成受影响页面的 diff，不整库重写。综合 Wiki 是可重建派生物，原始来源始终是事实基准。

## 7. 验证计划

### P0-E0：多端点采集

对 13 个源记录 Email 与可用 RSS/Web/API 的到达率、延迟、正文完整率、链接解析率和重复率。

- Item 摄取幂等率 100%；存在 edition 语义时额外验证 Issue；
- 正文可解析率 ≥95%；
- 跨端点确定性去重准确率 ≥98%；
- 单 endpoint 失败可逐源发现，并能按优先级降级。

### P0-E1：Ingest 契约

抽样约 100 个 Item，核对 canonical URL、发布时间、来源身份、内容 hash、多个 RawDocument provenance 与保留策略；重复投递、崩溃恢复和解析失败不得产生重复 Item。

### P0-E2：双通道 Digest 回放

至少运行 10 个工作日，对比“权威度 + 相关性”baseline 与 must-know/discovery 策略，评价 `precision@3`、must-know recall、重复条数、阅读时间、人工恢复率和 7 天后漏报率。

### P0-E3：Feedback 闭环

验证反馈能回到当时的 Item、Digest、Score 和策略版本；策略调整必须先在历史候选与反馈上回放，再由人激活新版本。

### P1 实验

P0 使用率与反馈成立后，再评估 Claim 边界、证据 span、KnowledgeCard citation coverage，以及“全库上下文”对比“index → 页面 → 原文”的 token 与引用正确率。

## 8. 分阶段建议

### P0

- 专用收件地址或显式转发，不连接个人全量邮箱；
- Newsletter 作为 Source，Email/RSS/Web/API 作为 SourceEndpoint；
- RawDocument、规范 Item、provenance 和 SourcePolicy；
- 版本化 Score、双通道 Digest 和最小 Feedback；
- Item 读取视图、逐源健康状态和成本审计。

### P0.5

- 在真实数据证明跨源聚类质量后，引入 Story；
- 微信公众号 connector 只有在合规、稳定和多上游方案成立后进入生产范围。

### P1

- Claim 与原文证据 span；
- KnowledgeCard 版本、diff 审核、回滚和 correction pin；
- KnowledgeIndex 与 index/BM25/embedding 并集检索；
- 写作引用审计和事实复核。

### P2

- Concept/OpenQuestion 与跨语种 Claim 演化；
- 多租户、自带订阅凭据、权限和保留策略；
- 写作工作台与经人工确认的作者问询草稿。

P0 不需要图数据库，也不应因 P1 假设预先建设知识 module。

## 9. 隐私、版权与商业化

- Gmail 的读取、修改和设置 scopes 属于受限权限；首版只读专用标签，过滤器由用户确认后自行创建，避免申请不必要的修改权限。参考 [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)。
- 邮件进入模型前移除收件人、退订 token、追踪像素和个性化链接；HTML 默认不加载远程资源。
- 原始邮件只向有订阅权的 workspace 成员开放，支持保留期、来源删除和访问审计。
- 收到订阅邮件不等于获得全文再分发、团队共享或商业派生许可；每个 Source 分别记录并执行策略。
- 商业化优先采用 BYO subscription/credential：客户自己订阅，IC 售卖连接器、筛选、知识工作流和私有部署，而不是集中采购后向多个客户分发正文。
- 对外共享源库只共享 source metadata 和 connector recipe，不共享受限正文。

## 10. 当前结论

将 Newsletter 作为 Source、Email/RSS/Web/API 作为 SourceEndpoint 纳入 P0 值得做，因为它补足了高质量定向订阅、一期内容和作者身份的表达能力。

首个原型只验证“可信采集 + 跨源压缩 + 可解释 Digest + 回源 + Feedback”。P0 的采集质量、团队阅读使用率和校准闭环成立后，再启动独立 P1 知识编译实验。
