#!/bin/bash
# PairDrop：强制所有客户端走服务器 WebSocket 中转（2026-10-18）。Runs ON PHOENIX as root:
#   bash scripts/new.sh 'sudo bash -s' < scripts/pairdrop/apply-ws-patch.sh
# 原因：设备卡片要等 WebRTC 数据通道建立才出现；本环境没有 TURN、STUN 是 Google 的（大陆连不上），
#       Mac 又走 Clash，ICE 必然失败 → 服务器上两台设备已在同一房间，页面却永远不显示。
# 做法：从镜像取出 util.js / service-worker.js，把 window.isRtcSupported 置 false（客户端改用 WS_FALLBACK 中转），
#       并把 service worker 缓存版本号加后缀让浏览器拉新脚本；通过 compose 的只读 volume 挂进容器。
#       升级镜像后重跑本脚本即可（会从新镜像重新生成补丁文件）。
set -eu
cd /opt/pairdrop
IMG=$(docker compose config --images | head -1)
mkdir -p patch
docker rm -f pairdrop-extract >/dev/null 2>&1 || true
docker create --name pairdrop-extract "$IMG" >/dev/null
docker cp pairdrop-extract:/home/node/app/public/scripts/util.js patch/util.js
docker cp pairdrop-extract:/home/node/app/public/service-worker.js patch/service-worker.js
docker rm pairdrop-extract >/dev/null
sed -i 's|^window.isRtcSupported = .*|window.isRtcSupported = false; // patched: force WebSocket relay (no TURN reachable)|' patch/util.js
sed -i "s|^const cacheVersion = '\(.*\)';|const cacheVersion = '\1-ws1';|" patch/service-worker.js
grep -n "^window.isRtcSupported" patch/util.js
grep -n "^const cacheVersion" patch/service-worker.js
chmod 644 patch/*.js
