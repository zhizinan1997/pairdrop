# PairDrop 凤凰城服务器源码快照

本目录是从 Oracle Cloud 凤凰城服务器 `132.226.73.207` 导出的 PairDrop 项目快照，导出日期为 2026-11-10。项目包含服务器上运行的上游源码和本地定制部署文件。

## 来源与版本

- 容器：`pairdrop`
- 镜像：`ghcr.io/schlagmichdoch/pairdrop:latest`
- 镜像标签：`v1.11.2`，revision `4862ba3067be1a0f2e0d1e94861dc9200b5bfeea`
- 上游仓库：<https://github.com/schlagmichdoch/PairDrop>
- 许可证：GPL-3.0（上游镜像元数据）；完整许可文本见 [`upstream/LICENSE`](upstream/LICENSE)

服务器上运行的源码来自镜像 `/home/node/app`。本快照中的 `upstream/` 同时补齐了官方 v1.11.2 tag 中未打进运行镜像的仓库文件；全部 168 个文件与官方 tag 的 SHA-256 校验和一致。

## 目录结构

| 路径 | 内容 |
|---|---|
| `upstream/` | 官方 PairDrop v1.11.2 源码及仓库文件，未包含 `node_modules` |
| `deploy/` | 服务器上 `/opt/pairdrop` 的部署配置、补丁生成器、已生成的补丁文件 |
| `deploy/patch/` | 通过 bind mount 覆盖进容器的前端 / 后端文件 |
| `deploy/docker-compose.yml` | 服务器上实际使用的 compose 配置，包含 `pairdrop` 与 `coturn-stun` |
| `deploy/patch-pairdrop.py` | 从上游源文件生成定制补丁的脚本 |
| `deploy/apply-ws-patch.sh` | 从容器镜像提取原版文件、复制主题并调用补丁生成器，原子替换 `patch/` |
| `deploy/deploy.sh` | 拉取镜像后重新生成补丁并重建容器 |
| `deploy/file.zhizinan.top.nginx.conf` | 宝塔站点的 nginx 配置快照 |
| `deploy/nginx-extension.conf` | EdgeOne 真实客户端 IP 相关扩展配置 |
| `deploy/eo_setup.py` | 早期 EdgeOne 接入脚本；运行需要 `.secrets/tencentcloud.key`（本仓库不含密钥） |
| `deploy/rtc_config.json` | 自建 STUN 配置 |

运行日志 `deploy/logs/` 没有纳入版本控制，避免泄露访客 IP、设备名和活动记录。`.secrets/`、SSH 私钥、证书私钥和 API 密钥均未复制到本仓库。

## 定制内容

`patch-pairdrop.py` 的文件头列出了完整的定制清单，主要包括：

- WebSocket 中转改为二进制帧和滑动窗口，并改进心跳 / 断线重连
- 连接与传输过程详细日志
- 简体中文界面、隐藏配对 / 公共房间等按钮、Apple 风格主题
- 设备卡片显示连接方式、速率、预计剩余时间
- 动画角色设备名、点击菜单、测网速功能
- WebRTC 优先、失败回退 WebSocket 中转；`?relay` 可强制中转
- 拖入文件 / 文件夹后显示「拖拽至对应设备图标快捷发送」，再拖到设备图标即可发送；文件夹内容会保留相对路径
- 零字节文件也能完成传输；局域网 P2P 直连使用更大的分块、受限发送缓冲和 8 分区滑动窗口提升吞吐，服务器中转仍用较保守的窗口
- 传输时弹出状态窗口，显示方向、当前协议、进度、速率、预计剩余时间与「终止传输」按钮；取消会通知对端并清理双方传输状态

这些改动通过 `deploy/patch/` 下的只读 bind mount 注入容器，并没有直接修改 `upstream/`。

## 与服务器部署的对应关系

```text
/opt/pairdrop/docker-compose.yml        -> deploy/docker-compose.yml
/opt/pairdrop/deploy.sh                 -> deploy/deploy.sh
/opt/pairdrop/apply-ws-patch.sh         -> deploy/apply-ws-patch.sh
/opt/pairdrop/patch-pairdrop.py         -> deploy/patch-pairdrop.py
/opt/pairdrop/patch/*                   -> deploy/patch/*
/opt/pairdrop/rtc_config.json           -> deploy/rtc_config.json
/opt/pairdrop/apple-theme.css           -> deploy/apple-theme.css
容器镜像 /home/node/app                 -> upstream/
/www/server/panel/vhost/nginx/...       -> deploy/file.zhizinan.top.nginx.conf
```

对外域名是 `file.zhizinan.top`，容器只监听 `127.0.0.1:3400`，由本机 nginx 反向代理，再经 EdgeOne 对外提供服务。

## 镜像构建

仓库根目录的 `Dockerfile` 以上游镜像为基础，把 `deploy/patch/` 里 11 个定制文件覆盖进 `/home/node/app`，构建出来的镜像可独立运行，不依赖服务器上的 bind mount。

`.github/workflows/image.yml` 在打 `v*` tag 或手动触发时构建并推送到 GHCR：

```
ghcr.io/zhizinan1997/pairdrop:<tag>
ghcr.io/zhizinan1997/pairdrop:latest
ghcr.io/zhizinan1997/pairdrop:sha-<短哈希>
```

构建后会用 `grep` 检查镜像里确实带上了 `network.js` 的定制、`index.html` 的传输弹窗和 `apple-theme.css`，避免推出去一个没打补丁的镜像。基础镜像默认取上游 `latest`，也可以在手动触发时通过 `base_image` 指定具体版本。

镜像里的定制文件是构建时固化的；服务器上现有部署仍以 `apply-ws-patch.sh` 重新生成补丁 + bind mount 为准，挂载优先级高于镜像层，两者不冲突。

上游镜像基于 Alpine、以 root 运行且没有 `node` 用户，所以 Dockerfile 不设置 `USER`，`ENTRYPOINT` / `CMD` / `WORKDIR` 全部继承上游。推 tag 之前先在本地跑一遍：

```bash
docker build -t pairdrop-custom:test .
docker run --rm --entrypoint sh pairdrop-custom:test -c \
  'grep -q patched public/scripts/network.js && grep -q transfer-status-dialog public/index.html && echo ok'
```

## 还原方式

本仓库是源码归档，不是可直接就地启动的完整部署目录。若要重新部署，应先把 `deploy/` 中的文件按服务器路径放回 `/opt/pairdrop`，在服务器上执行 `sudo bash deploy.sh`：脚本先拉取 compose 指定的镜像，调用 `apply-ws-patch.sh` 从镜像重新提取原版文件、复制 `apple-theme.css`、运行 `patch-pairdrop.py`，全部成功后原子替换 `patch/`，最后重建容器。补丁生成器使用精确字符串匹配；上游代码变化时会报错退出，需要人工核对定制点。nginx 与 EdgeOne 配置需要单独部署。不要在未核对证书、域名和密钥的情况下直接照搬 nginx 配置。

本地只需核验补丁时，可把 `upstream/` 中的 10 个原版文件复制到临时目录，执行 `python deploy/patch-pairdrop.py <临时目录>`；生成结果应与 `deploy/patch/` 一致，只有 `service-worker.js` 的缓存版本号会在每次生成时递增，以便浏览器丢弃旧缓存。

## 导出校验

- `/opt/pairdrop` 部署文件归档 SHA-256：`cde1646757c2e3a13da05b1814b016275ec44d9617d18e805adabc5044cfe66e`
- 镜像内原版源码归档 SHA-256：`7747ce7038f6c8bcaab9dfed8111c1dcce0c5e2531ac61dfcf7a9de86e0c6790`
- 两个归档从服务器下载后均通过 SHA-256 比对
