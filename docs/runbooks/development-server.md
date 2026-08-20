# IC 开发服务器 Runbook

- Target：`mckj@192.168.100.189:/data/fxjzai-ic`
- Service URL：`http://192.168.100.189:18000`
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
docker compose up -d --build api
```

## Health

```bash
curl --fail --silent http://127.0.0.1:18000/health
docker compose ps
```

健康响应至少包含 `{"status":"healthy"}`，`postgres` 与 `api` 容器均应显示 healthy。

## Logs

```bash
cd /data/fxjzai-ic
docker compose logs --tail=200 api
docker compose logs --tail=100 postgres
docker compose logs --follow api
```

## Run the tracer flow

```bash
docker compose run --rm api ic ingest openai-news-rss

docker compose run --rm api ic ingest horizon-hacker-news
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

### 本轮部署记录（待回填）

| 项目 | 实际状态 |
| --- | --- |
| 部署时间 | 待回填 |
| 部署 commit | 待回填 |
| `docker compose ps` | 待回填 |
| `/health` 实际响应 | 待回填 |
| OpenAI News RSS Ingest | 待回填 |
| Horizon Hacker News Ingest | 待回填 |
| Snapshot / Digest / Feedback | 待回填 |
| 已知问题与后续动作 | 待回填 |
