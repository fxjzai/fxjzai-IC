# 调研索引

本目录沉淀 IC 在选型、信源、处理流程、产品设计和合规方面的证据与阶段性结论。

Research 报告保存证据、观察和当时假设，不是规范 interface。当前产品共识以根 README 为入口，技术决定以 ADR 为准，字段和不变量以 `docs/specs/` 为准。后续决定推翻研究建议时，报告保留原始证据并明确标注规范影响。

## 文档约定

- 区分 **已验证**、**推断** 与 **待验证**，不把宣传文案当作实现事实；
- 优先引用官方页面、固定 commit 源码和实际接口响应；
- 每份报告记录调研日期、基线、结论、风险和下一步实验；
- 新证据改变判断时，更新原报告并在 Git 历史中保留变化；
- README 只保留当前共识，完整证据放在本目录。

## 报告

| 日期 | 主题 | 状态 | 后续规范影响 |
| --- | --- | --- | --- |
| 2026-08-19 | [首轮平台基线：AIHOT / Horizon / ai-news-aggregator](./2026-08-19-platform-baseline.md) | 已完成初步代码与接口核查 | Horizon 整管线 worker 假设已收窄为 Ingest 内部 adapter/implementation，待 tracer bullet 验证 |
| 2026-08-19 | [Newsletter → Agent → LLM Wiki 信息工作流](./2026-08-19-newsletter-agent-knowledge-workflow.md) | 信源与可行性核查完成 | Newsletter/Endpoint 进入 P0；Claim/Wiki 按后续 ADR 延至 P1 |
