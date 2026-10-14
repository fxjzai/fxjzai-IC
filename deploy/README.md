# 部署说明（RL-Radar 服务器）

面向 `ic.fxjzai.com` 这一套：GitHub Actions 构建镜像 → GHCR → 服务器 `docker compose pull` 运行。
**服务器不构建镜像**（雨云 2 核 2 GB，构建会 OOM）。

## 组成

| 文件 | 作用 |
| --- | --- |
| `../.github/workflows/publish.yml` | 上游 `Check` 通过后构建 `linux/amd64` 镜像并推到 `ghcr.io/fxjzai/rl-radar` |
| `compose.ghcr.yml` | 覆盖文件：去掉 `build`，四个应用服务改用 GHCR 镜像；compose 项目名改为 `rlradar` |
| `rlradar-worker-window.sh` | 只在 DeepSeek 闲时窗口运行 worker 容器（幂等，由 systemd timer 每分钟调一次） |
| `systemd/rlradar-worker-window.{service,timer}` | 上面那个脚本的定时器 |
| `../docker-compose.yml` | 上游文件，**不改**，升级时直接跟随上游 |

## 服务器目录约定

```
/opt/rl-radar           仓库克隆（含 .env，.env 不入库）
/opt/rl-radar/.env      生产配置，权限 600
```

## 首次安装

```bash
apt-get update && apt-get install -y git
git clone https://github.com/fxjzai/rl-radar.git /opt/rl-radar
cd /opt/rl-radar
cp .env.example .env      # 然后按下面「.env 要点」填写
chmod 600 .env

install -m 755 deploy/rlradar-worker-window.sh /opt/rl-radar/deploy/   # 仓库里已带，无需重复
install -m 644 deploy/systemd/rlradar-worker-window.service /etc/systemd/system/
install -m 644 deploy/systemd/rlradar-worker-window.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rlradar-worker-window.timer

dc() { docker compose -f docker-compose.yml -f deploy/compose.ghcr.yml "$@"; }
dc pull
dc up -d --no-build
```

## .env 要点

- `SITE_URL=https://ic.fxjzai.com`
- `ADMIN_PASSWORD` / `SESSION_SECRET` / `IMG_PROXY_SIGN_SECRET` / `POSTGRES_PASSWORD`：用 `node scripts/init-env.ts` 生成
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：DeepSeek
- `COLLECT_ENABLED=true`、`MODEL_CALLS_ENABLED=true` —— **配合闲时窗口使用**，别让 worker 在工作时段常驻
- `PORT=127.0.0.1:3000`、`SITE_DOMAIN=ic.fxjzai.com`、`TRUST_PROXY=true`（前面有 Cloudflare/Caddy）
- **不要设 `EGRESS_PROXY_URL`**：服务器在洛杉矶，海外信源直连即可。那边是国内 fake-IP 代理环境才需要

## 更新流程

```bash
cd /opt/rl-radar
git pull
dc pull
dc up -d --no-build
```

## 闲时窗口

`deploy/rlradar-worker-window.sh` 默认 **00:30–08:30（北京时间）**，由 `rlradar-worker-window.timer` 每分钟驱动：
窗口内 `up -d worker`，窗口外 `stop worker`。web/api 常驻不参与，它们不调用模型。

改窗口只改脚本里的 `START_MIN` / `END_MIN` 两行。查看动作记录：

```bash
journalctl -u rlradar-worker-window -f
```

改完记得 `systemctl restart rlradar-worker-window.timer` 不需要，但脚本改了要重新 `install` 或直接改 `/opt/rl-radar/deploy/` 下的文件。

## Cloudflare / CDN

域名已经走 Cloudflare 代理（橙云）。要点：

1. 边缘 SSL/TLS 模式用 **Full (strict)**；源站 Caddy 用 Let's Encrypt 证书（HTTP-01 挑战能穿过 Cloudflare 到达源站）。
2. 因为 `TRUST_PROXY=true`，应用从 `X-Forwarded-For` / `CF-Connecting-IP` 读访客地址；**故意伪造该头会被用来绕过登录与反馈限流，所以只在确认有反代时才开**。
3. 进阶加固：把 ufw 的 80/443 从 `Anywhere` 收紧到 **Cloudflare 官方 IP 段**，这样别人无法绕过 CDN 直连源站 IP。加入 CDN 后再做。
4. MCP 默认只接受 `SITE_URL` 主机；有额外访问域名时写 `MCP_ALLOWED_HOSTS`。

## 备份

`DB_BACKUP_*` 指向 S3 兼容对象存储（腾讯云 COS / AWS S3 / 阿里云 OSS）即可让
`ops.backup`（每天 04:10）自动备份数据库。**磁盘只有 30 GB，建议尽快配上。**
