#!/bin/bash
# 只在 DeepSeek 闲时窗口运行 worker 容器：采集与模型处理都发生在这个窗口里。
# web / api 容器常驻，站点 24 小时可访问，并且它们不调用模型
# （框架规则：读者打开页面不触发模型调用）。
#
# 由 systemd timer 每分钟调一次；脚本本身是幂等的。
#
# 闲时窗口（北京时间，分钟制）：默认 00:30 - 08:30。
# 公开报道称 DeepSeek 夜间错峰计费、周末与中国法定节假日全天按闲时计费；
# 准确时段以官方价格页为准 —— 要改窗口只改下面两行。
set -u

COMPOSE_DIR="${RLRADAR_DIR:-/opt/rl-radar}"
PROJECT="${RLRADAR_PROJECT:-rlradar}"
COMPOSE="docker compose -f docker-compose.yml -f deploy/compose.ghcr.yml"

START_MIN=$((0 * 60 + 30))
END_MIN=$((8 * 60 + 30))

cd "$COMPOSE_DIR" || { echo "找不到 $COMPOSE_DIR"; exit 1; }

now=$((10#$(TZ=Asia/Shanghai date +%H) * 60 + 10#$(TZ=Asia/Shanghai date +%M)))

worker_running=$(docker ps \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=worker" \
  --format '{{.Names}}' 2>/dev/null | head -1)

if [ "$now" -ge "$START_MIN" ] && [ "$now" -lt "$END_MIN" ]; then
  if [ -z "$worker_running" ]; then
    echo "$(date '+%F %T %Z') 进入闲时窗口：启动 worker"
    # shellcheck disable=SC2086
    $COMPOSE up -d --no-build worker
  fi
else
  if [ -n "$worker_running" ]; then
    echo "$(date '+%F %T %Z') 离开闲时窗口：停止 worker（避免工作时段更贵的模型调用）"
    # shellcheck disable=SC2086
    $COMPOSE stop worker
  fi
fi
