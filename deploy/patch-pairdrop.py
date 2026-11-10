#!/usr/bin/env python3
"""PairDrop 定制补丁（2026-10-19）。由 apply-ws-patch.sh 调用：python3 patch-pairdrop.py <补丁目录>
对从镜像里取出的原版文件做精确替换；每处替换必须恰好命中一次，否则直接报错退出（升级镜像后上游改了代码时提醒人工检查）。
脚本只修改补丁目录中的文件，不负责从镜像提取原版文件或复制 apple-theme.css。

1. server/ws-server.js：心跳超时 5s -> 30s。服务器每秒 ping 一次，原来 5 秒收不到 pong 就踢掉设备；
   跨境线路偶尔卡几秒、或者 WebSocket 中转传文件时 pong 排在 1MB 数据块后面，都会被误踢。
2. public/scripts/network.js：进度事件里附带本次传输的总字节数和连接方式（WebSocket 中转 / WebRTC 直连）。
3. public/scripts/ui.js：传输中在设备卡片上多显示一行：连接方式、速率、预计剩余时间。
4. public/index.html：隐藏右上角的"配对设备 / 编辑已配对设备 / 公共房间 / 语言 / 关于"按钮（nginx 已让所有访问者
   同处一个房间，这些功能用不上）。用 CSS 隐藏而不删元素：ui.js / main.js 里还引用着它们，main.js 还会主动取消隐藏。
5. public/scripts/localization.js：界面固定为简体中文，不再跟随浏览器语言或之前保存的语言选择。
6. public/lang/zh-CN.json：文案改成单一共享房间的说法（不再提配对和公共房间），措辞更自然。
7. Apple 风格主题：index.html 引入 styles/apple-theme.css（apply-ws-patch.sh 从 /opt/pairdrop/apple-theme.css 复制），
   service-worker.js 把它加入离线缓存；scripts/ui-main.js 背景圆环由灰色改为淡 Apple 蓝，并且有设备时也持续
   缓慢扩散（5 秒一圈、30fps；上游有设备就停）。
8. 提速（实测瓶颈是发送方到凤凰城的上行带宽，软件层面去掉两块浪费）：
   - 文件块改用二进制 WebSocket 帧中转，不再 base64 进 JSON（同样的上行带宽多传 33% 数据）；客户端连接时带
     ws_binary=1，服务器对没带的老页面自动转回 base64 'ws-chunk'，新旧页面互传不受影响（ws-server.js / peer.js / network.js）。
   - 滑动窗口：中转时同时允许 3 个 1MB 分块在路上，不再每 1MB 停下来等对方确认（一次确认要绕服务器两个来回）。
   - 服务器把任何消息都当作心跳：发送方的 pong 会排在自己几 MB 文件数据后面，只认 pong 会把正在发文件的设备误踢。
9. 断线修复（"总是断联"的根源）：Chrome 的网速估算一变就触发 navigator.connection 'change'，上游每次都主动断开重连，
   传文件时尤其频繁；而旧连接晚到的 'disconnect' 还会把新连接踢出房间（设备在线却谁也看不到）。现在只在确实断开时重连，
   20 秒收不到服务器消息才判定掉线；服务器忽略被新连接取代的旧连接的 'disconnect'；补上 WSPeer.refresh()。
10. 局域网直连，失败回退中转：不再强制 isRtcSupported=false（util.js 保持上游判断，?relay 可强制中转用于对比测速，
   ?relay=0 取消，存在 sessionStorage）。两端都支持 WebRTC 时先建 RTCPeer，设备卡片要等数据通道打开才出现，
   所以后加入的一方（主叫）6 秒内通道没打开、或 ICE 失败，就改建 WSPeer 并发 WS 握手，被叫方收到握手也换成 WSPeer。
   STUN 用自建 coturn（仅 STUN），地址在 /opt/pairdrop/rtc_config.json。
11. 设备卡片常驻连接方式标记（network.js 给卡片加 data-conn，样式在 apple-theme.css）。
13. 点击设备弹出菜单（测网速需对方弹窗同意）：传文件 / 发信息 / 测网速（右键同样弹出，长按不再发文字；测速协议见 network.js 的 _speedtest*）。
12. 随机设备名改成知名动画角色（peer.js，按设备号哈希，同一设备名字固定）。
14. 详细连接日志（2026-10-24，排查"用着用着断联、传输中断联"）：服务器每条 WebSocket 连接一个编号 #N，记录连接 / 断开
   （关闭码、谁断的、时长、收发字节、最后收到消息距今、未发出的数据）、心跳踢出、中转传输的开始 / 停顿 / 恢复 / 结束
   （速率、分块确认数、最大间隔）、转发失败（对方不在房间）、每 5 分钟在线状态。浏览器端把服务器看不到的事件
   （WebSocket 关闭码、20 秒静默重连、断网 / 恢复、切后台 / 冻结、网络类型变化、传输开始 / 完成 / 中断、WebRTC
   状态与连接路径、改走中转）攒起来（sessionStorage，刷新不丢），连上后以 'client-log' 发给服务器写进同一份日志。
   不记录文件名、消息内容和房间密钥。日志：docker logs pairdrop，以及宿主机 /opt/pairdrop/logs/pairdrop-日期.log（保留 14 天）。
   浏览器主动关闭连接时带上自定义关闭码：4000 = 关页面，4001 = 20 秒没收到服务器消息。
15. 拖拽快捷发送（2026-11-10）：把单个或多个文件、以及整个文件夹拖到页面空白处时，显示
   "拖拽至对应设备图标快捷发送"并进入选择接收设备状态；拖到设备图标上直接发送。浏览器只在
   drop 时暴露文件夹内容，因此用 DataTransferItem.webkitGetAsEntry() 递归读取目录，保留相对路径
   后仍走原来的 files-selected 通道；浏览器不支持目录条目时回退到 dataTransfer.files。
16. 零字节文件修复（2026-11-10）：文件夹里的空文件 / 空目录占位 .keep 都是 0 字节。原 FileChunker
   在读到空 chunk 后直接 return，既不发 partition 也不发 file-transfer-complete，进度会永远停在 0。
   现在空文件直接建立空 digester 并完成传输；全空文件批次也明确以进度 1 收尾。
17. 局域网 WebRTC 提速（2026-11-10）：上游用 64KB 的 FileReader 逐块读，每 1MB 等一次对端确认；
    本机两个标签页 RTT 即使只有 1ms，管道也会被 FileReader + stop-and-wait 限到几 MB/s。
    直连改为 256KB 块、最多 16MB 缓冲、按 bufferedamountlow 驱动，并把直连滑动窗口设为 8 个分区；
    WebSocket 中转仍保持原来的 64KB / 3 分区，避免占满服务器内存。
18. 传输状态弹窗（2026-11-10）：传输期间显示方向、协议、百分比、已传 / 总量、实时速率和预计剩余时间；
    点击「终止传输」会停止本地分块器并通知对端，双方清理 busy / 请求队列 / 接收缓冲后回到可再次传输的状态。
"""
import json, os, re, sys

PATCH_DIR = sys.argv[1] if len(sys.argv) > 1 else "."


def edit(name, old, new):
    path = os.path.join(PATCH_DIR, name)
    with open(path, encoding="utf-8", newline="") as f:
        s = f.read()
    n = s.count(old)
    if n != 1:
        sys.exit("%s: expected exactly 1 match, found %d for: %r" % (name, n, old[:80]))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace(old, new))


def set_json(name, values):
    """Set "section.key" entries in a translation file; every key must already exist upstream."""
    path = os.path.join(PATCH_DIR, name)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for dotted, value in values.items():
        section, key = dotted.split(".", 1)
        if key not in data.get(section, {}):
            sys.exit("%s: missing key %s" % (name, dotted))
        data[section][key] = value
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")


# 0. service worker: bump the cache version so browsers drop cached patched assets after an update.
# A fixed suffix would leave old cached files behind when the /wsN/ number changes or the patch is regenerated.
CACHE_SUFFIX_FILE = os.path.join(PATCH_DIR, ".cache-revision")
CACHE_SUFFIX = os.environ.get("PAIRDROP_CACHE_REVISION", "").strip()


def _bump_cache_version():
    global CACHE_SUFFIX, CACHE_SUFFIX_FILE
    path = os.path.join(PATCH_DIR, "service-worker.js")
    with open(path, encoding="utf-8", newline="") as f:
        s = f.read()
    match = re.search(r"(?m)^const cacheVersion = '([^']+)';(\r?\n)", s)
    if not match:
        sys.exit("service-worker.js: cannot find 'const cacheVersion' line")
    base = re.sub(r"-ws\d+$", "", match.group(1))
    if not CACHE_SUFFIX:
        # Legacy -ws17 is the highest revision already deployed; every regeneration moves past it.
        last = 17
        if os.path.isfile(CACHE_SUFFIX_FILE):
            with open(CACHE_SUFFIX_FILE, encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("-ws") and content[3:].isdigit():
                last = max(last, int(content[3:]))
        CACHE_SUFFIX = "-ws%d" % (last + 1)
    if not re.fullmatch(r"-ws\d+", CACHE_SUFFIX):
        sys.exit("PAIRDROP_CACHE_REVISION must look like -ws18")
    version = base + CACHE_SUFFIX
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(s[:match.start()] + "const cacheVersion = '%s';%s" % (version, match.group(2)) + s[match.end():])
    with open(CACHE_SUFFIX_FILE, "w", encoding="utf-8") as f:
        f.write(CACHE_SUFFIX + "\n")
    print("cacheVersion = %s" % version)


# 1. heartbeat timeout 5s -> 30s
edit("ws-server.js",
     """        if (Date.now() - this._keepAliveTimers[peer.id].lastBeat > 5 * timeout) {
            // Disconnect peer if unresponsive for 10s""",
     """        if (Date.now() - this._keepAliveTimers[peer.id].lastBeat > 30 * timeout) {
            // patched: disconnect peer if unresponsive for 30s (upstream: 5s, too strict for cross-border links)""")

# 2. network.js: report total size + protocol with every transfer progress event
edit("network.js",
     """        this._filesRequested = files;

        this.sendJSON({type: 'request',""",
     """        this._filesRequested = files;
        this._outgoingTotalSize = totalSize; // patched: for speed / ETA on the sender side

        this.sendJSON({type: 'request',""")
edit("network.js",
     """            case 'progress':
                this._onDownloadProgress(messageJSON.progress);""",
     """            case 'progress':
                this._onDownloadProgress(messageJSON.progress, this._outgoingTotalSize);""")
edit("network.js",
     """        this._onDownloadProgress(progress);

        // occasionally notify sender about our progress""",
     """        this._onDownloadProgress(progress, this._requestAccepted && this._requestAccepted.totalSize);

        // occasionally notify sender about our progress""")
edit("network.js",
     """    _onDownloadProgress(progress) {
        Events.fire('set-progress', {peerId: this._peerId, progress: progress, status: 'transfer'});
    }""",
     """    _onDownloadProgress(progress, totalSize) {
        // patched: totalSize + protocol let PeerUI show speed and ETA
        Events.fire('set-progress', {peerId: this._peerId, progress: progress, status: 'transfer', totalSize: totalSize, protocol: this._transferProtocol()});
    }

    _transferProtocol() {
        return this instanceof WSPeer ? 'ws' : 'rtc';
    }""")
edit("network.js",
     """        Events.fire('file-transfer-accepted');
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'transfer'});""",
     """        Events.fire('file-transfer-accepted');
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'transfer', totalSize: this._outgoingTotalSize, protocol: this._transferProtocol()});""")

# 3. ui.js: transfer stats line on the peer tile
edit("ui.js",
     """        $peer.ui.setProgress(progress.progress, progress.status)""",
     """        $peer.ui.setProgress(progress.progress, progress.status, progress)""")
edit("ui.js",
     """                    <div class="status font-body2"></div>
                </div>""",
     """                    <div class="status font-body2"></div>
                    <div class="transfer-stats" hidden style="font-size: 12px; line-height: 16px; opacity: 0.7;">
                        <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></div>
                        <div style="white-space: normal;"></div>
                    </div>
                </div>""")
edit("ui.js",
     """    setProgress(progress, status) {
        const $progress = this.$el.querySelector('.progress');""",
     """    setProgress(progress, status, detail) {
        this._updateTransferStats(progress, status, detail); // patched
        const $progress = this.$el.querySelector('.progress');""")
edit("ui.js",
     """    _onDrop(e) {
        if (this._shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();

        this._onDragEnd();

        const peerId = this._peer.id;""",
     """    // patched: protocol, speed and ETA while a file transfer is running
    _updateTransferStats(progress, status, detail) {
        const $stats = this.$el.querySelector('.transfer-stats');
        if (!$stats) return;
        if (status !== 'transfer' || progress >= 1) {
            this._xfer = null;
            $stats.hidden = true;
            return;
        }
        const now = Date.now();
        const total = detail && detail.totalSize;
        if (!this._xfer || progress < this._xfer.progress) {
            this._xfer = {samples: [], progress: 0, protocol: null, rendered: 0};
        }
        const x = this._xfer;
        x.progress = progress;
        if (detail && detail.protocol) x.protocol = detail.protocol;
        if (total) {
            x.samples.push({t: now, bytes: progress * total});
            // speed over the last ~5 seconds
            while (x.samples.length > 2 && now - x.samples[0].t > 5000) x.samples.shift();
        }
        if (x.rendered && now - x.rendered < 500) return;
        x.rendered = now;

        const zh = (Localization.getLocale() || navigator.language || '').toLowerCase().startsWith('zh');
        const protocolText = {
            ws: zh ? '服务器中转' : 'Server relay',
            rtc: zh ? 'P2P 直连' : 'P2P direct'
        }[x.protocol] || '';
        let rateText = zh ? '测速中…' : 'Measuring…';
        const first = x.samples[0];
        const last = x.samples[x.samples.length - 1];
        if (first && last && last.t - first.t >= 1000 && last.bytes > first.bytes) {
            const speed = (last.bytes - first.bytes) / ((last.t - first.t) / 1000);
            const eta = PeerUI._formatDuration((total - last.bytes) / speed);
            rateText = `${PeerUI._formatBytes(speed)}/s · ${zh ? '剩余 ' + eta : eta + ' left'}`;
        }
        $stats.children[0].textContent = protocolText;
        $stats.children[1].textContent = rateText;
        $stats.hidden = false;
    }

    static _formatBytes(bytes) {
        if (bytes >= 1048576) return (bytes / 1048576).toFixed(bytes >= 10485760 ? 0 : 1) + ' MB';
        if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
        return Math.round(bytes) + ' B';
    }

    static _formatDuration(seconds) {
        if (!isFinite(seconds) || seconds < 0) return '--:--';
        seconds = Math.round(seconds);
        const h = Math.floor(seconds / 3600);
        const m = Math.floor(seconds % 3600 / 60);
        const s = String(seconds % 60).padStart(2, '0');
        return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
    }

    _onDrop(e) {
        if (this._shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();

        this._onDragEnd();

        const peerId = this._peer.id;""")

# 4. index.html: hide pairing / public room / language / about buttons, Chinese from the first paint
edit("index.html",
     """<html lang="en">""",
     """<html lang="zh-CN">""")
edit("index.html",
     """</head>""",
     """    <!-- patched: everyone shares one room on this deployment, so pairing / public rooms / language / About / notification / expand are hidden; only the theme switch stays -->
    <style>
        #pair-device, #edit-paired-devices, #join-public-room, #language-selector, header > a[href="#about"], #notification, #expand, #websocket-fallback {
            display: none !important;
        }
    </style>
</head>""")

# 5. localization.js: always Simplified Chinese
edit("localization.js",
     """        Localization.initialLocale = storedLanguageCode && Localization.localeIsSupported(storedLanguageCode)
            ? storedLanguageCode
            : Localization.systemLocale;""",
     """        // patched: UI locked to Simplified Chinese (language selector is hidden)
        Localization.systemLocale = "zh-CN";
        Localization.initialLocale = "zh-CN";""")

# 6. zh-CN.json: copy for a single shared room, phrased the way Apple UI text reads
set_json("zh-CN.json", {
    "instructions.no-peers-title": "在另一台设备上打开此网页",
    "instructions.no-peers-subtitle": "打开后会自动出现在这里，无需配对",
    "instructions.x-instructions_desktop": "点击设备，选择传文件、发信息或测网速",
    "instructions.x-instructions_mobile": "轻点设备，选择传文件、发信息或测网速",
    "instructions.x-instructions_data-drop-peer": "松开即可发送给该设备",
    "instructions.x-instructions_data-drop-bg": "拖拽至对应设备图标快捷发送",
    "instructions.no-peers_data-drop-bg": "拖拽至对应设备图标快捷发送",
    "instructions.x-instructions-share-mode_desktop": "拖拽至对应设备图标快捷发送",
    "instructions.x-instructions-share-mode_mobile": "拖拽至对应设备图标快捷发送",
    "footer.known-as": "本机名称",
    "footer.display-name_title": "点击修改本机名称",
    "footer.discovery": "可见范围",
    "footer.on-this-network": "所有在线设备",
    "footer.on-this-network_title": "所有打开此网页的设备都能看到你",
    "footer.traffic": "同一局域网自动直连，其余情况经服务器中转",
    "peer-ui.click-to-send": "点击选择操作",
    "peer-ui.waiting": "等待对方接收…",
    "dialogs.would-like-to-share": "想要发送：",
    "dialogs.cancel": "取消",
    "notifications.selected-peer-left": "所选设备已离开",
    "notifications.file-transfer-completed": "传输完成",
})

# 7. Apple-style theme: apple-theme.css (copied in by apply-ws-patch.sh) loaded after the deferred stylesheet
edit("index.html",
     """    <!-- Receive File Dialog -->""",
     """    <!-- Transfer Status Dialog (patched) -->
    <x-dialog id="transfer-status-dialog">
        <x-background class="full center">
            <x-paper shadow="2" class="transfer-status-paper">
                <div class="transfer-status-header">
                    <div class="transfer-status-icon" aria-hidden="true">↗</div>
                    <div class="transfer-status-title-wrap">
                        <h2 class="dialog-title">文件传输</h2>
                        <div class="transfer-status-heading"></div>
                    </div>
                </div>
                <div class="transfer-status-body">
                    <div class="transfer-status-percent"></div>
                    <div class="transfer-status-track"><div class="transfer-status-bar"></div></div>
                    <div class="transfer-status-detail"></div>
                    <div class="transfer-status-meta"></div>
                </div>
                <div class="transfer-status-actions">
                    <button id="cancel-transfer" class="btn btn-rounded btn-grey" type="button">终止传输</button>
                </div>
            </x-paper>
        </x-background>
    </x-dialog>
    <!-- Receive File Dialog -->""")
edit("index.html",
     """</body>""",
     """    <!-- patched: Apple-style theme, after styles-deferred.css (appended to <head> by main.js) so it wins ties -->
    <link rel="stylesheet" type="text/css" href="styles/apple-theme.css">
</body>""")
edit("service-worker.js",
     """    'styles/styles-deferred.css',""",
     """    'styles/styles-deferred.css',
    'styles/apple-theme.css',""")
# background rings: soft Apple blue instead of grey
edit("ui-main.js",
     """        this.baseColorNormal = '168 168 168';
        this.baseColorShareMode = '168 168 255';
        this.baseOpacityNormal = 0.3;
        this.baseOpacityShareMode = 0.8;""",
     """        this.baseColorNormal = '0 122 255'; // patched: Apple theme
        this.baseColorShareMode = '0 122 255';
        this.baseOpacityNormal = 0.16;
        this.baseOpacityShareMode = 0.45;""")
# background rings keep rippling while devices are shown (upstream stops them as soon as a peer appears), but slower
# and at half the frame rate to save power; reduced-motion users get still rings. Hidden tabs pause rAF anyway.
edit("ui-main.js",
     """        this.speed = 0.5;
        this.fps = 60;""",
     """        this.speed = 0.2; // patched: one ripple every 5s (upstream 2s)
        this.fps = 30; // patched: half the redraws (upstream 60)""")
edit("ui-main.js",
     """        Events.on('background-animation', e => switchAnimation(e.detail.animate))""",
     """        // patched: keep rippling when devices are present; only honour prefers-reduced-motion
        Events.on('background-animation', _ => switchAnimation(!window.matchMedia('(prefers-reduced-motion: reduce)').matches))""")

# 8. faster WebSocket relay
# 8a. server: any message from a peer counts as a heartbeat (a sender's pong can sit behind megabytes of its own
#     file data in the same socket); binary frames carry file chunks without base64 (+33% payload per byte on the wire)
edit("ws-server.js",
     """        peer.socket.on('message', message => this._onMessage(peer, message));""",
     """        // patched: any message counts as a heartbeat; binary frames are raw ws-fallback file chunks
        peer.socket.on('message', (message, isBinary) => {
            this._setKeepAliveTimerToNow(peer);
            if (isBinary) this._onBinaryChunk(peer, message);
            else this._onMessage(peer, message);
        });""")
edit("ws-server.js",
     """    _signalAndRelay(sender, message) {""",
     """    // patched: binary ws-fallback chunk = 36-byte recipient id + raw bytes. Recipients that connected with
    // ws_binary=1 get 36-byte sender id + raw bytes; older clients get the classic base64 'ws-chunk' JSON.
    _onBinaryChunk(sender, data) {
        if (!this._conf.wsFallback || data.length < 36) return;
        const to = data.toString('latin1', 0, 36);
        if (!Peer.isValidUuid(to)) return;
        const recipient = [sender.ip, sender.publicRoomId, ...sender.roomSecrets]
            .map(roomId => roomId && this._rooms[roomId] && this._rooms[roomId][sender.id] && this._rooms[roomId][to])
            .find(Boolean);
        if (!recipient) return;
        const chunk = data.subarray(36);
        if (recipient.wsBinary) {
            recipient.socket.send(Buffer.concat([Buffer.from(sender.id, 'latin1'), chunk]), {binary: true});
        }
        else {
            this._send(recipient, {
                type: 'ws-chunk',
                chunk: chunk.toString('base64'),
                sender: {id: sender.id, rtcSupported: sender.rtcSupported}
            });
        }
    }

    _signalAndRelay(sender, message) {""")
edit("peer.js",
     """        this.rtcSupported = searchParams.get('webrtc_supported') === "true";""",
     """        this.rtcSupported = searchParams.get('webrtc_supported') === "true";
        this.wsBinary = searchParams.get('ws_binary') === "1"; // patched: client accepts binary ws-fallback chunks""")

# 8b. client: announce + send + receive binary chunks
edit("network.js",
     """        wsUrl.searchParams.append('webrtc_supported', window.isRtcSupported ? 'true' : 'false');""",
     """        wsUrl.searchParams.append('webrtc_supported', window.isRtcSupported ? 'true' : 'false');
        wsUrl.searchParams.append('ws_binary', '1'); // patched: we understand binary ws-fallback chunks""")
edit("network.js",
     """    _onMessage(msg) {
        msg = JSON.parse(msg);""",
     """    _onMessage(msg) {
        if (msg instanceof ArrayBuffer) {
            // patched: binary ws-fallback chunk = 36-byte sender id + raw bytes
            Events.fire('ws-relay-binary', {
                senderId: new TextDecoder().decode(new Uint8Array(msg, 0, 36)),
                chunk: msg.slice(36)
            });
            return;
        }
        msg = JSON.parse(msg);""")
edit("network.js",
     """    _onPeers(msg) {
        Events.fire('peers', msg);
    }""",
     """    // patched: binary ws-fallback chunk = 36-byte recipient id + raw bytes (no base64, no JSON)
    sendBinary(to, chunk) {
        if (!this._isConnected()) return;
        const frame = new Uint8Array(36 + chunk.byteLength);
        frame.set(new TextEncoder().encode(to), 0);
        frame.set(new Uint8Array(chunk), 36);
        this._socket.send(frame);
    }

    _onPeers(msg) {
        Events.fire('peers', msg);
    }""")
edit("network.js",
     """        Events.on('ws-relay', e => this._onWsRelay(e.detail));""",
     """        Events.on('ws-relay', e => this._onWsRelay(e.detail));
        Events.on('ws-relay-binary', e => this._onWsRelayBinary(e.detail)); // patched""")
edit("network.js",
     """        if (messageJSON.type === 'ws-chunk') message = base64ToArrayBuffer(messageJSON.chunk);
        this.peers[messageJSON.sender.id]._onMessage(message);
    }""",
     """        if (messageJSON.type === 'ws-chunk') message = base64ToArrayBuffer(messageJSON.chunk);
        this.peers[messageJSON.sender.id]._onMessage(message);
    }

    // patched: raw chunk relayed as a binary frame
    _onWsRelayBinary(detail) {
        if (!this._wsConfig.wsFallback) return;
        const peer = this.peers[detail.senderId];
        if (peer) peer._onMessage(detail.chunk);
    }""")
edit("network.js",
     """    _send(chunk) {
        this.sendJSON({
            type: 'ws-chunk',
            chunk: arrayBufferToBase64(chunk)
        });
    }""",
     """    _send(chunk) {
        // patched: raw binary frame instead of base64 JSON; the server converts it for clients that need base64
        this._server.sendBinary(this._peerId, chunk);
    }

    _partitionWindow() {
        return 3; // patched: partitions in flight over the server relay
    }""")

# 8c. client: sliding window -- keep up to _partitionWindow() 1 MB partitions in flight instead of stopping after
#     every partition until the receiver's ack has come back across the relay (one ack = two round trips)
edit("network.js",
     """            offset => this._onPartitionEnd(offset));
        this._chunker.nextPartition();
    }

    _onPartitionEnd(offset) {
        this.sendJSON({ type: 'partition', offset: offset });
    }""",
     """            offset => this._onPartitionEnd(offset));
        this._partitionsInFlight = 0; // patched
        this._chunker.nextPartition();
    }

    _onPartitionEnd(offset) {
        this.sendJSON({ type: 'partition', offset: offset });
        // patched: read on while fewer than _partitionWindow() partitions are waiting for their ack
        this._partitionsInFlight += 1;
        if (this._partitionsInFlight < this._partitionWindow()) this._chunker.nextPartition();
    }

    _partitionWindow() {
        // patched: WebRTC direct connections keep 16 x 4MB partitions in flight; relay stays conservative.
        return this instanceof RTCPeer ? 16 : 1;
    }""")
edit("network.js",
     """    _sendNextPartition() {
        if (!this._chunker || this._chunker.isFileEnd()) return;
        this._chunker.nextPartition();
    }""",
     """    _sendNextPartition() {
        // patched: an ack frees one slot; the chunker is idle only if the window was full
        const windowWasFull = this._partitionsInFlight >= this._partitionWindow();
        this._partitionsInFlight = Math.max(0, this._partitionsInFlight - 1);
        if (!this._chunker || this._chunker.isFileEnd() || !windowWasFull) return;
        this._chunker.nextPartition();
    }""")

# 9. connection robustness (the "keeps disconnecting" bug)
# 9a. client: Chrome fires navigator.connection 'change' whenever its bandwidth/RTT estimate moves -- constantly while
#     a transfer saturates the link. Upstream answered every one with _reconnect(): send 'disconnect', drop the socket,
#     open a new one. Now: connect only if not connected, and detect dead sockets by silence instead (server pings 1/s).
edit("network.js",
     """            navigator.connection.addEventListener('change', _ => this._reconnect());
        }""",
     """            // patched: only (re)connect when not connected -- 'change' also fires when the bandwidth estimate moves
            navigator.connection.addEventListener('change', _ => this._connect());
        }
        // patched: the server pings every second, so 20s without any message means the socket is dead
        setInterval(() => this._checkAlive(), 5000);""")
edit("network.js",
     """    _onOpen() {
        console.log('WS: server connected');""",
     """    _onOpen() {
        this._lastMessageAt = Date.now(); // patched
        console.log('WS: server connected');""")
edit("network.js",
     """    _onMessage(msg) {
        if (msg instanceof ArrayBuffer) {""",
     """    _onMessage(msg) {
        this._lastMessageAt = Date.now(); // patched: liveness for _checkAlive()
        if (msg instanceof ArrayBuffer) {""")
edit("network.js",
     """    _reconnect() {
        this._disconnect();
        this._connect();
    }""",
     """    // patched: drop a silent socket without the 'disconnect' message (it would only queue behind unsent data)
    _checkAlive() {
        if (!this._isConnected() || Date.now() - this._lastMessageAt < 20000) return;
        console.log('WS: no message from server for 20s, reconnecting');
        this._socket.onclose = null;
        this._socket.close();
        this._socket = null;
        Events.fire('ws-disconnected');
        this._isReconnect = true;
        this._connect();
    }

    _reconnect() {
        this._disconnect();
        this._connect();
    }""")
# 9b. client: WSPeer had no refresh(); PeersManager calls it when a known peer joins again -> TypeError and the
#     peer silently vanished from the list. Nothing needs re-establishing over the relay.
edit("network.js",
     """    getConnectionHash() {
        // Todo: implement SubtleCrypto asymmetric encryption and create connectionHash from public keys
        return "";
    }""",
     """    getConnectionHash() {
        // Todo: implement SubtleCrypto asymmetric encryption and create connectionHash from public keys
        return "";
    }

    refresh() {
        // patched: called by PeersManager when this peer re-joins; nothing to re-establish over the relay
    }""")
# 9c. server: after a reconnect the old socket's 'disconnect' can arrive *after* the new socket joined (it queues
#     behind the old socket's unsent data). Rooms and keepalive are keyed by peer id, so upstream then removed the
#     *new* connection from the room: the device stayed online but invisible until a page reload.
edit("ws-server.js",
     """        this._keepAliveTimers = {};""",
     """        this._keepAliveTimers = {};
        this._latestPeer = {}; // patched: newest Peer object per peer id""")
edit("ws-server.js",
     """    _onConnection(peer) {""",
     """    _onConnection(peer) {
        this._latestPeer[peer.id] = peer; // patched""")
edit("ws-server.js",
     """    _disconnect(sender) {
        this._removePairKey(sender.pairKey);""",
     """    _disconnect(sender) {
        // patched: a newer connection with the same peer id owns rooms and keepalive now -- only close this stale one
        if (this._latestPeer[sender.id] && this._latestPeer[sender.id] !== sender) {
            sender.socket.terminate();
            return;
        }
        delete this._latestPeer[sender.id];

        this._removePairKey(sender.pairKey);""")

# 10. LAN direct (WebRTC) first, server relay (WSPeer) as fallback
# 10a. util.js: keep upstream RTC detection; ?relay forces the relay for this tab session (A/B speed tests)
edit("util.js",
     """window.isRtcSupported = !!(window.RTCPeerConnection || window.mozRTCPeerConnection || window.webkitRTCPeerConnection);""",
     """window.isRtcSupported = !!(window.RTCPeerConnection || window.mozRTCPeerConnection || window.webkitRTCPeerConnection);
// patched: ?relay forces the server relay for this tab session (speed comparison), ?relay=0 clears it
try {
    const relayParams = new URLSearchParams(location.search);
    if (relayParams.has('relay')) {
        if (relayParams.get('relay') === '0') sessionStorage.removeItem('pd-relay');
        else sessionStorage.setItem('pd-relay', '1');
    }
    if (sessionStorage.getItem('pd-relay') === '1') window.isRtcSupported = false;
} catch (e) {}""")
# 10b. network.js: caller gives RTC 6s (or until ICE fails), then both sides switch that peer to WSPeer
edit("network.js",
     """            this.peers[peerId] = new RTCPeer(this._server, isCaller, peerId, roomType, roomId, this._wsConfig.rtcConfig);""",
     """            const rtcPeer = new RTCPeer(this._server, isCaller, peerId, roomType, roomId, this._wsConfig.rtcConfig);
            this.peers[peerId] = rtcPeer;
            // patched: the device tile only appears once the data channel opens -- give up on RTC after 6s
            if (isCaller && this._wsConfig.wsFallback) setTimeout(() => this._fallbackToWs(peerId, rtcPeer), 6000);""")
edit("network.js",
     """    _onMessage(message) {
        const peerId = message.sender.id;
        this.peers[peerId].onServerMessage(message);
    }""",
     """    _onMessage(message) {
        const peerId = message.sender.id;
        const peer = this.peers[peerId];
        if (!peer) return;
        // patched: the caller gave up on RTC and sent the WS handshake (signal with 'connected', no sdp / ice)
        if (peer instanceof RTCPeer && message.connected !== undefined && !message.sdp && !message.ice) {
            if (!this._wsConfig.wsFallback) return;
            this._swapToWsPeer(peerId, peer, false);
        }
        // patched: late sdp / ice for a peer that already fell back
        else if (peer instanceof WSPeer && (message.sdp || message.ice)) return;
        this.peers[peerId].onServerMessage(message);
    }

    // patched: RTC did not come up in time -> relay this peer through the server instead
    _fallbackToWs(peerId, rtcPeer) {
        if (this.peers[peerId] !== rtcPeer || rtcPeer._isConnected() || !rtcPeer._isCaller) return;
        if (!this._wsConfig || !this._wsConfig.wsFallback) return;
        console.log('RTC: no direct connection with', peerId, '-> using server relay');
        this._swapToWsPeer(peerId, rtcPeer, true);
    }

    _swapToWsPeer(peerId, oldPeer, isCaller) {
        if (oldPeer._channel) {
            oldPeer._channel.onclose = null;
            oldPeer._channel.close();
        }
        if (oldPeer._conn) {
            oldPeer._conn.onicecandidate = null;
            oldPeer._conn.onconnectionstatechange = null;
            oldPeer._conn.oniceconnectionstatechange = null;
            oldPeer._conn.close();
        }
        const roomType = oldPeer._getRoomTypes()[0];
        this.peers[peerId] = new WSPeer(this._server, isCaller, peerId, roomType, oldPeer._roomIds[roomType]);
    }""")
edit("network.js",
     """            case 'failed':
                this._onError('ICE Gathering failed');""",
     """            case 'failed':
                Events.fire('rtc-failed', {peerId: this._peerId}); // patched: PeersManager falls back to the relay
                this._onError('ICE Gathering failed');""")
edit("network.js",
     """        Events.on('ws-config', e => this._onWsConfig(e.detail));""",
     """        Events.on('ws-config', e => this._onWsConfig(e.detail));
        Events.on('rtc-failed', e => this._fallbackToWs(e.detail.peerId, this.peers[e.detail.peerId])); // patched""")

# 11. device tile shows how it is connected (green dot = direct, orange dot = server relay), styled in apple-theme.css
edit("network.js",
     """    _onPeerConnected(peerId) {
        this._notifyPeerDisplayNameChanged(peerId);
    }""",
     """    _onPeerConnected(peerId) {
        this._notifyPeerDisplayNameChanged(peerId);
        // patched: tag the tile once PeerUI has created it (listeners of this same event run first)
        setTimeout(() => {
            const peer = this.peers[peerId];
            const tile = document.getElementById(peerId);
            if (peer && tile) tile.dataset.conn = peer instanceof WSPeer ? 'ws' : 'rtc';
        }, 0);
    }""")

# 12. random device names: famous cartoon characters instead of "Purple Caribou" (stable per device: hashed from the peer id)
edit("peer.js",
     """        const displayName = uniqueNamesGenerator({
            length: 2,
            separator: ' ',
            dictionaries: [colors, animals],
            style: 'capital',
            seed: cyrb53(this.id)
        })""",
     """        // patched: cartoon character names, picked by hashing the peer id (same device -> same name)
        const characterNames = ["熊大", "熊二", "喜羊羊", "美羊羊", "懒羊羊", "沸羊羊", "暖羊羊", "灰太狼", "红太狼", "米老鼠", "米妮", "唐老鸭", "黛西", "高飞", "布鲁托", "小熊维尼", "跳跳虎", "屹耳", "小猪皮杰", "史迪奇", "爱丽儿", "艾莎", "安娜", "雪宝", "辛巴", "木法沙", "丁满", "彭彭", "小鹿斑比", "小飞象", "皮卡丘", "小火龙", "杰尼龟", "妙蛙种子", "伊布", "胖丁", "哆啦A梦", "大雄", "静香", "胖虎", "蜡笔小新", "樱桃小丸子", "龙猫", "小黄人", "巴斯光年", "胡迪", "翠丝", "毛怪", "大眼仔", "加菲猫", "史努比", "蓝精灵", "天线宝宝", "小猪佩奇", "乔治", "海绵宝宝", "派大星", "章鱼哥", "汤姆猫", "杰瑞鼠", "兔八哥", "达菲鸭", "小叮当", "瓦力", "大白", "尼莫", "多莉", "阿宝", "功夫熊猫", "小美人鱼", "白雪公主", "灰姑娘", "贝儿", "茉莉公主", "花木兰", "小熊猫", "路飞", "乔巴", "鸣人", "柯南", "小飞侠", "爱探险的朵拉", "巴布工程师", "蜘蛛侠", "光头强"];
        const displayName = characterNames[cyrb53(this.id) % characterNames.length];""")

# 12b. never two devices with the same name: on connect, skip names already used by another online device
edit("ws-server.js",
     """        this._latestPeer[peer.id] = peer; // patched""",
     """        this._latestPeer[peer.id] = peer; // patched
        // patched: unique display names among online devices (hash picks the first choice, then walk the list)
        {
            const characterNames = ["熊大", "熊二", "喜羊羊", "美羊羊", "懒羊羊", "沸羊羊", "暖羊羊", "灰太狼", "红太狼", "米老鼠", "米妮", "唐老鸭", "黛西", "高飞", "布鲁托", "小熊维尼", "跳跳虎", "屹耳", "小猪皮杰", "史迪奇", "爱丽儿", "艾莎", "安娜", "雪宝", "辛巴", "木法沙", "丁满", "彭彭", "小鹿斑比", "小飞象", "皮卡丘", "小火龙", "杰尼龟", "妙蛙种子", "伊布", "胖丁", "哆啦A梦", "大雄", "静香", "胖虎", "蜡笔小新", "樱桃小丸子", "龙猫", "小黄人", "巴斯光年", "胡迪", "翠丝", "毛怪", "大眼仔", "加菲猫", "史努比", "蓝精灵", "天线宝宝", "小猪佩奇", "乔治", "海绵宝宝", "派大星", "章鱼哥", "汤姆猫", "杰瑞鼠", "兔八哥", "达菲鸭", "小叮当", "瓦力", "大白", "尼莫", "多莉", "阿宝", "功夫熊猫", "小美人鱼", "白雪公主", "灰姑娘", "贝儿", "茉莉公主", "花木兰", "小熊猫", "路飞", "乔巴", "鸣人", "柯南", "小飞侠", "爱探险的朵拉", "巴布工程师", "蜘蛛侠", "光头强"];
            const taken = new Set(Object.values(this._latestPeer)
                .filter(other => other.id !== peer.id)
                .map(other => other.name.displayName));
            if (taken.has(peer.name.displayName)) {
                const start = Math.max(0, characterNames.indexOf(peer.name.displayName));
                let chosen = null;
                for (let i = 1; i <= characterNames.length && !chosen; i++) {
                    const candidate = characterNames[(start + i) % characterNames.length];
                    if (!taken.has(candidate)) chosen = candidate;
                }
                if (!chosen) {
                    let n = 2;
                    while (taken.has(peer.name.displayName + n)) n++;
                    chosen = peer.name.displayName + n;
                }
                peer.name.displayName = chosen;
            }
        }""")

# 13. one click menu on a device tile: send files / send message / speed test (right click and long press now open the same menu)
# 13a. server + client: let 'speedtest' control messages through the ws relay (binary test data already uses the relay frames)
edit("ws-server.js",
     """            case 'ws-chunk':
                // relay ws-fallback""",
     """            case 'ws-chunk':
            case 'speedtest': // patched: network speed test control messages
                // relay ws-fallback""")
edit("network.js",
     """            case 'ws-chunk':
                // ws-fallback""",
     """            case 'ws-chunk':
            case 'speedtest': // patched: network speed test control messages
                // ws-fallback""")

# 13b. network.js: speed test between two devices, over whatever connection they currently use (direct or relay)
edit("network.js",
     """    _onMessage(message) {
        if (typeof message !== 'string') {
            this._onChunkReceived(message);
            return;
        }""",
     """    _onMessage(message) {
        if (this._speedtestHandle(message)) return; // patched
        if (typeof message !== 'string') {
            this._onChunkReceived(message);
            return;
        }""")
edit("network.js",
     """    _onFilesTransferRequest(request) {""",
     """    // patched: speed test. Control messages are JSON {type: 'speedtest', phase, id}; test data are plain binary chunks.
    // ping/pong -> latency; start + chunks + end -> the receiver measures first->last chunk and answers with 'result';
    // 'reverse' asks the other side to send the same way back (download).
    _speedtestHandle(message) {
        if (typeof message !== 'string') {
            const st = this._stRecv;
            if (!st) return false;
            const now = performance.now();
            if (st.first === null) st.first = now; // the first chunk only starts the clock
            else st.bytes += message.byteLength;
            st.last = now;
            if (now - (st.tickT || st.first) >= 400) {
                const rate = (st.bytes - (st.tickBytes || 0)) * 1000 / (now - (st.tickT || st.first));
                st.tickT = now;
                st.tickBytes = st.bytes;
                this.sendJSON({type: 'speedtest', phase: 'tick', id: st.id, rate: rate});
                if (this._stDir && this._stDir[st.id] === 'down') Events.fire('speedtest-live', {peerId: this._peerId, dir: 'down', rate: rate});
            }
            return true;
        }
        if (message.indexOf('"speedtest"') === -1) return false;
        let m;
        try { m = JSON.parse(message); } catch (e) { return false; }
        if (m.type !== 'speedtest') return false;
        switch (m.phase) {
            case 'ping':
                this.sendJSON({type: 'speedtest', phase: 'pong', id: m.id});
                break;
            case 'tick':
                if (this._stDir && this._stDir[m.id] === 'up') Events.fire('speedtest-live', {peerId: this._peerId, dir: 'up', rate: m.rate});
                break;
            case 'request':
                // the other device wants to test the speed: ask the user (or refuse right away while busy)
                if (this._busy || this._stRecv || this._stWait) this.answerSpeedtest(m.id, false, 'busy');
                else Events.fire('speedtest-incoming', {peerId: this._peerId, id: m.id});
                break;
            case 'pong':
            case 'result':
            case 'accept':
            case 'reject':
                if (this._stWait && this._stWait.id === m.id) this._stWait.resolve(m);
                break;
            case 'start':
                this._stRecv = {id: m.id, first: null, last: 0, bytes: 0};
                break;
            case 'end': {
                const st = this._stRecv;
                this._stRecv = null;
                if (!st || st.id !== m.id) break;
                const res = {bytes: st.bytes, ms: st.first === null ? 0 : st.last - st.first};
                this.sendJSON({type: 'speedtest', phase: 'result', id: m.id, bytes: res.bytes, ms: res.ms});
                if (this._stWait && this._stWait.id === m.id) this._stWait.resolve(res);
                break;
            }
            case 'reverse':
                // only after our user agreed to the test
                if (this._stConsentUntil && Date.now() < this._stConsentUntil) this._speedtestSend(m.id, m.ms);
                break;
        }
        return true;
    }

    answerSpeedtest(id, accepted, reason) {
        if (accepted) this._stConsentUntil = Date.now() + 90000;
        this.sendJSON({type: 'speedtest', phase: accepted ? 'accept' : 'reject', id: id, reason: reason});
    }

    _speedBuffered() {
        if (this._channel) return this._channel.bufferedAmount;
        const socket = this._server && this._server._socket;
        return socket ? socket.bufferedAmount : 0;
    }

    async _speedtestSend(id, ms) {
        this.sendJSON({type: 'speedtest', phase: 'start', id: id});
        const chunk = new Uint8Array(64 * 1024);
        crypto.getRandomValues(chunk);
        const cap = this._channel ? 8 * 1024 * 1024 : 512 * 1024; // keep the send queue short on the slower relay
        const t0 = performance.now();
        while (performance.now() - t0 < ms) {
            if (this._speedBuffered() > cap) {
                if (this._channel) {
                    // event driven: timers are throttled in background tabs
                    this._channel.bufferedAmountLowThreshold = cap / 2;
                    await new Promise(resolve => {
                        this._channel.addEventListener('bufferedamountlow', resolve, {once: true});
                        setTimeout(resolve, 50);
                    });
                }
                else {
                    await new Promise(resolve => setTimeout(resolve, 5));
                }
                continue;
            }
            this._send(chunk);
        }
        this.sendJSON({type: 'speedtest', phase: 'end', id: id});
    }

    async runSpeedTest(onPhase) {
        if (this._busy || this._stRecv || this._stWait) throw new Error('busy');
        const wait = (id, timeout) => new Promise((resolve, reject) => {
            const timer = setTimeout(() => { this._stWait = null; reject(new Error('timeout')); }, timeout);
            this._stWait = {id: id, resolve: value => { clearTimeout(timer); this._stWait = null; resolve(value); }};
        });
        const rate = r => r.ms > 0 ? r.bytes * 1000 / r.ms : 0;
        try {
            onPhase('latency');
            let rtt = Infinity;
            for (let i = 0; i < 3; i++) {
                const id = 'p' + i + '-' + Date.now();
                const t0 = performance.now();
                const pending = wait(id, 5000);
                this.sendJSON({type: 'speedtest', phase: 'ping', id: id});
                await pending;
                rtt = Math.min(rtt, performance.now() - t0);
            }
            onPhase('waiting');
            const reqId = 'r-' + Date.now();
            const reqPending = wait(reqId, 35000);
            this.sendJSON({type: 'speedtest', phase: 'request', id: reqId});
            let answer;
            try { answer = await reqPending; } catch (e) { throw new Error('no-answer'); }
            if (answer.phase !== 'accept') throw new Error(answer.reason === 'busy' ? 'peer-busy' : 'rejected');
            onPhase('up');
            const upId = 'u-' + Date.now();
            this._stDir = {};
            this._stDir[upId] = 'up';
            const upPending = wait(upId, 40000);
            await this._speedtestSend(upId, 3000);
            const up = await upPending;
            onPhase('down');
            const downId = 'd-' + Date.now();
            this._stDir[downId] = 'down';
            const downPending = wait(downId, 40000);
            this.sendJSON({type: 'speedtest', phase: 'reverse', id: downId, ms: 3000});
            const down = await downPending;
            return {rtt: rtt, up: rate(up), down: rate(down), protocol: this._transferProtocol()};
        }
        finally {
            this._stWait = null;
            this._stDir = null;
        }
    }

    _onFilesTransferRequest(request) {""")
edit("network.js",
     """        Events.on('ws-config', e => this._onWsConfig(e.detail));""",
     """        Events.on('ws-config', e => this._onWsConfig(e.detail));
        Events.on('speedtest-request', e => this._onSpeedtestRequest(e.detail.peerId)); // patched
        Events.on('speedtest-answer', e => { // patched: our user answered a speed test request
            const peer = this.peers[e.detail.peerId];
            if (peer) peer.answerSpeedtest(e.detail.id, e.detail.accepted, e.detail.reason);
        });""")
edit("network.js",
     """    _onWsConfig(wsConfig) {""",
     """    // patched: run a speed test with one peer and report progress / result to the UI
    _onSpeedtestRequest(peerId) {
        const report = state => Events.fire('speedtest-state', Object.assign({peerId: peerId}, state));
        const peer = this.peers[peerId];
        if (!peer) return report({error: 'missing'});
        peer.runSpeedTest(phase => report({phase: phase}))
            .then(result => report({result: result}))
            .catch(error => report({error: error.message}));
    }

    _onWsConfig(wsConfig) {""")

# 13c. ui.js: clicking a tile opens the menu instead of the file picker; right click opens it too, long press no longer sends text
edit("ui.js",
     """        this._callbackInput = e => this._onFilesSelected(e);""",
     """        this._callbackInput = e => this._onFilesSelected(e);
        this._callbackPick = e => this._onPickClick(e); // patched""")
edit("ui.js",
     """            this.$el.querySelector('input').addEventListener('change', this._callbackInput);""",
     """            this.$el.querySelector('input').addEventListener('change', this._callbackInput);
            this.$el.querySelector('input').addEventListener('click', this._callbackPick); // patched""")
edit("ui.js",
     """    _onRightClick(e) {
        e.preventDefault();
        Events.fire('text-recipient', {
            peerId: this._peer.id,
            deviceName: e.target.closest('x-peer').querySelector('.name').innerText
        });
    }""",
     """    // patched: the click that would open the file picker opens the action menu; the menu's "send files" re-clicks
    _onPickClick(e) {
        if (this._allowPick) {
            this._allowPick = false;
            return;
        }
        e.preventDefault();
        PeerMenu.show(this);
    }

    _onRightClick(e) {
        e.preventDefault();
        PeerMenu.show(this); // patched
    }""")
edit("ui.js",
     """    _onTouchStart(e) {
        this._touchStart = Date.now();""",
     """    _onTouchStart(e) {
        return; // patched: long press no longer sends text, the tap menu has it
        this._touchStart = Date.now();""")
edit("ui.js",
     """class Dialog {
    constructor(id) {""",
     """// patched: action menu that pops up under a device icon
class PeerMenu {
    static show(peerUI) {
        PeerMenu.hide();
        const $menu = document.createElement('div');
        $menu.className = 'peer-menu';
        $menu.innerHTML = `
            <button type="button" data-act="file">传文件</button>
            <button type="button" data-act="text">发信息</button>
            <button type="button" data-act="speed">测网速</button>`;
        document.body.appendChild($menu);
        PeerMenu.$menu = $menu;
        PeerMenu.peerUI = peerUI;
        PeerMenu._place();

        $menu.addEventListener('click', e => {
            const act = e.target.closest('button') && e.target.closest('button').dataset.act;
            if (!act) return;
            if (act === 'file') {
                PeerMenu.hide();
                peerUI._allowPick = true;
                peerUI.$input.click();
            }
            else if (act === 'text') {
                PeerMenu.hide();
                Events.fire('text-recipient', {
                    peerId: peerUI._peer.id,
                    deviceName: peerUI.$el.querySelector('.name').innerText
                });
            }
            else if (act === 'speed') {
                PeerMenu._startSpeedtest(peerUI);
            }
            else if (act === 'close') {
                PeerMenu.hide();
            }
        });

        if (!PeerMenu._bound) {
            PeerMenu._bound = true;
            document.addEventListener('pointerdown', e => {
                if (PeerMenu.$menu && !PeerMenu.$menu.contains(e.target)) PeerMenu.hide();
            }, true);
            document.addEventListener('keydown', e => {
                if (e.key === 'Escape') PeerMenu.hide();
            });
            window.addEventListener('resize', () => PeerMenu.hide());
            Events.on('peer-disconnected', e => {
                if (PeerMenu.peerUI && PeerMenu.peerUI._peer.id === e.detail) PeerMenu.hide();
            });
            Events.on('speedtest-state', e => PeerMenu._onSpeedtestState(e.detail));
            Events.on('speedtest-live', e => PeerMenu._onSpeedtestLive(e.detail));
        }
    }

    static hide() {
        if (PeerMenu.$menu) PeerMenu.$menu.remove();
        PeerMenu.$menu = null;
        PeerMenu.peerUI = null;
    }

    static _place() {
        const $menu = PeerMenu.$menu;
        if (!$menu || !PeerMenu.peerUI) return;
        const icon = PeerMenu.peerUI.$el.querySelector('x-icon').getBoundingClientRect();
        const w = $menu.offsetWidth;
        const h = $menu.offsetHeight;
        let left = icon.left + icon.width / 2 - w / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
        let top = icon.bottom + 6;
        if (top + h > window.innerHeight - 8) top = Math.max(8, icon.top - h - 6);
        $menu.style.left = left + 'px';
        $menu.style.top = top + 'px';
    }

    static _startSpeedtest(peerUI) {
        PeerMenu._speedRows = {};
        PeerMenu.$menu.innerHTML = `
            <div class="pm-title">测网速</div>
            <div class="pm-line"><span>连接方式</span><span data-k="protocol">…</span></div>
            <div class="pm-line"><span>延迟</span><span data-k="rtt">…</span></div>
            <div class="pm-line"><span>上传（本机→对方）</span><span data-k="up">…</span></div>
            <div class="pm-line"><span>下载（对方→本机）</span><span data-k="down">…</span></div>
            <div class="pm-note" data-k="note">测延迟中…</div>
            <button type="button" data-act="close">关闭</button>`;
        PeerMenu._place();
        Events.fire('speedtest-request', {peerId: peerUI._peer.id});
    }

    static _formatSpeed(bytesPerSecond) {
        const mbps = bytesPerSecond * 8 / 1e6;
        const size = bytesPerSecond >= 1048576
            ? (bytesPerSecond / 1048576).toFixed(bytesPerSecond >= 10485760 ? 1 : 2) + ' MB/s'
            : Math.round(bytesPerSecond / 1024) + ' KB/s';
        return `${size}（${mbps >= 10 ? mbps.toFixed(0) : mbps.toFixed(1)} Mbps）`;
    }

    static _onSpeedtestLive(detail) {
        const $menu = PeerMenu.$menu;
        if (!$menu || !PeerMenu.peerUI || PeerMenu.peerUI._peer.id !== detail.peerId) return;
        const $el = $menu.querySelector(`[data-k="${detail.dir}"]`);
        if ($el) $el.textContent = PeerMenu._formatSpeed(detail.rate);
    }

    static _onSpeedtestState(detail) {
        const $menu = PeerMenu.$menu;
        if (!$menu || !PeerMenu.peerUI || PeerMenu.peerUI._peer.id !== detail.peerId) return;
        const set = (k, text) => { const $el = $menu.querySelector(`[data-k="${k}"]`); if ($el) $el.textContent = text; };
        if (detail.error) {
            set('note', {
                busy: '正在传输文件，传完再测',
                rejected: '对方拒绝了测速',
                'peer-busy': '对方正在传输文件，稍后再测',
                'no-answer': '对方没有回应同意请求，请稍后再试'
            }[detail.error] || '测速失败：对方没有响应，请让对方刷新页面后重试');
            return;
        }
        if (detail.phase) {
            set('note', {latency: '测延迟中…', waiting: '等待对方同意…', up: '测上传中…（实时速度，约 3 秒）', down: '测下载中…（实时速度，约 3 秒）'}[detail.phase] || '');
            return;
        }
        const r = detail.result;
        set('protocol', r.protocol === 'ws' ? '服务器中转' : 'P2P 直连');
        set('rtt', Math.round(r.rtt) + ' ms');
        set('up', PeerMenu._formatSpeed(r.up));
        set('down', PeerMenu._formatSpeed(r.down));
        set('note', '');
    }
}

PeerMenu._askQueueBusy = false;

// patched: the other device asks to run a speed test -> small confirm dialog
Events.on('speedtest-incoming', e => {
    const {peerId, id} = e.detail;
    const answer = (accepted, reason) => Events.fire('speedtest-answer', {peerId: peerId, id: id, accepted: accepted, reason: reason});
    if (PeerMenu._askQueueBusy) return answer(false, 'busy');
    PeerMenu._askQueueBusy = true;
    const tile = document.getElementById(peerId);
    const name = tile ? tile.querySelector('.name').textContent : '另一台设备';
    const $ask = document.createElement('div');
    $ask.className = 'peer-ask';
    $ask.innerHTML = `
        <div class="pa-card">
            <div class="pa-title">测网速请求</div>
            <div class="pa-text"></div>
            <div class="pa-actions">
                <button type="button" data-a="no">拒绝</button>
                <button type="button" data-a="yes" class="primary">同意</button>
            </div>
        </div>`;
    $ask.querySelector('.pa-text').textContent = `「${name}」想和你的设备测一次网速（约 10 秒，会临时占用一些流量）。是否同意？`;
    document.body.appendChild($ask);
    let finished = false;
    const finish = (accepted, reason) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        $ask.remove();
        PeerMenu._askQueueBusy = false;
        answer(accepted, reason);
    };
    const timer = setTimeout(() => finish(false, 'timeout'), 30000);
    $ask.addEventListener('click', ev => {
        const a = ev.target.closest('button') && ev.target.closest('button').dataset.a;
        if (a === 'yes') finish(true);
        else if (a === 'no') finish(false);
    });
    Events.on('peer-disconnected', ev => {
        if (ev.detail === peerId && !finished) {
            finished = true;
            clearTimeout(timer);
            $ask.remove();
            PeerMenu._askQueueBusy = false;
        }
    });
});

class Dialog {
    constructor(id) {""")

# 14. detailed connection log (server) + browser-side diagnostics reported to it (client)
# 14a. ws-server.js: number every connection, log connect / close (code, who closed, duration, bytes, idle, unsent),
#      heartbeat kicks, relay sessions (start / pause / resume / end), undeliverable messages and a status line every 5 min
edit("ws-server.js",
     """import crypto from "crypto"
""",
     """import crypto from "crypto"
import fs from "fs"; // patched: connection log file
""")
edit("ws-server.js",
     """        this._wss.on('connection', (socket, request) => this._onConnection(new Peer(socket, request, conf)));""",
     """        // patched: detailed connection log (see _lg* below)
        this._lgInit();
        this._wss.on('connection', (socket, request) => {
            const peer = new Peer(socket, request, conf);
            this._lgAttach(peer, request);
            this._onConnection(peer);
            this._lgConnected(peer);
        });""")
edit("ws-server.js",
     """            this._setKeepAliveTimerToNow(peer);
            if (isBinary) this._onBinaryChunk(peer, message);""",
     """            this._setKeepAliveTimerToNow(peer);
            this._lgIn(peer, isBinary); // patched: log
            if (isBinary) this._onBinaryChunk(peer, message);""")
edit("ws-server.js",
     """        peer.socket.onerror = e => console.error(e);""",
     """        peer.socket.onerror = e => this._lg('错误', peer, {err: e && (e.message || (e.error && e.error.code)) || String(e)}); // patched: was console.error(e)""")
edit("ws-server.js",
     """            case 'disconnect':
                this._onDisconnect(sender);
                break;""",
     """            case 'disconnect':
                sender._lgCloseBy = sender._lgCloseBy || '客户端发来 disconnect（关闭页面）'; // patched: log
                this._onDisconnect(sender);
                break;
            case 'client-log': // patched: browser-side diagnostics, written to the server log
                this._lgClient(sender, message);
                break;""")
edit("ws-server.js",
     """        // relay message to recipient
        if (message.to && Peer.isValidUuid(message.to) && this._rooms[room]) {""",
     """        this._lgRelayMsg(sender, room, message); // patched: log transfer control messages and undeliverable ones

        // relay message to recipient
        if (message.to && Peer.isValidUuid(message.to) && this._rooms[room]) {""")
edit("ws-server.js",
     """        if (!recipient) return;
        const chunk = data.subarray(36);""",
     """        this._lgChunk(sender, to, recipient, data.length - 36); // patched: relay sessions / pauses / drops
        if (!recipient) return;
        const chunk = data.subarray(36);""")
edit("ws-server.js",
     """        if (this._latestPeer[sender.id] && this._latestPeer[sender.id] !== sender) {
            sender.socket.terminate();""",
     """        if (this._latestPeer[sender.id] && this._latestPeer[sender.id] !== sender) {
            sender._lgCloseBy = sender._lgCloseBy || '旧连接，已被同一设备的新连接取代'; // patched: log
            sender.socket.terminate();""")
edit("ws-server.js",
     """            // patched: disconnect peer if unresponsive for 30s (upstream: 5s, too strict for cross-border links)
            this._disconnect(peer);""",
     """            // patched: disconnect peer if unresponsive for 30s (upstream: 5s, too strict for cross-border links)
            peer._lgCloseBy = '心跳超时，服务器踢出（30 秒没收到任何消息）'; // patched: log
            this._lgKick(peer);
            this._disconnect(peer);""")
edit("ws-server.js",
     """        message = JSON.stringify(message);
        peer.socket.send(message);
    }""",
     """        message = JSON.stringify(message);
        peer.socket.send(message);
        this._lgOut(peer); // patched: log
    }""")
edit("ws-server.js",
     """    _keepAlive(peer) {""",
     r"""    // ---- patched: detailed connection log ----------------------------------------------------------------------
    // One line per event to stdout (docker logs pairdrop) and to /home/node/app/logs/pairdrop-YYYY-MM-DD.log
    // (host: /opt/pairdrop/logs, kept 14 days). No file names, message text or room secrets are logged.
    _lgInit() {
        this._lgSeq = 0;
        this._lgRelays = {}; // "senderId>recipientId" -> relay session
        this._lgReq = {}; // "senderId>recipientId" -> {n: files in the last transfer request, t}
        this._lgSpeedAt = {}; // "senderId>recipientId" -> time of the last speed test 'start'
        this._lgDropAt = {}; // rate limit for "undeliverable" lines
        this._lgDir = '/home/node/app/logs';
        this._lgStream = null;
        this._lgDay = '';
        this._lg('启动', null, {pid: process.pid, node: process.version});
        setInterval(() => this._lgSweep(), 2000).unref();
        setInterval(() => this._lgStatus(), 5 * 60 * 1000).unref();
    }

    _lgTime(t) {
        const d = new Date(t);
        const p = (n, w = 2) => String(n).padStart(w, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
    }

    _lgBytes(n) {
        if (!(n >= 0)) return '?';
        if (n >= 1048576) return (n / 1048576).toFixed(1) + 'MB';
        if (n >= 1024) return (n / 1024).toFixed(1) + 'KB';
        return Math.round(n) + 'B';
    }

    _lgSec(ms) {
        return (ms / 1000).toFixed(1) + 's';
    }

    _lgWho(peer) {
        if (!peer) return '-';
        return `#${peer._lgId || '?'}${peer.name && peer.name.displayName || '?'}(${String(peer.id).slice(0, 8)})`;
    }

    _lg(event, peer, fields) {
        const now = Date.now();
        let line = `${this._lgTime(now)} [${event}]` + (peer ? ' ' + this._lgWho(peer) : '');
        for (const k in fields || {}) {
            const v = fields[k];
            if (v === undefined || v === null || v === '') continue;
            const s = typeof v === 'string' ? v : JSON.stringify(v);
            line += ` ${k}=${/[\s"]/.test(s) ? JSON.stringify(s) : s}`;
        }
        console.log(line);
        this._lgWrite(now, line);
    }

    _lgWrite(now, line) {
        try {
            const day = this._lgTime(now).slice(0, 10);
            if (day !== this._lgDay) {
                if (this._lgStream) this._lgStream.end();
                this._lgStream = null;
                this._lgDay = day;
                if (!fs.existsSync(this._lgDir)) return;
                this._lgStream = fs.createWriteStream(`${this._lgDir}/pairdrop-${day}.log`, {flags: 'a'});
                this._lgStream.on('error', () => { this._lgStream = null; });
                this._lgPrune();
            }
            if (this._lgStream) this._lgStream.write(line + '\n');
        } catch (e) {}
    }

    _lgPrune() {
        fs.readdir(this._lgDir, (err, files) => {
            if (err) return;
            const cutoff = Date.now() - 14 * 86400000;
            for (const f of files) {
                if (!/^pairdrop-\d{4}-\d{2}-\d{2}\.log$/.test(f)) continue;
                const p = `${this._lgDir}/${f}`;
                fs.stat(p, (e, st) => { if (!e && st.mtimeMs < cutoff) fs.unlink(p, () => {}); });
            }
        });
    }

    _lgAttach(peer, request) {
        const h = request.headers || {};
        const now = Date.now();
        peer._lgId = ++this._lgSeq;
        peer._lgT0 = now;
        peer._lgIp = String(h['x-real-ip'] || (request.socket && request.socket.remoteAddress) || '?').replace(/^::ffff:/, '');
        const m = /(?:EdgA|Edg|OPR|SamsungBrowser|MicroMessenger|CriOS|FxiOS|Chrome|Firefox|Version)\/(\d+)/.exec(String(h['user-agent'] || ''));
        peer._lgVer = m ? m[1] : '';
        peer._lgRaw = request.socket;
        peer._lgInJson = 0;
        peer._lgInBin = 0;
        peer._lgOutMsgs = 0;
        peer._lgLastIn = now;
        peer._lgMaxBuf = 0;
        peer._lgClientWin = now;
        peer._lgClientN = 0;
        const old = this._latestPeer[peer.id];
        if (old && old !== peer) {
            peer._lgReplaces = `#${old._lgId || '?'}(${['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][old.socket.readyState]},最后收到${this._lgSec(now - (old._lgLastIn || now))}前)`;
        }
        peer.socket.on('close', (code, reason) => this._lgOnClose(peer, code, reason));
    }

    _lgConnected(peer) {
        this._lg('连接', peer, {
            ip: peer._lgIp,
            dev: peer.name.deviceName + (peer._lgVer ? ' ' + peer._lgVer : ''),
            rtc: peer.rtcSupported ? 1 : 0,
            bin: peer.wsBinary ? 1 : 0,
            replaces: peer._lgReplaces,
            online: Object.keys(this._latestPeer).length
        });
    }

    _lgIn(peer, isBinary) {
        peer._lgLastIn = Date.now();
        if (isBinary) peer._lgInBin++;
        else peer._lgInJson++;
    }

    _lgOut(peer) {
        peer._lgOutMsgs++;
        const b = peer.socket.bufferedAmount;
        if (b > peer._lgMaxBuf) peer._lgMaxBuf = b;
    }

    _lgOnClose(peer, code, reason) {
        const now = Date.now();
        const raw = peer._lgRaw;
        const by = peer._lgCloseBy || {
            1000: '浏览器正常关闭',
            1001: '浏览器离开页面（关闭 / 刷新 / 跳转）',
            1005: '浏览器关闭（没带关闭码）',
            1006: '连接异常中断：没收到关闭帧（网络断开、代理 / CDN 掐断、浏览器被冻结或被杀）',
            1009: '消息过大',
            1011: '服务器内部错误',
            4000: '浏览器关闭页面时主动断开',
            4001: '浏览器 20 秒没收到服务器消息，判定掉线后重连'
        }[code] || '其他';
        const relay = this._lgEndRelaysOf(peer, '连接断开');
        const latest = this._latestPeer[peer.id];
        this._lg('断开', peer, {
            ip: peer._lgIp,
            code: code,
            reason: reason && reason.length ? reason.toString().slice(0, 80) : undefined,
            by: by,
            dur: this._lgSec(now - peer._lgT0),
            idle: this._lgSec(now - peer._lgLastIn),
            rx: raw ? this._lgBytes(raw.bytesRead) : undefined,
            tx: raw ? this._lgBytes(raw.bytesWritten) : undefined,
            msgs: `${peer._lgInJson}/${peer._lgInBin}/${peer._lgOutMsgs}`,
            unsent: this._lgBytes(peer.socket.bufferedAmount),
            maxbuf: this._lgBytes(peer._lgMaxBuf),
            relay: relay,
            // still listed: other devices keep seeing it until the heartbeat kicks it (or it reconnects)
            listed: latest === peer ? 1 : undefined,
            replaced_by: latest && latest !== peer ? '#' + latest._lgId : undefined
        });
    }

    _lgKick(peer) {
        this._lg('踢出', peer, {
            why: '30 秒没收到任何消息',
            state: ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][peer.socket.readyState],
            idle: this._lgSec(Date.now() - (peer._lgLastIn || 0)),
            unsent: this._lgBytes(peer.socket.bufferedAmount)
        });
    }

    _lgClient(peer, message) {
        const now = Date.now();
        if (now - peer._lgClientWin > 60000) {
            peer._lgClientWin = now;
            peer._lgClientN = 0;
        }
        const events = Array.isArray(message.events) ? message.events.slice(0, 50) : [];
        for (const e of events) {
            if (++peer._lgClientN > 300) return; // at most 300 browser events per connection per minute
            if (!e || typeof e !== 'object') continue;
            const fields = {ev: String(e.ev).slice(0, 40)};
            let n = 0;
            for (const k in e) {
                if (k === 'ev' || k === 't' || ++n > 20) continue;
                let v = e[k];
                if (v === undefined || v === null) continue;
                if (typeof v === 'object') v = JSON.stringify(v);
                fields[String(k).slice(0, 24)] = String(v).slice(0, 200);
            }
            const t = Number(e.t);
            if (t) fields.at = this._lgTime(t).slice(11) + (Math.abs(now - t) > 3000 ? `(${this._lgSec(now - t)}前)` : '');
            this._lg('浏览器', peer, fields);
        }
    }

    _lgDrop(sender, to, type, bytes) {
        const now = Date.now();
        const k = `${sender.id}>${to}:${type}`;
        if (this._lgDropAt[k] && now - this._lgDropAt[k] < 10000) return;
        this._lgDropAt[k] = now;
        this._lg('转发失败', sender, {
            type: type,
            to: `${String(to || '?').slice(0, 8)}(不在同一房间或已离线)`,
            bytes: bytes !== undefined ? this._lgBytes(bytes) : undefined
        });
    }

    _lgRelayMsg(sender, room, message) {
        const type = message.type;
        const to = typeof message.to === 'string' ? message.to : '';
        const recipient = to && this._rooms[room] ? this._rooms[room][to] : undefined;
        if (!recipient) {
            this._lgDrop(sender, to, type);
            return;
        }
        const now = Date.now();
        const key = sender.id + '>' + to;
        const back = to + '>' + sender.id; // the session this message answers (receiver -> sender acks)
        const target = this._lgWho(recipient);
        switch (type) {
            case 'request':
                this._lgReq[key] = {n: Array.isArray(message.header) ? message.header.length : 0, t: now};
                this._lg('传输请求', sender, {to: target, files: this._lgReq[key].n, size: this._lgBytes(message.totalSize)});
                break;
            case 'files-transfer-response':
                this._lg('传输应答', sender, {to: target, accepted: message.accepted ? 1 : 0, reason: message.reason});
                break;
            case 'header': {
                const s = this._lgRelay(sender, recipient, key, now);
                s.files++;
                s.last = now;
                this._lg('文件开始', sender, {to: target, n: s.expect ? `${s.files}/${s.expect}` : s.files, size: this._lgBytes(message.size)});
                break;
            }
            case 'file-transfer-complete': {
                const s = this._lgRelays[back];
                if (s) {
                    s.done++;
                    s.last = now;
                }
                this._lg('文件收完', sender, {from: target, n: s ? `${s.done}/${s.expect || s.files}` : undefined});
                break;
            }
            case 'partition':
                if (this._lgRelays[key]) this._lgRelays[key].parts++;
                break;
            case 'partition-received':
                if (this._lgRelays[back]) this._lgRelays[back].acks++;
                break;
            case 'text':
                this._lg('发信息', sender, {to: target, len: typeof message.text === 'string' ? message.text.length : 0});
                break;
            case 'speedtest':
                if (message.phase === 'start') this._lgSpeedAt[key] = now;
                if (message.phase === 'end' && this._lgRelays[key]) this._lgEndRelay(key, this._lgRelays[key], '测速结束');
                if (['request', 'accept', 'reject', 'result'].includes(message.phase)) {
                    this._lg('测速', sender, {
                        to: target,
                        phase: message.phase,
                        reason: message.reason,
                        rate: message.phase === 'result' && message.ms > 0 ? this._lgBytes(message.bytes * 1000 / message.ms) + '/s' : undefined
                    });
                }
                break;
            case 'signal':
                if (message.sdp) this._lg('直连协商', sender, {to: target, sdp: message.sdp.type});
                else if (message.connected !== undefined && !message.ice) this._lg('中转握手', sender, {to: target, connected: message.connected ? 1 : 0});
                break;
        }
    }

    _lgRelay(sender, recipient, key, now) {
        let s = this._lgRelays[key];
        if (!s) {
            const speedAt = this._lgSpeedAt[key];
            const req = this._lgReq[key];
            s = this._lgRelays[key] = {
                sender: sender, recipient: recipient, t0: now, last: now, bytes: 0, chunks: 0, maxGap: 0, maxBuf: 0,
                parts: 0, acks: 0, files: 0, done: 0, pauses: 0, pauseLogged: false,
                expect: req ? req.n : 0,
                kind: speedAt && now - speedAt < 10000 ? '测速' : '文件'
            };
            delete this._lgReq[key];
            this._lg('中转开始', sender, {to: this._lgWho(recipient), kind: s.kind, files: s.expect || undefined});
        }
        s.sender = sender;
        s.recipient = recipient;
        return s;
    }

    _lgChunk(sender, to, recipient, bytes) {
        if (!recipient) {
            this._lgDrop(sender, to, '文件数据', bytes);
            return;
        }
        const now = Date.now();
        const s = this._lgRelay(sender, recipient, sender.id + '>' + to, now);
        const gap = now - s.last;
        if (s.chunks && gap > s.maxGap) s.maxGap = gap;
        if (s.pauseLogged) {
            s.pauseLogged = false;
            this._lg('中转恢复', sender, {to: this._lgWho(recipient), gap: this._lgSec(gap)});
        }
        s.last = now;
        s.bytes += bytes;
        s.chunks++;
        const buf = recipient.socket.bufferedAmount;
        if (buf > s.maxBuf) s.maxBuf = buf;
    }

    _lgEndRelay(key, s, why) {
        delete this._lgRelays[key];
        const ms = s.last - s.t0;
        const total = s.expect || s.files;
        this._lg('中转结束', s.sender, {
            to: this._lgWho(s.recipient),
            kind: s.kind,
            why: why,
            ok: total ? (s.done >= total ? 1 : 0) : undefined,
            files: total ? `${s.done}/${total}` : undefined,
            sent: this._lgBytes(s.bytes),
            dur: this._lgSec(ms),
            avg: ms > 0 ? this._lgBytes(s.bytes * 1000 / ms) + '/s' : undefined,
            acks: `${s.acks}/${s.parts}`,
            maxgap: this._lgSec(s.maxGap),
            maxbuf: this._lgBytes(s.maxBuf),
            pauses: s.pauses || undefined
        });
    }

    _lgEndRelaysOf(peer, why) {
        const out = [];
        for (const key in this._lgRelays) {
            const s = this._lgRelays[key];
            if (s.sender !== peer && s.recipient !== peer) continue;
            const role = s.sender === peer ? '发送方' : '接收方';
            out.push(`${role}${this._lgBytes(s.bytes)}`);
            this._lgEndRelay(key, s, `${why}（${role}）`);
        }
        return out.join(',') || undefined;
    }

    _lgSweep() {
        const now = Date.now();
        for (const key in this._lgRelays) {
            const s = this._lgRelays[key];
            const idle = now - s.last;
            const total = s.expect || s.files;
            if (total && s.done >= total && idle > 2000) {
                this._lgEndRelay(key, s, '文件全部收完');
            }
            else if (idle > 30000) {
                this._lgEndRelay(key, s, '30 秒没有新数据');
            }
            else if (idle > 5000 && !s.pauseLogged && s.kind !== '测速') {
                // window full (acks < parts - 2) = waiting for the receiver; otherwise the sender stopped sending
                s.pauseLogged = true;
                s.pauses++;
                this._lg('中转停顿', s.sender, {
                    to: this._lgWho(s.recipient),
                    idle: this._lgSec(idle),
                    sent: this._lgBytes(s.bytes),
                    acks: `${s.acks}/${s.parts}`,
                    files: total ? `${s.done}/${total}` : undefined,
                    sender_idle: this._lgSec(now - s.sender._lgLastIn),
                    recv_idle: this._lgSec(now - s.recipient._lgLastIn),
                    recv_unsent: this._lgBytes(s.recipient.socket.bufferedAmount)
                });
            }
        }
        for (const k in this._lgDropAt) if (now - this._lgDropAt[k] > 60000) delete this._lgDropAt[k];
        for (const k in this._lgReq) if (now - this._lgReq[k].t > 600000) delete this._lgReq[k];
        for (const k in this._lgSpeedAt) if (now - this._lgSpeedAt[k] > 60000) delete this._lgSpeedAt[k];
    }

    _lgStatus() {
        const peers = Object.values(this._latestPeer);
        if (!peers.length) return;
        const now = Date.now();
        this._lg('状态', null, {
            online: peers.length,
            peers: peers.map(p => `#${p._lgId}${p.name.displayName}@${p._lgIp}/在线${this._lgSec(now - p._lgT0)}/静默${this._lgSec(now - p._lgLastIn)}`).join(','),
            relays: Object.keys(this._lgRelays).length,
            rss: this._lgBytes(process.memoryUsage().rss)
        });
    }
    // ---- end of connection log -------------------------------------------------------------------------------

    _keepAlive(peer) {""")

# 14b. network.js: browser-side diagnostics (the server cannot see why a browser closed, froze or lost a transfer)
edit("network.js",
     """class ServerConnection {

    constructor() {
        Events.on('pagehide', _ => this._disconnect());""",
     """// patched: connection diagnostics. Events are queued (kept in sessionStorage across reloads) and sent to the server
// as 'client-log', which writes them into the server log. No file names or message text are included.
const PdLog = {
    server: null,
    peers: null,
    queue: [],
    init() {
        try { this.queue = JSON.parse(sessionStorage.getItem('pd-log') || '[]'); } catch (e) { this.queue = []; }
        if (!Array.isArray(this.queue)) this.queue = [];
    },
    add(ev, data) {
        const e = Object.assign({ev: ev, t: Date.now()}, data);
        console.log('PdLog:', e);
        this.queue.push(e);
        if (this.queue.length > 200) this.queue.splice(0, this.queue.length - 200);
        this.save();
        this.flush();
    },
    save() {
        try { sessionStorage.setItem('pd-log', JSON.stringify(this.queue)); } catch (e) {}
    },
    flush() {
        const s = this.server;
        if (!s || !s._isConnected() || !this.queue.length) return;
        const events = this.queue.splice(0, 50);
        this.save();
        s.send({type: 'client-log', events: events});
        if (this.queue.length) setTimeout(() => this.flush(), 200);
    },
    env() {
        const c = navigator.connection;
        return {
            vis: document.visibilityState,
            online: navigator.onLine ? 1 : 0,
            net: c ? [c.type, c.effectiveType, c.rtt, c.downlink].filter(v => v !== undefined).join('/') : undefined
        };
    },
    // transfers running right now, e.g. "1a2b3c4d:ws:send"
    busy() {
        const pm = this.peers;
        if (!pm) return undefined;
        const out = [];
        for (const id in pm.peers) {
            const p = pm.peers[id];
            if (p._pdXfer) out.push(`${id.slice(0, 8)}:${p._transferProtocol()}:${p._pdXfer.dir}`);
        }
        return out.join(',') || undefined;
    }
};
PdLog.init();

class ServerConnection {

    constructor() {
        PdLog.server = this; // patched: diagnostics
        window.addEventListener('offline', () => PdLog.add('offline', PdLog.env()));
        window.addEventListener('online', () => PdLog.add('online', PdLog.env()));
        document.addEventListener('visibilitychange', () => PdLog.add('visibility', Object.assign(PdLog.env(), {busy: PdLog.busy()})));
        document.addEventListener('freeze', () => PdLog.add('page-freeze', PdLog.env()));
        document.addEventListener('resume', () => PdLog.add('page-resume', PdLog.env()));
        window.addEventListener('pageshow', e => { if (e.persisted) PdLog.add('page-restored', PdLog.env()); });
        if (navigator.connection) {
            const netKind = () => `${navigator.connection.type || '?'}/${navigator.connection.effectiveType || '?'}`;
            let lastNet = netKind();
            navigator.connection.addEventListener('change', () => {
                const net = netKind();
                if (net !== lastNet) PdLog.add('net-change', {from: lastNet, to: net, busy: PdLog.busy()});
                lastNet = net;
            });
        }
        Events.on('pagehide', _ => this._disconnect());""")
edit("network.js",
     """        const ws = new WebSocket(this._endpoint());
        ws.binaryType = 'arraybuffer';
        ws.onopen = _ => this._onOpen();
        ws.onmessage = e => this._onMessage(e.data);
        ws.onclose = _ => this._onDisconnect();""",
     """        const ws = new WebSocket(this._endpoint());
        ws.binaryType = 'arraybuffer';
        ws._pdT0 = Date.now(); // patched: diagnostics
        this._pdTries = (this._pdTries || 0) + 1;
        ws.onopen = _ => { ws._pdOpenAt = Date.now(); this._onOpen(); };
        ws.onmessage = e => this._onMessage(e.data);
        ws.onclose = e => { this._pdLogClose(ws, e); this._onDisconnect(); }; // patched: log why it closed""")
edit("network.js",
     """    _onOpen() {
        this._lastMessageAt = Date.now(); // patched
        console.log('WS: server connected');""",
     """    _onOpen() {
        this._lastMessageAt = Date.now(); // patched
        const ws = this._socket; // patched: diagnostics
        PdLog.add('ws-open', Object.assign({
            connect_ms: ws && ws._pdOpenAt ? ws._pdOpenAt - ws._pdT0 : undefined,
            down_ms: this._pdDownAt ? Date.now() - this._pdDownAt : undefined,
            tries: this._pdTries
        }, PdLog.env()));
        this._pdDownAt = 0;
        this._pdTries = 0;
        console.log('WS: server connected');""")
edit("network.js",
     """        console.log('WS: no message from server for 20s, reconnecting');
        this._socket.onclose = null;
        this._socket.close();""",
     """        console.log('WS: no message from server for 20s, reconnecting');
        // patched: diagnostics; code 4001 tells the server log why (if the close frame still gets through)
        PdLog.add('ws-silent', Object.assign({
            silent_ms: Date.now() - this._lastMessageAt,
            up_ms: this._socket._pdOpenAt ? Date.now() - this._socket._pdOpenAt : undefined,
            unsent: this._socket.bufferedAmount,
            busy: PdLog.busy()
        }, PdLog.env()));
        this._pdDownAt = Date.now();
        this._socket.onclose = null;
        this._socket.close(4001, 'no message for 20s');""")
edit("network.js",
     """    _disconnect() {
        this.send({ type: 'disconnect' });""",
     """    _disconnect() {
        PdLog.add('ws-disconnect', Object.assign({why: 'pagehide', busy: PdLog.busy()}, PdLog.env())); // patched
        this.send({ type: 'disconnect' });""")
edit("network.js",
     """        if (!this._socket) return;

        this._socket.onclose = null;
        this._socket.close();""",
     """        if (!this._socket) return;

        this._socket.onclose = null;
        this._socket.close(4000, 'pagehide'); // patched: tells the server log why""")
edit("network.js",
     """    _reconnect() {
        this._disconnect();
        this._connect();
    }""",
     """    _reconnect() {
        this._disconnect();
        this._connect();
    }

    // patched: why did the socket close? (1006 = dropped without a close frame: network / proxy / CDN / frozen tab)
    _pdLogClose(ws, e) {
        const now = Date.now();
        if (!this._pdDownAt) this._pdDownAt = now;
        PdLog.add(ws._pdOpenAt ? 'ws-close' : 'ws-connect-failed', Object.assign({
            code: e.code,
            reason: e.reason || undefined,
            clean: e.wasClean ? 1 : 0,
            up_ms: ws._pdOpenAt ? now - ws._pdOpenAt : now - ws._pdT0,
            silent_ms: ws._pdOpenAt && this._lastMessageAt ? now - this._lastMessageAt : undefined,
            unsent: ws.bufferedAmount,
            busy: PdLog.busy()
        }, PdLog.env()));
    }""")
# transfers: start / done / rejected / aborted, with protocol (ws relay / rtc direct), size and progress
edit("network.js",
     """    _transferProtocol() {
        return this instanceof WSPeer ? 'ws' : 'rtc';
    }""",
     """    _transferProtocol() {
        return this instanceof WSPeer ? 'ws' : 'rtc';
    }

    // patched: transfer diagnostics for PdLog
    _pdShort() {
        return String(this._peerId).slice(0, 8);
    }

    _pdXferStart(dir, files, size) {
        this._pdXfer = {t0: Date.now(), dir: dir, files: files, size: size};
        this._pdLastProgress = 0;
        PdLog.add('xfer-start', {peer: this._pdShort(), proto: this._transferProtocol(), dir: dir, files: files, size: size});
    }

    _pdXferEnd(ev) {
        const x = this._pdXfer;
        if (!x) return;
        this._pdXfer = null;
        const secs = (Date.now() - x.t0) / 1000;
        PdLog.add(ev, Object.assign({
            peer: this._pdShort(),
            proto: this._transferProtocol(),
            dir: x.dir,
            files: x.files,
            size: x.size,
            secs: secs.toFixed(1),
            progress: ev === 'xfer-done' ? undefined : (this._pdLastProgress || 0).toFixed(3),
            rate: ev === 'xfer-done' && secs > 0 && x.size ? Math.round(x.size / secs) : undefined
        }, ev === 'xfer-done' ? {} : PdLog.env()));
    }""")
edit("network.js",
     """    _onDownloadProgress(progress, totalSize) {
        // patched: totalSize + protocol let PeerUI show speed and ETA""",
     """    _onDownloadProgress(progress, totalSize) {
        this._pdLastProgress = progress; // patched: diagnostics
        // patched: totalSize + protocol let PeerUI show speed and ETA""")
edit("network.js",
     """        if (!message.accepted) {
            Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});""",
     """        if (!message.accepted) {
            PdLog.add('xfer-rejected', {peer: this._pdShort(), proto: this._transferProtocol(), reason: message.reason}); // patched
            Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});""")
edit("network.js",
     """        Events.fire('file-transfer-accepted');
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'transfer', totalSize: this._outgoingTotalSize, protocol: this._transferProtocol()});""",
     """        Events.fire('file-transfer-accepted');
        this._pdXferStart('send', this._filesRequested ? this._filesRequested.length : 0, this._outgoingTotalSize); // patched
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'transfer', totalSize: this._outgoingTotalSize, protocol: this._transferProtocol()});""")
edit("network.js",
     """    _onFileTransferCompleted() {
        this._chunker = null;
        if (!this._filesQueue.length) {
            this._busy = false;""",
     """    _onFileTransferCompleted() {
        this._chunker = null;
        if (!this._filesQueue.length) {
            this._busy = false;
            this._pdXferEnd('xfer-done'); // patched""")
edit("network.js",
     """        if (accepted) {
            this._requestAccepted = this._requestPending;
            this._totalBytesReceived = 0;""",
     """        if (accepted) {
            this._pdXferStart('recv', this._requestPending.header ? this._requestPending.header.length : 0, this._requestPending.totalSize); // patched
            this._requestAccepted = this._requestPending;
            this._totalBytesReceived = 0;""")
edit("network.js",
     """        if (!this._requestAccepted.header.length) {
            this._busy = false;""",
     """        if (!this._requestAccepted.header.length) {
            this._busy = false;
            this._pdXferEnd('xfer-done'); // patched""")
# WebRTC: time to open, selected path (host = same LAN, srflx = via public address), state changes, errors
edit("network.js",
     """        this.rtcSupported = true;
        this.rtcConfig = rtcConfig
""",
     """        this.rtcSupported = true;
        this.rtcConfig = rtcConfig
        this._pdT0 = Date.now(); // patched: diagnostics
""")
edit("network.js",
     """    _onChannelOpened(event) {
        console.log('RTC: channel opened with', this._peerId);""",
     """    _onChannelOpened(event) {
        console.log('RTC: channel opened with', this._peerId);
        PdLog.add('rtc-open', {peer: this._pdShort(), ms: Date.now() - this._pdT0, caller: this._isCaller ? 1 : 0}); // patched
        this._pdLogPath();""")
edit("network.js",
     """    _onChannelClosed() {
        console.log('RTC: channel closed', this._peerId);""",
     """    _onChannelClosed() {
        console.log('RTC: channel closed', this._peerId);
        PdLog.add('rtc-channel-closed', Object.assign({peer: this._pdShort(), busy: this._pdXfer ? this._pdXfer.dir : undefined}, PdLog.env())); // patched""")
edit("network.js",
     """    _onConnectionStateChange() {
        console.log('RTC: state changed:', this._conn.connectionState);""",
     """    // patched: which candidate pair carries the data (host = same LAN, srflx = public address via STUN)
    _pdLogPath() {
        const conn = this._conn;
        if (!conn || !conn.getStats) return;
        conn.getStats().then(stats => {
            const byId = {};
            let pair = null;
            stats.forEach(r => { byId[r.id] = r; });
            stats.forEach(r => { if (r.type === 'transport' && r.selectedCandidatePairId) pair = byId[r.selectedCandidatePairId]; });
            if (!pair) stats.forEach(r => { if (!pair && r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') pair = r; });
            if (!pair) return;
            const l = byId[pair.localCandidateId] || {};
            const r = byId[pair.remoteCandidateId] || {};
            PdLog.add('rtc-path', {
                peer: this._pdShort(),
                local: [l.candidateType, l.protocol, l.networkType].filter(Boolean).join('/'),
                remote: [r.candidateType, r.protocol].filter(Boolean).join('/'),
                rtt_ms: pair.currentRoundTripTime !== undefined ? Math.round(pair.currentRoundTripTime * 1000) : undefined
            });
        }).catch(() => {});
    }

    _onConnectionStateChange() {
        console.log('RTC: state changed:', this._conn.connectionState);
        if (!['new', 'connecting'].includes(this._conn.connectionState)) { // patched: diagnostics
            PdLog.add('rtc-state', Object.assign({peer: this._pdShort(), state: this._conn.connectionState, ice: this._conn.iceConnectionState, busy: this._pdXfer ? this._pdXfer.dir : undefined}, PdLog.env()));
        }""")
edit("network.js",
     """            case 'failed':
                Events.fire('rtc-failed', {peerId: this._peerId}); // patched: PeersManager falls back to the relay""",
     """            case 'failed':
                PdLog.add('rtc-ice-failed', {peer: this._pdShort(), ms: Date.now() - this._pdT0}); // patched
                Events.fire('rtc-failed', {peerId: this._peerId}); // patched: PeersManager falls back to the relay""")
edit("network.js",
     """    _onError(error) {
        console.error(error);
    }

    _send(message) {
        if (!this._channel) this.refresh();""",
     """    _onError(error) {
        console.error(error);
        // patched: diagnostics (ICE candidate errors, e.g. an unreachable IPv6 STUN path, are routine and skipped;
        // data channel errors arrive as RTCErrorEvent with the RTCError in .error)
        const err = error && error.error instanceof Error ? error.error : error;
        if (typeof err === 'string' || err instanceof Error) {
            PdLog.add('rtc-error', {peer: this._pdShort(), err: String(err.message || err).slice(0, 120), busy: this._pdXfer ? this._pdXfer.dir : undefined});
        }
    }

    _send(message) {
        if (!this._channel) this.refresh();""")
# PeersManager: switching a peer to the relay, peers leaving / disconnecting (and the transfer that dies with them)
edit("network.js",
     """    constructor(serverConnection) {
        this.peers = {};
        this._server = serverConnection;""",
     """    constructor(serverConnection) {
        this.peers = {};
        this._server = serverConnection;
        PdLog.peers = this; // patched: diagnostics""")
edit("network.js",
     """    _swapToWsPeer(peerId, oldPeer, isCaller) {""",
     """    _swapToWsPeer(peerId, oldPeer, isCaller) {
        PdLog.add('switch-to-relay', { // patched: diagnostics
            peer: peerId.slice(0, 8),
            side: isCaller ? 'caller-timeout-or-ice-failed' : 'callee-got-ws-handshake',
            rtc: oldPeer._conn ? `${oldPeer._conn.connectionState}/${oldPeer._conn.iceConnectionState}` : 'none',
            waited_ms: oldPeer._pdT0 ? Date.now() - oldPeer._pdT0 : undefined
        });""")
edit("network.js",
     """    _onPeerLeft(message) {
        if (this._peerExists(message.peerId) && this._webRtcSupported(message.peerId)) {""",
     """    _onPeerLeft(message) {
        const leaving = this.peers[message.peerId]; // patched: diagnostics
        PdLog.add('peer-left', {
            peer: String(message.peerId).slice(0, 8),
            disconnect: message.disconnect ? 1 : 0,
            known: leaving ? leaving._transferProtocol() : 'no',
            busy: leaving && leaving._pdXfer ? leaving._pdXfer.dir : undefined
        });
        if (this._peerExists(message.peerId) && this._webRtcSupported(message.peerId)) {""")
edit("network.js",
     """    _onPeerDisconnected(peerId) {
        const peer = this.peers[peerId];
        delete this.peers[peerId];""",
     """    _onPeerDisconnected(peerId) {
        const peer = this.peers[peerId];
        if (peer) { // patched: diagnostics -- a running transfer dies here
            PdLog.add('peer-disconnected', {peer: String(peerId).slice(0, 8), proto: peer._transferProtocol(), busy: peer._pdXfer ? peer._pdXfer.dir : undefined, ws: this._server._isConnected() ? 1 : 0});
            peer._pdXferEnd('xfer-abort');
        }
        delete this.peers[peerId];""")

# 17. WebRTC direct-transfer speed: pipelined disk reads, a bounded send queue instead of dropped chunks,
#     a wider ack window, and Blob-backed receive buffers so a multi-GB file is not pinned in the JS heap.
edit("network.js",
     """class FileChunker {

    constructor(file, onChunk, onPartitionEnd) {
        this._chunkSize = 64000; // 64 KB
        this._maxPartitionSize = 1e6; // 1 MB
        this._offset = 0;
        this._partitionSize = 0;
        this._file = file;
        this._onChunk = onChunk;
        this._onPartitionEnd = onPartitionEnd;
        this._reader = new FileReader();
        this._reader.addEventListener('load', e => this._onChunkRead(e.target.result));
    }

    nextPartition() {
        this._partitionSize = 0;
        this._readChunk();
    }

    _readChunk() {
        const chunk = this._file.slice(this._offset, this._offset + this._chunkSize);
        this._reader.readAsArrayBuffer(chunk);
    }

    _onChunkRead(chunk) {
        this._offset += chunk.byteLength;
        this._partitionSize += chunk.byteLength;
        this._onChunk(chunk);
        if (this.isFileEnd()) return;
        if (this._isPartitionEnd()) {
            this._onPartitionEnd(this._offset);
            return;
        }
        this._readChunk();
    }

    repeatPartition() {
        this._offset -= this._partitionSize;
        this.nextPartition();
    }""",
     """class FileChunker {

    constructor(file, onChunk, onPartitionEnd, direct = false) {
        // patched: a direct (WebRTC) transfer reads 4 chunks in flight, 512KB each; the server relay keeps
        // the original conservative numbers because every chunk costs the server memory and uplink.
        this._chunkSize = direct ? 512 * 1024 : 64000;
        this._maxPartitionSize = direct ? 4 * 1024 * 1024 : 1e6; // 1 MB
        this._readAhead = direct ? 4 : 1;
        this._offset = 0;
        this._readOffset = 0;
        this._partitionSize = 0;
        this._file = file;
        this._onChunk = onChunk;
        this._onPartitionEnd = onPartitionEnd;
        this._stopped = false;
        this._reads = [];
        this._pumping = false;
        this._wake = false;
        this._waitingDrain = null;
    }

    nextPartition() {
        this._partitionSize = 0;
        this._pump();
    }

    // Re-entrancy safe: _onPartitionEnd may call nextPartition() again before the current pump returns.
    _pump() {
        if (this._stopped) return;
        if (this._pumping) {
            this._wake = true;
            return;
        }
        this._pumping = true;
        this._run().catch(e => console.error('chunker pump failed', e)).then(() => {
            this._pumping = false;
            if (this._wake) {
                this._wake = false;
                this._pump();
            }
        });
    }

    async _run() {
        while (!this._stopped) {
            // keep disk reads one window ahead of the socket so the pipe never waits on the file system
            while (!this._stopped && this._reads.length < this._readAhead && this._readOffset < this._file.size) {
                const start = this._readOffset;
                const end = Math.min(start + this._chunkSize, this._file.size);
                this._readOffset = end;
                this._reads.push(this._file.slice(start, end).arrayBuffer());
            }
            if (!this._reads.length) return;
            const chunk = await this._reads.shift();
            if (this._stopped) return;
            await this._drain();
            if (this._stopped) return;
            this._offset += chunk.byteLength;
            this._partitionSize += chunk.byteLength;
            this._onChunk(chunk);
            if (this.isFileEnd()) {
                this._reads.length = 0;
                return;
            }
            if (this._isPartitionEnd()) {
                this._onPartitionEnd(this._offset);
                return; // the window slot is free again; nextPartition() restarts the pump
            }
        }
    }

    // patched: bounded send queue. Upstream dropped the chunk that crossed the high-water mark, which lost
    // 64KB out of the stream every time the channel backed up; wait for the drain event instead.
    _drain() {
        const channel = this._channel;
        if (!channel || this._stopped) return Promise.resolve();
        if (channel.readyState !== 'open') return new Promise(resolve => setTimeout(resolve, 100));
        if (channel.bufferedAmount <= 32 * 1024 * 1024) return Promise.resolve();
        if (this._waitingDrain) return this._waitingDrain;
        this._waitingDrain = new Promise(resolve => {
            channel.bufferedAmountLowThreshold = 16 * 1024 * 1024;
            const done = () => {
                channel.removeEventListener('bufferedamountlow', done);
                this._waitingDrain = null;
                resolve();
            };
            channel.addEventListener('bufferedamountlow', done, {once: true});
            // safety net only: bufferedamountlow does not fire if the channel dies mid-transfer
            const poll = () => {
                if (this._stopped || channel.readyState !== 'open'
                    || channel.bufferedAmount <= channel.bufferedAmountLowThreshold) return done();
                setTimeout(poll, 100);
            };
            setTimeout(poll, 250);
        });
        return this._waitingDrain;
    }

    stop() {
        this._stopped = true;
        this._reads.length = 0;
        this._waitingDrain = null;
    }

    repeatPartition() {
        this._offset -= this._partitionSize;
        this._readOffset = this._offset;
        this._reads.length = 0;
        this.nextPartition();
    }""")
edit("network.js",
     """        this._chunker = new FileChunker(file,
            chunk => this._send(chunk),
            offset => this._onPartitionEnd(offset));""",
     """        this._chunker = new FileChunker(file,
            chunk => this._send(chunk),
            offset => this._onPartitionEnd(offset),
            this instanceof RTCPeer);""")
# The receiver used to keep every chunk as an ArrayBuffer, so a 6 GB transfer pinned 6 GB in the JS heap
# and then copied it once more into a Blob. Blob parts live in the browser's blob store, which spills to
# disk on its own, so the heap stays flat and the final Blob is assembled without a second copy.
edit("network.js",
     """    unchunk(chunk) {
        this._buffer.push(chunk);""",
     """    unchunk(chunk) {
        this._buffer.push(chunk instanceof Blob ? chunk : new Blob([chunk])); // patched: disk-backed segment""")

# 16. Zero-byte files: the upstream chunker stops on the first empty read without sending a completion.
edit("network.js",
     """    async _sendFile(file) {
        this.sendJSON({
            type: 'header',
            size: file.size,
            name: file.name,
            mime: file.type
        });
        this._chunker = new FileChunker(file,""",
     """    async _sendFile(file) {
        this.sendJSON({
            type: 'header',
            size: file.size,
            name: file.name,
            mime: file.type
        });
        if (file.size === 0) { // patched: empty files have no chunks, so complete them explicitly
            this._chunker = null;
            this.sendJSON({type: 'partition', offset: 0});
            return;
        }
        this._chunker = new FileChunker(file,""")
edit("network.js",
     """    _onFileHeader(header) {
        if (this._requestAccepted && this._requestAccepted.header.length) {""",
     """    _onFileHeader(header) {
        if (this._requestAccepted && this._requestAccepted.header.length) {
            if (header.size === 0) { // patched: an empty file will never receive a chunk
                this._digester = new FileDigester({size: 0, name: header.name, mime: header.mime},
                    this._requestAccepted.totalSize || 1,
                    this._totalBytesReceived,
                    fileBlob => this._onFileReceived(fileBlob)
                );
                this._digester.unchunk(new ArrayBuffer(0));
                return;
            }""")
edit("network.js",
     """    _onChunkReceived(chunk) {
        if(!this._digester || !(chunk.byteLength || chunk.size)) return;""",
     """    _onChunkReceived(chunk) {
        if(!this._digester || !(chunk.byteLength || chunk.size)) return;
        // patched: zero-byte files were completed by _onFileHeader""")
edit("network.js",
     """    _onFileTransferRequestResponded(message) {
        if (!message.accepted) {""",
     """    _onFileTransferRequestResponded(message) {
        if (message.accepted && this._outgoingTotalSize === 0) {
            // patched: an all-empty batch has no data chunks to drive progress; finish it now.
            this._filesRequested = null;
            Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});
            Events.fire('notify-user', Localization.getTranslation("notifications.file-transfer-completed"));
            Events.fire('files-sent');
        }
        if (!message.accepted) {""")

# 15. 拖拽快捷发送：拖到页面进入快捷发送提示，拖到设备则立即发送；文件夹递归展开并保留相对路径。
# DataTransfer.items 和 FileSystemEntry 在 drop 事件结束后失效，因此先同步读取 getAsFile()，
# 文件夹内容通过 readEntries 逐批读取（每批最多 100 项）。
edit("ui.js",
     """class PeersUI {

    constructor() {""",
     """// patched: read dropped files / folders synchronously enough to survive the drop event.
class DroppedFiles {
    static async fromEvent(e) {
        const items = [...(e.dataTransfer.items || [])].filter(item => item.kind === 'file');
        const snapshots = items.map(item => ({
            file: item.getAsFile && item.getAsFile(),
            entry: item.webkitGetAsEntry && item.webkitGetAsEntry()
        })).filter(item => item.file || item.entry);
        const flatFiles = [...e.dataTransfer.files];
        const namedFiles = [];
        const folderPaths = new Set();
        const folderReads = [];

        const collect = (entry, path) => {
            const fullPath = path ? path + '/' + entry.name : entry.name;
            if (entry.isFile) {
                // getAsFile() only works for top-level entries; children must use entry.file().
                const snapshot = snapshots.find(item => item.entry === entry && item.file);
                namedFiles.push(DroppedFiles._withPath(snapshot ? snapshot.file : entry, fullPath));
                return;
            }
            if (!entry.isDirectory || !entry.createReader) return;
            folderPaths.add(fullPath + '/');
            const reader = entry.createReader();
            const children = [];
            folderReads.push(new Promise(resolve => {
                const readBatch = () => reader.readEntries(batch => {
                    if (!batch.length) {
                        children.forEach(child => collect(child, fullPath));
                        resolve();
                        return;
                    }
                    children.push(...batch);
                    readBatch(); // readEntries returns at most 100 entries per call
                }, error => {
                    console.error('Cannot read dropped folder:', error);
                    resolve();
                });
                readBatch();
            }));
        };

        snapshots.forEach(item => item.entry && collect(item.entry, ''));
        await Promise.all(folderReads);
        if (namedFiles.length) {
            const files = (await Promise.all(namedFiles)).filter(Boolean);
            if (files.length) return files.sort((a, b) => a.name.localeCompare(b.name));
        }
        // An empty folder is dropped: represent it as a zero-byte .keep placeholder so the directory survives.
        if (folderPaths.size) {
            return [...folderPaths].sort().map(path => new File([''], path + '.keep', {type: 'text/plain'}));
        }
        // Safari / older browsers without the entry API: fall back to the flat FileList.
        const fallback = flatFiles.length ? flatFiles : snapshots.map(item => item.file).filter(Boolean);
        return fallback;
    }

    static async _withPath(fileOrEntry, path) {
        let file = fileOrEntry;
        if (fileOrEntry.isFile) {
            file = await new Promise(resolve => fileOrEntry.file(resolve, error => {
                console.error('Cannot read dropped file:', error);
                resolve(null);
            }));
        }
        if (!file) return null;
        // Preserve folder hierarchy in the name. File.name is immutable, so wrap nested files.
        if (file.name === path || path.indexOf('/') < 0) return file;
        return new File([file], path, {type: file.type, lastModified: file.lastModified});
    }
}

class PeersUI {

    constructor() {
        window.transferStatusDialog = new TransferStatusDialog(); // patched: live transfer status""")
edit("ui.js",
     """    _onDrop(e) {
        if (this.shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();

        this._onDragEnd();

        if ($$('x-peer') && $$('x-peer').contains(e.target)) return; // dropped on peer

        let files = e.dataTransfer.files;
        let text = e.dataTransfer.getData("text");

        // convert FileList to Array
        files = [...files];

        if (files.length > 0) {
            Events.fire('activate-share-mode', {
                files: files
            });
        }
        else if(text.length > 0) {
            Events.fire('activate-share-mode', {
                text: text
            });
        }
    }""",
     """    async _onDrop(e) {
        if (this.shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();

        this._onDragEnd();

        if ($$('x-peer') && $$('x-peer').contains(e.target)) return; // dropped on peer

        const text = e.dataTransfer.getData("text");
        const files = await DroppedFiles.fromEvent(e); // patched: includes whole folders

        if (files.length > 0) {
            Events.fire('activate-share-mode', {
                files: files
            });
        }
        else if(text.length > 0) {
            Events.fire('activate-share-mode', {
                text: text
            });
        }
    }""")
edit("ui.js",
     """    _onDrop(e) {
        if (this._shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();

        this._onDragEnd();

        const peerId = this._peer.id;
        const files = e.dataTransfer.files;
        const text = e.dataTransfer.getData("text");

        if (files.length > 0) {
            Events.fire('files-selected', {
                files: files,
                to: peerId
            });
        }
        else if (text.length > 0) {
            Events.fire('send-text', {
                text: text,
                to: peerId
            });
        }
    }""",
     """    async _onDrop(e) {
        if (this._shareMode.active || Dialog.anyDialogShown()) return;

        e.preventDefault();
        e.stopPropagation(); // patched: don't also activate the page-wide share mode

        this._onDragEnd();

        const peerId = this._peer.id;
        const text = e.dataTransfer.getData("text");
        const files = await DroppedFiles.fromEvent(e); // patched: includes whole folders

        if (files.length > 0) {
            Events.fire('files-selected', {
                files: files,
                to: peerId
            });
        }
        else if (text.length > 0) {
            Events.fire('send-text', {
                text: text,
                to: peerId
            });
        }
    }""")

# 17b. Pass the peer's WebRTC channel into its chunker so the send queue can be bounded.
edit("network.js",
     """        this._chunker = new FileChunker(file,
            chunk => this._send(chunk),
            offset => this._onPartitionEnd(offset),
            this instanceof RTCPeer);""",
     """        this._chunker = new FileChunker(file,
            chunk => this._send(chunk),
            offset => this._onPartitionEnd(offset),
            this instanceof RTCPeer);
        if (this instanceof RTCPeer) this._chunker._channel = this._channel; // patched""")

# 18. Transfer status dialog: live progress / rate / ETA / protocol, and a working cancel button.
edit("ui.js",
     """class Dialog {""",
     """// patched: live transfer status with a cancel action that stops both peers cleanly
class TransferStatusDialog {
    constructor() {
        this.$el = $('transfer-status-dialog');
        this.$heading = this.$el.querySelector('.transfer-status-heading');
        this.$percent = this.$el.querySelector('.transfer-status-percent');
        this.$bar = this.$el.querySelector('.transfer-status-bar');
        this.$detail = this.$el.querySelector('.transfer-status-detail');
        this.$meta = this.$el.querySelector('.transfer-status-meta');
        this.samples = [];
        this.active = false;

        this.$el.querySelector('#cancel-transfer').addEventListener('click', () => this.cancel());
        Events.on('transfer-state', e => this._onState(e.detail));
        Events.on('transfer-progress', e => this._onProgress(e.detail));
    }

    static bytes(n) {
        if (!isFinite(n) || n < 0) n = 0;
        if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
        if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
        if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
        return Math.round(n) + ' B';
    }

    static duration(s) {
        if (!isFinite(s) || s < 0) return '--:--';
        s = Math.round(s);
        const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = String(s % 60).padStart(2, '0');
        return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
    }

    _onState(detail) {
        if (detail.state === 'start') {
            this.active = true;
            this.detail = detail;
            this.samples = [];
            this.$heading.textContent = detail.dir === 'send' ? '正在发送文件' : '正在接收文件';
            this.$percent.textContent = '0%';
            this.$bar.style.width = '0%';
            this.$detail.textContent = `0 B / ${TransferStatusDialog.bytes(detail.totalSize)} · 测速中…`;
            this.$meta.textContent = detail.protocol === 'ws' ? '服务器中转' : 'P2P 直连';
            this.$el.setAttribute('show', true);
        }
        else if (this.active) {
            this.active = false;
            this.$el.removeAttribute('show');
        }
    }

    _onProgress(detail) {
        if (!this.active || !this.detail) return;
        const progress = Math.max(0, Math.min(1, detail.progress));
        const total = this.detail.totalSize;
        const now = performance.now();
        this.samples.push({t: now, bytes: progress * total});
        while (this.samples.length > 2 && now - this.samples[0].t > 5000) this.samples.shift();
        let speedText = '测速中…', etaText = '--:--';
        const first = this.samples[0], last = this.samples[this.samples.length - 1];
        if (first && last && last.t - first.t >= 700 && last.bytes > first.bytes) {
            const speed = (last.bytes - first.bytes) / ((last.t - first.t) / 1000);
            speedText = TransferStatusDialog.bytes(speed) + '/s';
            etaText = TransferStatusDialog.duration((total - last.bytes) / speed);
        }
        this.$percent.textContent = Math.round(progress * 100) + '%';
        this.$bar.style.width = (progress * 100) + '%';
        this.$detail.textContent = `${TransferStatusDialog.bytes(progress * total)} / ${TransferStatusDialog.bytes(total)} · ${speedText}`;
        this.$meta.textContent = `${detail.protocol === 'ws' ? '服务器中转' : 'P2P 直连'} · 预计剩余 ${etaText}`;
    }

    cancel() {
        if (!this.active || !this.detail) return;
        Events.fire('cancel-transfer', {peerId: this.detail.peerId});
        this.active = false;
        this.$el.removeAttribute('show');
        Events.fire('notify-user', '已终止传输');
    }
}

class Dialog {""")

# 18a. network.js: publish state/progress events, handle cancel, and relay cancellation to the other peer.
edit("ws-server.js",
     """            case 'speedtest': // patched: network speed test control messages""",
     """            case 'speedtest': // patched: network speed test control messages
            case 'transfer-cancel': // patched: cancel a relayed file transfer""")
edit("network.js",
     """            case 'speedtest': // patched: network speed test control messages""",
     """            case 'speedtest': // patched: network speed test control messages
            case 'transfer-cancel': // patched: cancel a relayed file transfer""")
edit("network.js",
     """            case 'message-transfer-complete':
                this._onMessageTransferCompleted();
                break;""",
     """            case 'message-transfer-complete':
                this._onMessageTransferCompleted();
                break;
            case 'transfer-cancel': // patched: the other side stopped the transfer
                this._cancelTransfer(false);
                break;""")
edit("network.js",
     """    _onMessage(message) {
        if (this._speedtestHandle(message)) return; // patched""",
     """    _onMessage(message) {
        // Cancellation must bypass the late-frame guard: a queued data-channel cancel can arrive
        // after the local side has already stopped, and the WebSocket control path is out-of-band.
        if (typeof message === 'string' && message.indexOf('transfer-cancel') !== -1) {
            try {
                if (JSON.parse(message).type === 'transfer-cancel') {
                    this._cancelTransfer(false);
                    return;
                }
            }
            catch (e) {}
        }
        if (this._cancelled) return; // patched: ignore late chunks / acks after cancellation
        if (this._speedtestHandle(message)) return; // patched""")
edit("network.js",
     """    _pdXferStart(dir, files, size) {
        this._pdXfer = {t0: Date.now(), dir: dir, files: files, size: size};
        this._pdLastProgress = 0;
        PdLog.add('xfer-start', {peer: this._pdShort(), proto: this._transferProtocol(), dir: dir, files: files, size: size});
    }""",
     """    _pdXferStart(dir, files, size) {
        this._pdXfer = {t0: Date.now(), dir: dir, files: files, size: size};
        this._pdLastProgress = 0;
        this._cancelled = false; // patched
        Events.fire('transfer-state', {state: 'start', peerId: this._peerId, dir: dir, protocol: this._transferProtocol(), totalSize: size, files: files}); // patched
        PdLog.add('xfer-start', {peer: this._pdShort(), proto: this._transferProtocol(), dir: dir, files: files, size: size});
    }""")
edit("network.js",
     """    _pdXferEnd(ev) {
        const x = this._pdXfer;
        if (!x) return;
        this._pdXfer = null;""",
     """    _pdXferEnd(ev) {
        const x = this._pdXfer;
        if (!x) return;
        this._pdXfer = null;
        Events.fire('transfer-state', {state: ev === 'xfer-done' ? 'done' : 'abort', peerId: this._peerId}); // patched""")
edit("network.js",
     """    _onDownloadProgress(progress, totalSize) {
        this._pdLastProgress = progress; // patched: diagnostics""",
     """    _onDownloadProgress(progress, totalSize) {
        this._pdLastProgress = progress; // patched: diagnostics
        Events.fire('transfer-progress', {peerId: this._peerId, progress: progress, protocol: this._transferProtocol()}); // patched""")
edit("network.js",
     """    _abortTransfer() {""",
     """    // patched: stop the local chunker and tell the other side to stop too
    _cancelTransfer(notifyPeer = true) {
        if (!this._pdXfer) return;
        if (notifyPeer) {
            // RTC data channels are ordered behind queued file chunks; use the existing WebSocket
            // signaling path for an out-of-band cancel so the sender stops immediately.
            if (this instanceof RTCPeer && this._server && this._server.send) {
                const roomType = this._getRoomTypes()[0];
                this._server.send({type: 'transfer-cancel', to: this._peerId, roomType: roomType, roomId: this._roomIds[roomType]});
            }
            else {
                this.sendJSON({type: 'transfer-cancel'});
            }
        }
        if (this._chunker) {
            if (this._chunker.stop) this._chunker.stop();
            else if (this._chunker._reader) {
                try { this._chunker._reader.abort(); } catch (e) {}
            }
        }
        this._chunker = null;
        this._filesQueue = [];
        this._filesRequested = null;
        this._requestAccepted = null;
        this._requestPending = null;
        this._digester = null;
        this._filesReceived = [];
        this._busy = false;
        this._cancelled = true;
        this._pdXferEnd('xfer-cancel');
        Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});
        setTimeout(() => { this._cancelled = false; }, 3000); // allow fresh transfers after late frames drain
    }

    _abortTransfer() {""")
edit("network.js",
     """        Events.on('speedtest-request', e => this._onSpeedtestRequest(e.detail.peerId)); // patched""",
     """        Events.on('speedtest-request', e => this._onSpeedtestRequest(e.detail.peerId)); // patched
        Events.on('cancel-transfer', e => { // patched: user pressed cancel in the dialog
            const peer = this.peers[e.detail.peerId];
            if (peer) peer._cancelTransfer(true);
        });""")

_bump_cache_version()
print("patched ws-server.js, peer.js, network.js, util.js, ui.js, ui-main.js, index.html, localization.js, zh-CN.json, service-worker.js")
