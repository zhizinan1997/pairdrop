# 基于上游官方镜像，把 deploy/patch/ 里的定制文件覆盖进 /home/node/app。
# 这样镜像本身就能直接跑（docker run 即可），不需要服务器上的 bind mount。
# 服务器上现有的部署方式不受影响：deploy/docker-compose.yml 仍然按挂载清单覆盖同样的路径，
# 挂载优先级高于镜像层，所以 apply-ws-patch.sh 热更新补丁依旧生效。
ARG BASE_IMAGE=ghcr.io/schlagmichdoch/pairdrop:latest
FROM ${BASE_IMAGE}

# 补丁文件与 deploy/docker-compose.yml 的挂载清单一一对应。
COPY --chown=node:node deploy/patch/util.js            /home/node/app/public/scripts/util.js
COPY --chown=node:node deploy/patch/network.js         /home/node/app/public/scripts/network.js
COPY --chown=node:node deploy/patch/ui.js              /home/node/app/public/scripts/ui.js
COPY --chown=node:node deploy/patch/ui-main.js         /home/node/app/public/scripts/ui-main.js
COPY --chown=node:node deploy/patch/localization.js    /home/node/app/public/scripts/localization.js
COPY --chown=node:node deploy/patch/index.html         /home/node/app/public/index.html
COPY --chown=node:node deploy/patch/service-worker.js  /home/node/app/public/service-worker.js
COPY --chown=node:node deploy/patch/apple-theme.css    /home/node/app/public/styles/apple-theme.css
COPY --chown=node:node deploy/patch/zh-CN.json         /home/node/app/public/lang/zh-CN.json
COPY --chown=node:node deploy/patch/ws-server.js       /home/node/app/server/ws-server.js
COPY --chown=node:node deploy/patch/peer.js            /home/node/app/server/peer.js

USER node
WORKDIR /home/node/app
EXPOSE 3000
CMD ["node", "server/index.js"]
