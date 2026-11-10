#!/bin/bash
# 从 compose 指定的 pairdrop 镜像提取原版文件，重新生成 patch/ 下的全部定制补丁。
# 在凤凰城服务器以 root 运行（deploy.sh 会在 pull 之后自动调用）：
#   cd /opt/pairdrop && sudo bash apply-ws-patch.sh
# 生成过程先落在临时目录，全部成功后才整体替换 patch/；patch-pairdrop.py 对每处替换做精确匹配，
# 上游代码变化会直接报错退出，避免把半成品挂进容器。PYTHON=python 可覆盖解释器（便于本地测试）。
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/pairdrop}"
PYTHON="${PYTHON:-python3}"
cd "$DEPLOY_DIR"

# compose --images 的输出顺序随 compose 版本变化，不能靠 tail 取；按镜像名挑出 pairdrop。
IMG=$(docker compose config --images | grep -v '^$' | grep -i pairdrop | tail -1)
[ -n "$IMG" ] || IMG=$(docker compose config --images | grep -v '^$' | tail -1)
[ -n "$IMG" ] || { echo "无法从 compose 读取 pairdrop 镜像" >&2; exit 1; }

for f in patch-pairdrop.py apple-theme.css docker-compose.yml; do
    [ -f "$f" ] || { echo "缺少 $DEPLOY_DIR/$f" >&2; exit 1; }
done

STAGE=$(mktemp -d "$DEPLOY_DIR/.patch-stage.XXXXXX")
cleanup() {
    docker rm -f pairdrop-extract >/dev/null 2>&1 || true
    if [ -n "${STAGE:-}" ] && [ -d "$STAGE" ]; then rm -rf "$STAGE"; fi
}
trap cleanup EXIT

docker rm -f pairdrop-extract >/dev/null 2>&1 || true
docker create --name pairdrop-extract "$IMG" >/dev/null

# 补丁生成器按文件名读写；左边是容器内路径，右边是补丁目录里的文件名。
copy() {
    docker cp "pairdrop-extract:/home/node/app/$1" "$STAGE/$2" >/dev/null
}
copy server/ws-server.js ws-server.js
copy server/peer.js peer.js
copy public/scripts/network.js network.js
copy public/scripts/ui.js ui.js
copy public/scripts/ui-main.js ui-main.js
copy public/scripts/util.js util.js
copy public/scripts/localization.js localization.js
copy public/index.html index.html
copy public/service-worker.js service-worker.js
copy public/lang/zh-CN.json zh-CN.json
cp apple-theme.css "$STAGE/apple-theme.css"
docker rm pairdrop-extract >/dev/null

# .cache-revision 记录上轮部署的 -wsN；本次生成必须基于它递增，否则浏览器不会丢弃旧缓存。
[ ! -f patch/.cache-revision ] || cp patch/.cache-revision "$STAGE/.cache-revision"
"$PYTHON" patch-pairdrop.py "$STAGE"

# 校验生成结果与 compose 的 bind mount 清单一致，防止漏挂或补丁生成器漏写。
for f in util.js service-worker.js network.js ui.js ws-server.js peer.js index.html localization.js zh-CN.json apple-theme.css ui-main.js; do
    [ -f "$STAGE/$f" ] || { echo "补丁缺少 $f" >&2; exit 1; }
done
chmod 644 "$STAGE"/*
mkdir -p logs

BACKUP=""
if [ -d patch ]; then
    BACKUP="patch.backup.$$"
    mv patch "$BACKUP"
fi
mv "$STAGE" patch
STAGE=""
[ -z "$BACKUP" ] || rm -rf "$BACKUP"

# 生成文件必须与 compose 的 mount 清单完全一致（.cache-revision 只是本机生成状态，不挂载）。
MOUNTS=$(grep -o '\./patch/[^:]*' docker-compose.yml | sed 's|./patch/||' | sort -u)
GENERATED=$(find patch -maxdepth 1 -type f -not -name '.*' -printf '%f\n' | sort -u)
[ "$MOUNTS" = "$GENERATED" ] || {
    echo "补丁文件与 compose 挂载清单不一致：" >&2
    diff <(printf '%s\n' "$MOUNTS") <(printf '%s\n' "$GENERATED") >&2 || true
    exit 1
}
echo "已从 $IMG 生成 $(printf '%s\n' "$GENERATED" | wc -l) 个补丁文件，与 compose 挂载清单一致：$DEPLOY_DIR/patch"
grep -n '^const cacheVersion' patch/service-worker.js
