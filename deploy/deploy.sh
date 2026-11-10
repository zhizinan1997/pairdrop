#!/bin/bash
# PairDrop 部署/更新（2026-10-18）。Runs ON PHOENIX as root:
#   1) 复制 compose：bash scripts/new.sh 'sudo tee /opt/pairdrop/docker-compose.yml >/dev/null' < scripts/pairdrop/docker-compose.yml
#   2) bash scripts/new.sh 'sudo bash -s' < scripts/pairdrop/deploy.sh
# 更新镜像：重跑本脚本即可（pull + 重建容器，数据不落盘，无状态）。
set -eu
mkdir -p /opt/pairdrop
cd /opt/pairdrop
docker compose pull
docker compose up -d
for i in $(seq 1 20); do
  [ "$(docker inspect -f '{{.State.Running}}' pairdrop 2>/dev/null)" = true ] && break
  sleep 2
done
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
echo "local -> $(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:3400/)"
