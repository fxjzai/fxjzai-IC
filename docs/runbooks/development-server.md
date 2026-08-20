# IC 开发服务器 Runbook

- Target：`mckj@192.168.100.189:/data/fxjzai-ic`
- Web 工作台：`http://192.168.100.189:18000/`
- API docs：`http://192.168.100.189:18000/docs`
- Runtime：Docker Compose、Python 3.12、PostgreSQL 16
- Host：Ubuntu 24.04 KVM，20 vCPU，39 GiB RAM，RTX 3060 12 GiB

不要在文档、仓库或命令历史中保存 SSH 密码和数据库密码。数据库只绑定服务器回环地址的 `18432`；API 绑定内网 `18000`。

## Start

```bash
cd /data/fxjzai-ic
docker compose up -d --build
```

## Stop

```bash
cd /data/fxjzai-ic
docker compose stop
```

`stop` 保留 PostgreSQL volume。只有明确决定丢弃全部 IC 数据时才能使用 `docker compose down -v`。

## Restart

```bash
cd /data/fxjzai-ic
docker compose restart api
```

代码或依赖变化后使用：

```bash
cd /data/fxjzai-ic
docker compose up -d --build api retention
```

## Health

```bash
curl --fail --silent http://127.0.0.1:18000/health
docker compose ps
```

健康响应至少包含 `{"status":"healthy"}`，`postgres` 与 `api` 容器均应显示 healthy。
`retention` 应显示 running；它在 API 完成迁移并健康后启动，随后每小时执行一次全文保留清理。

部署后还应访问 `/`，确认返回 HTML 且能看到“今天，值得注意什么？”；从首页至少打开一个 Item。存在已发布 Digest 时，再打开 Digest 详情并实测一枚反馈按钮。工作台没有同步 Ingest 按钮，这是有意保持的读写边界。

## Logs

```bash
cd /data/fxjzai-ic
docker compose logs --tail=200 api
docker compose logs --tail=100 postgres
docker compose logs --tail=100 retention
docker compose logs --follow api
```

## Run the tracer flow

```bash
docker compose run --rm api ic ingest openai-news-rss

docker compose run --rm api ic ingest horizon-hacker-news

# retention 容器会每小时自动运行；需要立即验证时可手动执行一次
docker compose run --rm api ic retention
```

随后以当日 UTC 时间窗创建 snapshot；从响应复制 `snapshot_id`，再生成 Digest：

```bash
curl --fail --silent --request POST \
  --header 'content-type: application/json' \
  --data '{"kind":"daily","period_start":"2026-08-18T16:00:00Z","period_end":"2026-08-19T16:00:00Z","timezone":"Asia/Shanghai","limit":50}' \
  http://127.0.0.1:18000/v1/snapshots

curl --fail --silent --request POST \
  --header 'content-type: application/json' \
  --data '{"snapshot_id":"<snapshot-id>","policy_version":"tracer-policy-v1"}' \
  http://127.0.0.1:18000/v1/digests
```

## Storage and process checks

```bash
df -hT /data
docker system df
docker compose top
docker compose exec postgres pg_isready -U ic -d ic
```

2026-08-19 初检时 `/data` 所在根文件系统已使用 90%，剩余约 101 GiB。不得清理其他项目、Docker volume、模型或镜像；空间不足时先做只读盘点并确认目标。

## Recovery

- API 不健康：先看 `docker compose logs --tail=200 api`，确认数据库 URL 与 schema 初始化错误。
- PostgreSQL 不健康：检查 volume 权限、磁盘余量和 `docker compose logs postgres`，不要删除 volume。
- 上游限流或超时：`IngestResult.health` 应为 `failed/degraded`，checkpoint 不推进；等待后重放相同 endpoint。
- 重复调用：Ingest、Digest 和 Feedback 按契约幂等，正常重放不得产生第二份事实记录。

## Service state

本节在每次部署后更新，记录当前 commit、容器状态和真实请求结果；不得只根据容器启动成功判断 tracer bullet 通过。

### 2026-08-20 首次部署记录

| 项目 | 实际状态 |
| --- | --- |
| 部署时间 | 2026-08-20 15:01 CST（07:01 UTC 完成验收） |
| 部署 commit | `39729a6`（`fix: enforce database write dependencies`） |
| `docker compose ps` | `postgres` healthy、`api` healthy、`retention` running；只存在这 3 个 `fxjzai-ic` 容器 |
| `/health` 实际响应 | 从开发机访问 `http://192.168.100.189:18000/health` 返回 `{"status":"healthy","version":"0.1.0"}` |
| 数据库 | PostgreSQL 首次应用 migration `9cbf6fa0e8aa (head)`；`.env` 权限 `0600`，数据库端口仅绑定 `127.0.0.1:18432` |
| OpenAI News RSS Ingest | 首次：1 个 RawDocument、1,141 个新 Item、0 错误；第二次：0 新 Item、1,141 duplicate、0 错误 |
| Horizon Hacker News Ingest | 首次：16 个 RawDocument、15 个新 Item、0 错误；第二次命中 ETag：0 RawDocument、0 Item，checkpoint 不变 |
| Snapshot / Digest | HN 窄窗口持久化 15 条候选；同一 Snapshot + policy 重放返回同一 Digest `0af9a723-adc0-5c85-83d7-9c139ce5dad2`；Item 与 Digest 读取均通过 |
| Feedback | `useful` 首次写入成功；同一 idempotency key 重放返回同一 Feedback，`created=false` |
| Calibration | v2 replay 未推进 stream watermark，线上仍读取 v1；激活前 publish 被拒绝；人工激活后 v2 Digest 发布并成为线上 Score |
| Retention | 自动服务与手工命令均成功；当前内容未到期，清理计数为 0 |

### 当前已知限制

- OpenAI News RSS 本次返回 1,141 条历史内容且没有 ETag；重放保持数据幂等，但仍会下载、解析整份 feed。应在后续 adapter 中增加有界历史策略或可靠的条件请求依据。
- HN 的部分条目没有正文，因此 v1、v2 Digest 均诚实标记为 `degraded`；不是服务健康故障。
- `/metrics` 可从开发机读取，但一次性 `docker compose run` Ingest worker 的进程内指标不会自动聚合到 API 进程。当前逐次结果以 CLI JSON、endpoint checkpoint 和数据库审计为准；P0 后续应接入 Pushgateway、OpenTelemetry Collector 或常驻 worker 指标面。
- 当前真实源只验证了英文 RSS 与 Horizon-derived Hacker News。中文源、微信公众号和第二读取传输仍是后续独立验证项。
- `/data` 所在磁盘仍为 90% 使用率、约 100 GiB 可用；本次没有清理或停止任何其他项目、镜像、volume 或 build cache。
