#!/bin/bash
# PairDrop 部署/更新（2026-10-18）。Runs ON PHOENIX as root:
#   1) 复制 compose：bash scripts/new.sh 'sudo tee /opt/pairdrop/docker-compose.yml >/dev/null' < scripts/pairdrop/docker-compose.yml
#   2) bash scripts/new.sh 'sudo bash -s' < scripts/pairdrop/deploy.sh
# 更新镜像：重跑本脚本即可（pull + 重新生成补丁 + 重建容器，数据不落盘，无状态）。
# 镜像里的原版代码不是 git 分支；每次都要从新镜像重新提取并打补丁，否则精确替换会失败或漏掉新的定制。
set -euo pipefail
mkdir -p /opt/pairdrop
cd /opt/pairdrop
for f in docker-compose.yml apply-ws-patch.sh patch-pairdrop.py apple-theme.css rtc_config.json; do
    [ -f "$f" ] || { echo "缺少 /opt/pairdrop/$f，请先完整复制 deploy/ 下的部署文件" >&2; exit 1; }
done
docker compose pull
bash apply-ws-patch.sh
docker compose up -d
for i in $(seq 1 20); do
  [ "$(docker inspect -f '{{.State.Running}}' pairdrop 2>/dev/null)" = true ] && break
  sleep 2
done
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
echo "local -> $(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:3400/)"
