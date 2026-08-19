# IC 情报领域

IC 将分散的国内外科技内容转化为可追溯、可筛选并能接受团队反馈的内部情报。本文件只定义项目领域语言；字段和行为不变量以 `docs/specs/` 为准。

## Language

**Source**:
内容生产者或出版品牌的稳定身份，与具体接入地址无关。
_Avoid_: Feed、Channel、Connector

**SourceEndpoint**:
某个 Source 的具体采集入口，例如 RSS URL、邮箱、网页或外部接口。
_Avoid_: Source、Feed Source

**SourcePolicy**:
某个 Source 在特定版本下的采集、处理、保留、展示和删除规则；它不是法律授权证明。
_Avoid_: Compliance Approval、Permission Flag

**RawDocument**:
一次采集得到的不可变原始响应，是内容溯源起点。
_Avoid_: Item、SourceDocument

**Issue**:
Newsletter 或期刊中真实存在的一期，可作为多个 Item 的父级；没有期次语义时不存在。
_Avoid_: Batch、Fetch Run

**Item**:
IC 中可独立引用和评估的规范内容原子，事实变化以 revision 表达。
_Avoid_: RawDocument、Story、Source Card

**CandidateSnapshot**:
在一个确定 scope 下持久化并封闭的 Item revision 集合，是筛选与策略回放的固定输入。
_Avoid_: Live Query、Candidate List

**Score**:
某个模型和策略版本针对一个 Item revision 形成的追加式评估记录。
_Avoid_: Item Quality、Current Score

**Digest**:
由固定候选快照生成的可发布精选产物；精选流、日报和 Top 3 都是 Digest 的不同 kind。
_Avoid_: Story、Newsletter Issue

**Feedback**:
团队成员对 Item 或 Digest entry 提交的追加式校准事件，不会同步改写线上策略。
_Avoid_: Vote、Automatic Calibration

**Story**:
P0.5 中表达多个 Source 围绕同一事件的聚类，不用于合并 Item。
_Avoid_: Item、Digest

**Claim**:
P1 中从证据提炼、经人工接受后才能进入长期知识编译的可核验主张。
_Avoid_: Summary、Fact

**KnowledgeCard**:
P1 中围绕稳定知识主题维护、带证据与版本差异的长期知识产物。
_Avoid_: Digest、Wiki Page Dump
