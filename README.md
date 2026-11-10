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
| `deploy/apply-ws-patch.sh` | 从容器镜像提取原版文件并调用补丁生成器 |
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

这些改动通过 `deploy/patch/` 下的只读 bind mount 注入容器，并没有直接修改 `upstream/`。

## 与服务器部署的对应关系

```text
/opt/pairdrop/docker-compose.yml        -> deploy/docker-compose.yml
/opt/pairdrop/patch-pairdrop.py         -> deploy/patch-pairdrop.py
/opt/pairdrop/patch/*                   -> deploy/patch/*
/opt/pairdrop/rtc_config.json           -> deploy/rtc_config.json
/opt/pairdrop/apple-theme.css           -> deploy/apple-theme.css
容器镜像 /home/node/app                 -> upstream/
/www/server/panel/vhost/nginx/...       -> deploy/file.zhizinan.top.nginx.conf
```

对外域名是 `file.zhizinan.top`，容器只监听 `127.0.0.1:3400`，由本机 nginx 反向代理，再经 EdgeOne 对外提供服务。

## 还原方式

本仓库是源码归档，不是可直接就地启动的完整部署目录。若要重新部署，应先把 `deploy/` 中的文件按服务器路径放回 `/opt/pairdrop`，按 `apply-ws-patch.sh` 的注释从对应镜像重新生成补丁，再使用 `docker compose` 启动；nginx 与 EdgeOne 配置需要单独部署。不要在未核对证书、域名和密钥的情况下直接照搬 nginx 配置。

## 导出校验

- `/opt/pairdrop` 部署文件归档 SHA-256：`cde1646757c2e3a13da05b1814b016275ec44d9617d18e805adabc5044cfe66e`
- 镜像内原版源码归档 SHA-256：`7747ce7038f6c8bcaab9dfed8111c1dcce0c5e2531ac61dfcf7a9de86e0c6790`
- 两个归档从服务器下载后均通过 SHA-256 比对
