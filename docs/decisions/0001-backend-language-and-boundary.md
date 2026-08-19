# ADR-0001：后端主语言与运行形态

- 状态：Proposed
- 日期：2026-08-19
- 决策范围：IC MVP 的后端语言、运行形态和基础设施起点

## 背景

IC 的 P0 需要处理 RSS、Newsletter/Email、网页、GitHub 和可持续中文源，并完成增量采集、规范化、筛选、Digest 与反馈闭环。Story 聚类属于 P0.5；Claim 与长期知识编译属于 P1，不是本 ADR 的 MVP 前提。

首轮调研确认 Horizon 是 Python 3.11 的批处理管线。它的部分 fetcher、解析工具和模型 provider 值得验证，但 Horizon 的 `ContentItem`、评分语义和整条“抓取 → 评分 → Markdown”管线不构成 IC 的领域 interface。

MVP 预计每天处理 500–2,000 条内容。主要等待来自上游网络、内容解析和模型调用，而不是 HTTP 请求的纯计算吞吐。

## 建议决策

### 语言

- 后端主语言：**Python 3.11+**；MVP 优先采用与待验证 Horizon 代码兼容的版本；
- Web 前端：**TypeScript**；
- MVP 不引入 Go/Rust 等第三种服务语言，除非压测证明存在无法通过有界并发、批处理或进程拆分解决的瓶颈。

### 运行形态

采用 **模块化单体 + 独立 worker 进程**：共享领域契约和 PostgreSQL，但 API 与长任务不在同一执行路径。

`api`、`worker`、定时触发器是部署与运行拓扑，不等于领域 module。P0 只承认由 [ADR-0002](./0002-p0-scope-and-module-interfaces.md) 定义的 `Ingest`、`Select`、统一读取能力面和最小 Feedback interface；P1 的 `Compile` 通过验证后再 scaffold。

目录结构、Python package 名称和进程数量不是本 ADR 的规范内容，不应据此前置拆分 `intelligence/knowledge/scheduler` 等假想 module。

### 基础组件

| 能力 | MVP 建议 | 说明 |
| --- | --- | --- |
| HTTP | FastAPI | 适合异步 I/O、Pydantic 和 Python 内容处理生态 |
| seam 数据校验 | Pydantic | 在 module interface 所在 seam 校验输入与输出 |
| 主数据库 | PostgreSQL | 事务、JSON、全文检索和增量状态 |
| 向量检索 | pgvector，按需启用 | P0 不因长期知识编译预先建设独立向量数据库 |
| 数据迁移 | Alembic | schema 演进可审计 |
| 全文检索 | PostgreSQL FTS | P0 足够；真实召回或规模不达标时再评估 OpenSearch |
| 任务执行 | PostgreSQL-backed queue，具体库待选 | 必须验证租约、重试、幂等、死信和可观测性 |
| 缓存 | 暂不默认引入 Redis | 有明确热点、分布式协调或限流需求后再加入 |

任务队列库仍是开放问题；“使用 PostgreSQL”不等于已经选择 Procrastinate、pgqueuer 或自研实现。

## Horizon 角色

Horizon 是 `Ingest` 内部 fetch seam 的候选 adapter/implementation，不是独立领域 module，也不是 IC 的整管线入口。

优先验证的复用范围：

- 单个来源的 fetcher；
- 正文解析和清洗工具；
- 模型 provider routing，可作为 `Select` 的内部实现细节。

默认不复用为 IC 契约的部分：

- Horizon `ContentItem` 作为 IC `Item`；
- Horizon URL 去重作为 IC canonical ID 规则；
- Horizon 评分作为 IC 的最终 `Score`；
- Horizon Markdown/Pages 输出作为 IC `Digest`；
- 整条 pipeline 包装成一个浅 adapter。

具体 interface、错误模式与替换条件见 [ADR-0002](./0002-p0-scope-and-module-interfaces.md)。字段、ID 与保留策略见 [P0 规范数据契约](../specs/p0-domain-contract.md)。

## 为什么首选 Python

- 可直接验证 Horizon 的 fetcher、解析和 provider 代码，不先引入跨语言调用；
- RSS、MIME/Email、网页/PDF、NLP、Embedding、聚类和 LLM SDK 生态完整；
- 当前负载以 I/O 与模型等待为主，Python 足以满足目标规模；
- 研究阶段会频繁调整解析与筛选实现，Python 迭代成本较低；
- 单一后端语言减少部署、追踪和契约维护成本。

## 未选择的方案

### TypeScript 全后端

前后端语言统一有工程优势，但会迫使团队重写 Horizon 相关能力或过早建立 Python/TypeScript 跨语言 seam。

### Go 或 Rust 主后端

适合经证据确认的高吞吐 connector。当前没有性能数据支持额外语言与部署复杂度；未来可以局部替换热点 adapter，而不是重写领域核心。

### 多语言微服务

领域 interface 与负载尚未稳定时，微服务会把不稳定模型复制成 RPC。MVP 先保持深模块和进程隔离，出现真实组织或扩展压力后再拆。

## 影响与风险

正面影响：

- 最快开展真实信源的端到端验证；
- AI 与内容处理逻辑在同一类型与测试体系内演进；
- IC 不被 Horizon 内部模型和评分语义锁定；
- 保留按真实瓶颈拆进程、替换 adapter 或局部改用其他语言的空间。

代价与风险：

- CPU 密集任务必须批量化或放入 worker，不能阻塞同步读取；
- Python 类型约束需要 Pydantic、静态检查和 module interface 契约测试；
- 模块化单体必须依赖明确 interface，而不是靠目录名维持；
- PostgreSQL-backed queue 的库和失败语义尚未选定。

## 转为 Accepted 的验证门槛

1. Python 能完成 P0 中英文混合 tracer bullet；
2. 日处理 500、1,000、2,000 条下的延迟、恢复、模型成本和资源占用达标；
3. worker 崩溃、重复投递、限流与单源失败不会制造重复 Item 或 Digest；
4. Horizon 与至少一个非 Horizon 的生产 Fetch adapter 通过同一 Ingest 契约测试；
5. PostgreSQL FTS（必要时叠加 pgvector）满足 P0 搜索质量和响应时间；
6. 任务队列候选通过租约、重试、死信、幂等和观测性实验。

如果 Python 无法在合理资源内满足目标，或 Horizon 的可复用价值在删除测试中不足，再重新评估语言或实现范围。
