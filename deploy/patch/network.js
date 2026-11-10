// patched: connection diagnostics. Events are queued (kept in sessionStorage across reloads) and sent to the server
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
        Events.on('pagehide', _ => this._disconnect());
        Events.on(window.visibilityChangeEvent, _ => this._onVisibilityChange());

        if (navigator.connection) {
            // patched: only (re)connect when not connected -- 'change' also fires when the bandwidth estimate moves
            navigator.connection.addEventListener('change', _ => this._connect());
        }
        // patched: the server pings every second, so 20s without any message means the socket is dead
        setInterval(() => this._checkAlive(), 5000);

        Events.on('room-secrets', e => this.send({ type: 'room-secrets', roomSecrets: e.detail }));
        Events.on('join-ip-room', _ => this.send({ type: 'join-ip-room'}));
        Events.on('room-secrets-deleted', e => this.send({ type: 'room-secrets-deleted', roomSecrets: e.detail}));
        Events.on('regenerate-room-secret', e => this.send({ type: 'regenerate-room-secret', roomSecret: e.detail}));
        Events.on('pair-device-initiate', _ => this._onPairDeviceInitiate());
        Events.on('pair-device-join', e => this._onPairDeviceJoin(e.detail));
        Events.on('pair-device-cancel', _ => this.send({ type: 'pair-device-cancel' }));

        Events.on('create-public-room', _ => this._onCreatePublicRoom());
        Events.on('join-public-room', e => this._onJoinPublicRoom(e.detail.roomId, e.detail.createIfInvalid));
        Events.on('leave-public-room', _ => this._onLeavePublicRoom());

        Events.on('offline', _ => clearTimeout(this._reconnectTimer));
        Events.on('online', _ => this._connect());

        this._getConfig().then(() => this._connect());
    }

    _getConfig() {
        console.log("Loading config...")
        return new Promise((resolve, reject) => {
            let xhr = new XMLHttpRequest();
            xhr.addEventListener("load", () => {
                if (xhr.status === 200) {
                    // Config received
                    let config = JSON.parse(xhr.responseText);
                    console.log("Config loaded:", config)
                    this._config = config;
                    Events.fire('config', config);
                    resolve()
                } else if (xhr.status < 200 || xhr.status >= 300) {
                    retry(xhr);
                }
            })

            xhr.addEventListener("error", _ => {
                retry(xhr);
            });

            function retry(request) {
                setTimeout(function () {
                    openAndSend(request)
                }, 1000)
            }

            function openAndSend() {
                xhr.open('GET', 'config');
                xhr.send();
            }

            openAndSend(xhr);
        })
    }

    _setWsConfig(wsConfig) {
        this._wsConfig = wsConfig;
        Events.fire('ws-config', wsConfig);
    }

    _connect() {
        clearTimeout(this._reconnectTimer);
        if (this._isConnected() || this._isConnecting() || this._isOffline()) return;
        if (this._isReconnect) {
            Events.fire('notify-user', {
                message: Localization.getTranslation("notifications.connecting"),
                persistent: true
            });
        }
        const ws = new WebSocket(this._endpoint());
        ws.binaryType = 'arraybuffer';
        ws._pdT0 = Date.now(); // patched: diagnostics
        this._pdTries = (this._pdTries || 0) + 1;
        ws.onopen = _ => { ws._pdOpenAt = Date.now(); this._onOpen(); };
        ws.onmessage = e => this._onMessage(e.data);
        ws.onclose = e => { this._pdLogClose(ws, e); this._onDisconnect(); }; // patched: log why it closed
        ws.onerror = e => this._onError(e);
        this._socket = ws;
    }

    _onOpen() {
        this._lastMessageAt = Date.now(); // patched
        const ws = this._socket; // patched: diagnostics
        PdLog.add('ws-open', Object.assign({
            connect_ms: ws && ws._pdOpenAt ? ws._pdOpenAt - ws._pdT0 : undefined,
            down_ms: this._pdDownAt ? Date.now() - this._pdDownAt : undefined,
            tries: this._pdTries
        }, PdLog.env()));
        this._pdDownAt = 0;
        this._pdTries = 0;
        console.log('WS: server connected');
        Events.fire('ws-connected');
        if (this._isReconnect) Events.fire('notify-user', Localization.getTranslation("notifications.connected"));
    }

    _onPairDeviceInitiate() {
        if (!this._isConnected()) {
            Events.fire('notify-user', Localization.getTranslation("notifications.online-requirement-pairing"));
            return;
        }
        this.send({ type: 'pair-device-initiate' });
    }

    _onPairDeviceJoin(pairKey) {
        if (!this._isConnected()) {
            setTimeout(() => this._onPairDeviceJoin(pairKey), 1000);
            return;
        }
        this.send({ type: 'pair-device-join', pairKey: pairKey });
    }

    _onCreatePublicRoom() {
        if (!this._isConnected()) {
            Events.fire('notify-user', Localization.getTranslation("notifications.online-requirement-public-room"));
            return;
        }
        this.send({ type: 'create-public-room' });
    }

    _onJoinPublicRoom(roomId, createIfInvalid) {
        if (!this._isConnected()) {
            setTimeout(() => this._onJoinPublicRoom(roomId), 1000);
            return;
        }
        this.send({ type: 'join-public-room', publicRoomId: roomId, createIfInvalid: createIfInvalid });
    }

    _onLeavePublicRoom() {
        if (!this._isConnected()) {
            setTimeout(() => this._onLeavePublicRoom(), 1000);
            return;
        }
        this.send({ type: 'leave-public-room' });
    }

    _onMessage(msg) {
        this._lastMessageAt = Date.now(); // patched: liveness for _checkAlive()
        if (msg instanceof ArrayBuffer) {
            // patched: binary ws-fallback chunk = 36-byte sender id + raw bytes
            Events.fire('ws-relay-binary', {
                senderId: new TextDecoder().decode(new Uint8Array(msg, 0, 36)),
                chunk: msg.slice(36)
            });
            return;
        }
        msg = JSON.parse(msg);
        if (msg.type !== 'ping') console.log('WS receive:', msg);
        switch (msg.type) {
            case 'ws-config':
                this._setWsConfig(msg.wsConfig);
                break;
            case 'peers':
                this._onPeers(msg);
                break;
            case 'peer-joined':
                Events.fire('peer-joined', msg);
                break;
            case 'peer-left':
                Events.fire('peer-left', msg);
                break;
            case 'signal':
                Events.fire('signal', msg);
                break;
            case 'ping':
                this.send({ type: 'pong' });
                break;
            case 'display-name':
                this._onDisplayName(msg);
                break;
            case 'pair-device-initiated':
                Events.fire('pair-device-initiated', msg);
                break;
            case 'pair-device-joined':
                Events.fire('pair-device-joined', msg);
                break;
            case 'pair-device-join-key-invalid':
                Events.fire('pair-device-join-key-invalid');
                break;
            case 'pair-device-canceled':
                Events.fire('pair-device-canceled', msg.pairKey);
                break;
            case 'join-key-rate-limit':
                Events.fire('notify-user', Localization.getTranslation("notifications.rate-limit-join-key"));
                break;
            case 'secret-room-deleted':
                Events.fire('secret-room-deleted', msg.roomSecret);
                break;
            case 'room-secret-regenerated':
                Events.fire('room-secret-regenerated', msg);
                break;
            case 'public-room-id-invalid':
                Events.fire('public-room-id-invalid', msg.publicRoomId);
                break;
            case 'public-room-created':
                Events.fire('public-room-created', msg.roomId);
                break;
            case 'public-room-left':
                Events.fire('public-room-left');
                break;
            case 'request':
            case 'header':
            case 'partition':
            case 'partition-received':
            case 'progress':
            case 'files-transfer-response':
            case 'file-transfer-complete':
            case 'message-transfer-complete':
            case 'text':
            case 'display-name-changed':
            case 'ws-chunk':
            case 'speedtest': // patched: network speed test control messages
                // ws-fallback
                if (this._wsConfig.wsFallback) {
                    Events.fire('ws-relay', JSON.stringify(msg));
                }
                else {
                    console.log("WS receive: message type is for websocket fallback only but websocket fallback is not activated on this instance.")
                }
                break;
            default:
                console.error('WS receive: unknown message type', msg);
        }
    }

    send(msg) {
        if (!this._isConnected()) return;
        if (msg.type !== 'pong') console.log("WS send:", msg)
        this._socket.send(JSON.stringify(msg));
    }

    // patched: binary ws-fallback chunk = 36-byte recipient id + raw bytes (no base64, no JSON)
    sendBinary(to, chunk) {
        if (!this._isConnected()) return;
        const frame = new Uint8Array(36 + chunk.byteLength);
        frame.set(new TextEncoder().encode(to), 0);
        frame.set(new Uint8Array(chunk), 36);
        this._socket.send(frame);
    }

    _onPeers(msg) {
        Events.fire('peers', msg);
    }

    _onDisplayName(msg) {
        // Add peerId and peerIdHash to sessionStorage to authenticate as the same device on page reload
        sessionStorage.setItem('peer_id', msg.peerId);
        sessionStorage.setItem('peer_id_hash', msg.peerIdHash);

        // Add peerId to localStorage to mark it for other PairDrop tabs on the same browser
        BrowserTabsConnector
            .addPeerIdToLocalStorage()
            .then(peerId => {
                if (!peerId) return;
                console.log("successfully added peerId to localStorage");

                // Only now join rooms
                Events.fire('join-ip-room');
                PersistentStorage.getAllRoomSecrets()
                    .then(roomSecrets => {
                        Events.fire('room-secrets', roomSecrets);
                    });
            });

        Events.fire('display-name', msg);
    }

    _endpoint() {
        const protocol = location.protocol.startsWith('https') ? 'wss' : 'ws';
        // Check whether the instance specifies another signaling server otherwise use the current instance for signaling
        let wsServerDomain = this._config.signalingServer
            ? this._config.signalingServer
            : location.host + location.pathname;

        let wsUrl = new URL(protocol + '://' + wsServerDomain + 'server');

        wsUrl.searchParams.append('webrtc_supported', window.isRtcSupported ? 'true' : 'false');
        wsUrl.searchParams.append('ws_binary', '1'); // patched: we understand binary ws-fallback chunks

        const peerId = sessionStorage.getItem('peer_id');
        const peerIdHash = sessionStorage.getItem('peer_id_hash');
        if (peerId && peerIdHash) {
            wsUrl.searchParams.append('peer_id', peerId);
            wsUrl.searchParams.append('peer_id_hash', peerIdHash);
        }

        return wsUrl.toString();
    }

    _disconnect() {
        PdLog.add('ws-disconnect', Object.assign({why: 'pagehide', busy: PdLog.busy()}, PdLog.env())); // patched
        this.send({ type: 'disconnect' });

        const peerId = sessionStorage.getItem('peer_id');
        BrowserTabsConnector
            .removePeerIdFromLocalStorage(peerId)
            .then(_ => {
                console.log("successfully removed peerId from localStorage");
            });

        if (!this._socket) return;

        this._socket.onclose = null;
        this._socket.close(4000, 'pagehide'); // patched: tells the server log why
        this._socket = null;
        Events.fire('ws-disconnected');
        this._isReconnect = true;
    }

    _onDisconnect() {
        console.log('WS: server disconnected');
        setTimeout(() => {
            this._isReconnect = true;
            Events.fire('ws-disconnected');
            this._reconnectTimer = setTimeout(() => this._connect(), 1000);
        }, 100); //delay for 100ms to prevent flickering on page reload
    }

    _onVisibilityChange() {
        if (window.hiddenProperty) return;
        this._connect();
    }

    _isConnected() {
        return this._socket && this._socket.readyState === this._socket.OPEN;
    }

    _isConnecting() {
        return this._socket && this._socket.readyState === this._socket.CONNECTING;
    }

    _isOffline() {
        return !navigator.onLine;
    }

    _onError(e) {
        console.error(e);
    }

    // patched: drop a silent socket without the 'disconnect' message (it would only queue behind unsent data)
    _checkAlive() {
        if (!this._isConnected() || Date.now() - this._lastMessageAt < 20000) return;
        console.log('WS: no message from server for 20s, reconnecting');
        // patched: diagnostics; code 4001 tells the server log why (if the close frame still gets through)
        PdLog.add('ws-silent', Object.assign({
            silent_ms: Date.now() - this._lastMessageAt,
            up_ms: this._socket._pdOpenAt ? Date.now() - this._socket._pdOpenAt : undefined,
            unsent: this._socket.bufferedAmount,
            busy: PdLog.busy()
        }, PdLog.env()));
        this._pdDownAt = Date.now();
        this._socket.onclose = null;
        this._socket.close(4001, 'no message for 20s');
        this._socket = null;
        Events.fire('ws-disconnected');
        this._isReconnect = true;
        this._connect();
    }

    _reconnect() {
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
    }
}

class Peer {

    constructor(serverConnection, isCaller, peerId, roomType, roomId) {
        this._server = serverConnection;
        this._isCaller = isCaller;
        this._peerId = peerId;

        this._roomIds = {};
        this._updateRoomIds(roomType, roomId);

        this._filesQueue = [];
        this._busy = false;

        // evaluate auto accept
        this._evaluateAutoAccept();
    }

    sendJSON(message) {
        this._send(JSON.stringify(message));
    }

    // Is overwritten in expanding classes
    _send(message) {}

    sendDisplayName(displayName) {
        this.sendJSON({type: 'display-name-changed', displayName: displayName});
    }

    _isSameBrowser() {
        return BrowserTabsConnector.peerIsSameBrowser(this._peerId);
    }

    _isPaired() {
        return !!this._roomIds['secret'];
    }

    _getPairSecret() {
        return this._roomIds['secret'];
    }

    _regenerationOfPairSecretNeeded() {
        return this._getPairSecret() && this._getPairSecret().length !== 256
    }

    _getRoomTypes() {
        return Object.keys(this._roomIds);
    }

    _updateRoomIds(roomType, roomId) {
        const roomTypeIsSecret = roomType === "secret";
        const roomIdIsNotPairSecret = this._getPairSecret() !== roomId;

        // if peer is another browser tab, peer is not identifiable with roomSecret as browser tabs share all roomSecrets
        // -> do not delete duplicates and do not regenerate room secrets
        if (!this._isSameBrowser()
            && roomTypeIsSecret
            && this._isPaired()
            && roomIdIsNotPairSecret) {
            // multiple roomSecrets with same peer -> delete old roomSecret
            PersistentStorage
                .deleteRoomSecret(this._getPairSecret())
                .then(deletedRoomSecret => {
                    if (deletedRoomSecret) console.log("Successfully deleted duplicate room secret with same peer: ", deletedRoomSecret);
                });
        }

        this._roomIds[roomType] = roomId;

        if (!this._isSameBrowser()
            &&  roomTypeIsSecret
            &&  this._isPaired()
            &&  this._regenerationOfPairSecretNeeded()
            &&  this._isCaller) {
            // increase security by initiating the increase of the roomSecret length
            // from 64 chars (<v1.7.0) to 256 chars (v1.7.0+)
            console.log('RoomSecret is regenerated to increase security')
            Events.fire('regenerate-room-secret', this._getPairSecret());
        }
    }

    _removeRoomType(roomType) {
        delete this._roomIds[roomType];

        Events.fire('room-type-removed', {
            peerId: this._peerId,
            roomType: roomType
        });
    }

    _evaluateAutoAccept() {
        if (!this._isPaired()) {
            this._setAutoAccept(false);
            return;
        }

        PersistentStorage
            .getRoomSecretEntry(this._getPairSecret())
            .then(roomSecretEntry => {
                const autoAccept = roomSecretEntry
                    ? roomSecretEntry.entry.auto_accept
                    : false;
                this._setAutoAccept(autoAccept);
            })
            .catch(_ => {
                this._setAutoAccept(false);
            });
    }

    _setAutoAccept(autoAccept) {
        this._autoAccept = !this._isSameBrowser()
            ? autoAccept
            : false;
    }

    async requestFileTransfer(files) {
        let header = [];
        let totalSize = 0;
        let imagesOnly = true
        for (let i=0; i<files.length; i++) {
            Events.fire('set-progress', {peerId: this._peerId, progress: 0.8*i/files.length, status: 'prepare'})
            header.push({
                name: files[i].name,
                mime: files[i].type,
                size: files[i].size
            });
            totalSize += files[i].size;
            if (files[i].type.split('/')[0] !== 'image') imagesOnly = false;
        }

        Events.fire('set-progress', {peerId: this._peerId, progress: 0.8, status: 'prepare'})

        let dataUrl = '';
        if (files[0].type.split('/')[0] === 'image') {
            try {
                dataUrl = await getThumbnailAsDataUrl(files[0], 400, null, 0.9);
            } catch (e) {
                console.error(e);
            }
        }

        Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'prepare'})

        this._filesRequested = files;
        this._outgoingTotalSize = totalSize; // patched: for speed / ETA on the sender side

        this.sendJSON({type: 'request',
            header: header,
            totalSize: totalSize,
            imagesOnly: imagesOnly,
            thumbnailDataUrl: dataUrl
        });
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'wait'})
    }

    async sendFiles() {
        for (let i=0; i<this._filesRequested.length; i++) {
            this._filesQueue.push(this._filesRequested[i]);
        }
        this._filesRequested = null
        if (this._busy) return;
        this._dequeueFile();
    }

    _dequeueFile() {
        this._busy = true;
        const file = this._filesQueue.shift();
        this._sendFile(file);
    }

    async _sendFile(file) {
        this.sendJSON({
            type: 'header',
            size: file.size,
            name: file.name,
            mime: file.type
        });
        this._chunker = new FileChunker(file,
            chunk => this._send(chunk),
            offset => this._onPartitionEnd(offset));
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
        return 1; // upstream behaviour (stop-and-wait); WSPeer overrides
    }

    _onReceivedPartitionEnd(offset) {
        this.sendJSON({ type: 'partition-received', offset: offset });
    }

    _sendNextPartition() {
        // patched: an ack frees one slot; the chunker is idle only if the window was full
        const windowWasFull = this._partitionsInFlight >= this._partitionWindow();
        this._partitionsInFlight = Math.max(0, this._partitionsInFlight - 1);
        if (!this._chunker || this._chunker.isFileEnd() || !windowWasFull) return;
        this._chunker.nextPartition();
    }

    _sendProgress(progress) {
        this.sendJSON({ type: 'progress', progress: progress });
    }

    _onMessage(message) {
        if (this._speedtestHandle(message)) return; // patched
        if (typeof message !== 'string') {
            this._onChunkReceived(message);
            return;
        }
        const messageJSON = JSON.parse(message);
        switch (messageJSON.type) {
            case 'request':
                this._onFilesTransferRequest(messageJSON);
                break;
            case 'header':
                this._onFileHeader(messageJSON);
                break;
            case 'partition':
                this._onReceivedPartitionEnd(messageJSON);
                break;
            case 'partition-received':
                this._sendNextPartition();
                break;
            case 'progress':
                this._onDownloadProgress(messageJSON.progress, this._outgoingTotalSize);
                break;
            case 'files-transfer-response':
                this._onFileTransferRequestResponded(messageJSON);
                break;
            case 'file-transfer-complete':
                this._onFileTransferCompleted();
                break;
            case 'message-transfer-complete':
                this._onMessageTransferCompleted();
                break;
            case 'text':
                this._onTextReceived(messageJSON);
                break;
            case 'display-name-changed':
                this._onDisplayNameChanged(messageJSON);
                break;
        }
    }

    // patched: speed test. Control messages are JSON {type: 'speedtest', phase, id}; test data are plain binary chunks.
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

    _onFilesTransferRequest(request) {
        if (this._requestPending) {
            // Only accept one request at a time per peer
            this.sendJSON({type: 'files-transfer-response', accepted: false});
            return;
        }
        if (window.iOS && request.totalSize >= 200*1024*1024) {
            // iOS Safari can only put 400MB at once to memory.
            // Request to send them in chunks of 200MB instead:
            this.sendJSON({type: 'files-transfer-response', accepted: false, reason: 'ios-memory-limit'});
            return;
        }

        this._requestPending = request;

        if (this._autoAccept) {
            // auto accept if set via Edit Paired Devices Dialog
            this._respondToFileTransferRequest(true);
            return;
        }

        // default behavior: show user transfer request
        Events.fire('files-transfer-request', {
            request: request,
            peerId: this._peerId
        });
    }

    _respondToFileTransferRequest(accepted) {
        this.sendJSON({type: 'files-transfer-response', accepted: accepted});
        if (accepted) {
            this._pdXferStart('recv', this._requestPending.header ? this._requestPending.header.length : 0, this._requestPending.totalSize); // patched
            this._requestAccepted = this._requestPending;
            this._totalBytesReceived = 0;
            this._busy = true;
            this._filesReceived = [];
        }
        this._requestPending = null;
    }

    _onFileHeader(header) {
        if (this._requestAccepted && this._requestAccepted.header.length) {
            this._lastProgress = 0;
            this._digester = new FileDigester({size: header.size, name: header.name, mime: header.mime},
                this._requestAccepted.totalSize,
                this._totalBytesReceived,
                fileBlob => this._onFileReceived(fileBlob)
            );
        }
    }

    _abortTransfer() {
        Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});
        Events.fire('notify-user', Localization.getTranslation("notifications.files-incorrect"));
        this._filesReceived = [];
        this._requestAccepted = null;
        this._digester = null;
        throw new Error("Received files differ from requested files. Abort!");
    }

    _onChunkReceived(chunk) {
        if(!this._digester || !(chunk.byteLength || chunk.size)) return;

        this._digester.unchunk(chunk);
        const progress = this._digester.progress;

        if (progress > 1) {
            this._abortTransfer();
        }

        this._onDownloadProgress(progress, this._requestAccepted && this._requestAccepted.totalSize);

        // occasionally notify sender about our progress
        if (progress - this._lastProgress < 0.005 && progress !== 1) return;
        this._lastProgress = progress;
        this._sendProgress(progress);
    }

    _onDownloadProgress(progress, totalSize) {
        this._pdLastProgress = progress; // patched: diagnostics
        // patched: totalSize + protocol let PeerUI show speed and ETA
        Events.fire('set-progress', {peerId: this._peerId, progress: progress, status: 'transfer', totalSize: totalSize, protocol: this._transferProtocol()});
    }

    _transferProtocol() {
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
    }

    async _onFileReceived(fileBlob) {
        const acceptedHeader = this._requestAccepted.header.shift();
        this._totalBytesReceived += fileBlob.size;

        this.sendJSON({type: 'file-transfer-complete'});

        const sameSize = fileBlob.size === acceptedHeader.size;
        const sameName = fileBlob.name === acceptedHeader.name
        if (!sameSize || !sameName) {
            this._abortTransfer();
        }

        // include for compatibility with 'Snapdrop & PairDrop for Android' app
        Events.fire('file-received', fileBlob);

        this._filesReceived.push(fileBlob);
        if (!this._requestAccepted.header.length) {
            this._busy = false;
            this._pdXferEnd('xfer-done'); // patched
            Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'process'});
            Events.fire('files-received', {peerId: this._peerId, files: this._filesReceived, imagesOnly: this._requestAccepted.imagesOnly, totalSize: this._requestAccepted.totalSize});
            this._filesReceived = [];
            this._requestAccepted = null;
        }
    }

    _onFileTransferCompleted() {
        this._chunker = null;
        if (!this._filesQueue.length) {
            this._busy = false;
            this._pdXferEnd('xfer-done'); // patched
            Events.fire('notify-user', Localization.getTranslation("notifications.file-transfer-completed"));
            Events.fire('files-sent'); // used by 'Snapdrop & PairDrop for Android' app
        }
        else {
            this._dequeueFile();
        }
    }

    _onFileTransferRequestResponded(message) {
        if (!message.accepted) {
            PdLog.add('xfer-rejected', {peer: this._pdShort(), proto: this._transferProtocol(), reason: message.reason}); // patched
            Events.fire('set-progress', {peerId: this._peerId, progress: 1, status: 'wait'});
            this._filesRequested = null;
            if (message.reason === 'ios-memory-limit') {
                Events.fire('notify-user', Localization.getTranslation("notifications.ios-memory-limit"));
            }
            return;
        }
        Events.fire('file-transfer-accepted');
        this._pdXferStart('send', this._filesRequested ? this._filesRequested.length : 0, this._outgoingTotalSize); // patched
        Events.fire('set-progress', {peerId: this._peerId, progress: 0, status: 'transfer', totalSize: this._outgoingTotalSize, protocol: this._transferProtocol()});
        this.sendFiles();
    }

    _onMessageTransferCompleted() {
        Events.fire('notify-user', Localization.getTranslation("notifications.message-transfer-completed"));
    }

    sendText(text) {
        const unescaped = btoa(unescape(encodeURIComponent(text)));
        this.sendJSON({ type: 'text', text: unescaped });
    }

    _onTextReceived(message) {
        if (!message.text) return;
        const escaped = decodeURIComponent(escape(atob(message.text)));
        Events.fire('text-received', { text: escaped, peerId: this._peerId });
        this.sendJSON({ type: 'message-transfer-complete' });
    }

    _onDisplayNameChanged(message) {
        const displayNameHasChanged = this._displayName !== message.displayName

        if (message.displayName && displayNameHasChanged) {
            this._displayName = message.displayName;
        }

        Events.fire('peer-display-name-changed', {peerId: this._peerId, displayName: message.displayName});

        if (!displayNameHasChanged) return;
        Events.fire('notify-peer-display-name-changed', this._peerId);
    }
}

class RTCPeer extends Peer {

    constructor(serverConnection, isCaller, peerId, roomType, roomId, rtcConfig) {
        super(serverConnection, isCaller, peerId, roomType, roomId);

        this.rtcSupported = true;
        this.rtcConfig = rtcConfig
        this._pdT0 = Date.now(); // patched: diagnostics

        if (!this._isCaller) return; // we will listen for a caller
        this._connect();
    }

    _connect() {
        if (!this._conn || this._conn.signalingState === "closed") this._openConnection();

        if (this._isCaller) {
            this._openChannel();
        }
        else {
            this._conn.ondatachannel = e => this._onChannelOpened(e);
        }
    }

    _openConnection() {
        this._conn = new RTCPeerConnection(this.rtcConfig);
        this._conn.onicecandidate = e => this._onIceCandidate(e);
        this._conn.onicecandidateerror = e => this._onError(e);
        this._conn.onconnectionstatechange = _ => this._onConnectionStateChange();
        this._conn.oniceconnectionstatechange = e => this._onIceConnectionStateChange(e);
    }

    _openChannel() {
        if (!this._conn) return;

        const channel = this._conn.createDataChannel('data-channel', {
            ordered: true,
            reliable: true // Obsolete. See https://developer.mozilla.org/en-US/docs/Web/API/RTCDataChannel/reliable
        });
        channel.onopen = e => this._onChannelOpened(e);
        channel.onerror = e => this._onError(e);

        this._conn
            .createOffer()
            .then(d => this._onDescription(d))
            .catch(e => this._onError(e));
    }

    _onDescription(description) {
        // description.sdp = description.sdp.replace('b=AS:30', 'b=AS:1638400');
        this._conn
            .setLocalDescription(description)
            .then(_ => this._sendSignal({ sdp: description }))
            .catch(e => this._onError(e));
    }

    _onIceCandidate(event) {
        if (!event.candidate) return;
        this._sendSignal({ ice: event.candidate });
    }

    onServerMessage(message) {
        if (!this._conn) this._connect();

        if (message.sdp) {
            this._conn
                .setRemoteDescription(message.sdp)
                .then(_ => {
                    if (message.sdp.type === 'offer') {
                        return this._conn
                            .createAnswer()
                            .then(d => this._onDescription(d));
                    }
                })
                .catch(e => this._onError(e));
        }
        else if (message.ice) {
            this._conn
                .addIceCandidate(new RTCIceCandidate(message.ice))
                .catch(e => this._onError(e));
        }
    }

    _onChannelOpened(event) {
        console.log('RTC: channel opened with', this._peerId);
        PdLog.add('rtc-open', {peer: this._pdShort(), ms: Date.now() - this._pdT0, caller: this._isCaller ? 1 : 0}); // patched
        this._pdLogPath();
        const channel = event.channel || event.target;
        channel.binaryType = 'arraybuffer';
        channel.onmessage = e => this._onMessage(e.data);
        channel.onclose = _ => this._onChannelClosed();
        this._channel = channel;
        Events.on('beforeunload', e => this._onBeforeUnload(e));
        Events.on('pagehide', _ => this._onPageHide());
        Events.fire('peer-connected', {peerId: this._peerId, connectionHash: this.getConnectionHash()});
    }

    _onMessage(message) {
        if (typeof message === 'string') {
            console.log('RTC:', JSON.parse(message));
        }
        super._onMessage(message);
    }

    getConnectionHash() {
        const localDescriptionLines = this._conn.localDescription.sdp.split("\r\n");
        const remoteDescriptionLines = this._conn.remoteDescription.sdp.split("\r\n");
        let localConnectionFingerprint, remoteConnectionFingerprint;
        for (let i=0; i<localDescriptionLines.length; i++) {
            if (localDescriptionLines[i].startsWith("a=fingerprint:")) {
                localConnectionFingerprint = localDescriptionLines[i].substring(14);
                break;
            }
        }
        for (let i=0; i<remoteDescriptionLines.length; i++) {
            if (remoteDescriptionLines[i].startsWith("a=fingerprint:")) {
                remoteConnectionFingerprint = remoteDescriptionLines[i].substring(14);
                break;
            }
        }
        const combinedFingerprints = this._isCaller
            ? localConnectionFingerprint + remoteConnectionFingerprint
            : remoteConnectionFingerprint + localConnectionFingerprint;
        let hash = cyrb53(combinedFingerprints).toString();
        while (hash.length < 16) {
            hash = "0" + hash;
        }
        return hash;
    }

    _onBeforeUnload(e) {
        if (this._busy) {
            e.preventDefault();
            return Localization.getTranslation("notifications.unfinished-transfers-warning");
        }
    }

    _onPageHide() {
        this._disconnect();
    }

    _disconnect() {
        if (this._conn && this._channel) {
            this._channel.onclose = null;
            this._channel.close();
        }
        Events.fire('peer-disconnected', this._peerId);
    }

    _onChannelClosed() {
        console.log('RTC: channel closed', this._peerId);
        PdLog.add('rtc-channel-closed', Object.assign({peer: this._pdShort(), busy: this._pdXfer ? this._pdXfer.dir : undefined}, PdLog.env())); // patched
        Events.fire('peer-disconnected', this._peerId);
        if (!this._isCaller) return;
        this._connect(); // reopen the channel
    }

    // patched: which candidate pair carries the data (host = same LAN, srflx = public address via STUN)
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
        }
        switch (this._conn.connectionState) {
            case 'disconnected':
                Events.fire('peer-disconnected', this._peerId);
                this._onError('rtc connection disconnected');
                break;
            case 'failed':
                Events.fire('peer-disconnected', this._peerId);
                this._onError('rtc connection failed');
                break;
        }
    }

    _onIceConnectionStateChange() {
        switch (this._conn.iceConnectionState) {
            case 'failed':
                PdLog.add('rtc-ice-failed', {peer: this._pdShort(), ms: Date.now() - this._pdT0}); // patched
                Events.fire('rtc-failed', {peerId: this._peerId}); // patched: PeersManager falls back to the relay
                this._onError('ICE Gathering failed');
                break;
            default:
                console.log('ICE Gathering', this._conn.iceConnectionState);
        }
    }

    _onError(error) {
        console.error(error);
        // patched: diagnostics (ICE candidate errors, e.g. an unreachable IPv6 STUN path, are routine and skipped;
        // data channel errors arrive as RTCErrorEvent with the RTCError in .error)
        const err = error && error.error instanceof Error ? error.error : error;
        if (typeof err === 'string' || err instanceof Error) {
            PdLog.add('rtc-error', {peer: this._pdShort(), err: String(err.message || err).slice(0, 120), busy: this._pdXfer ? this._pdXfer.dir : undefined});
        }
    }

    _send(message) {
        if (!this._channel) this.refresh();
        this._channel.send(message);
    }

    _sendSignal(signal) {
        signal.type = 'signal';
        signal.to = this._peerId;
        signal.roomType = this._getRoomTypes()[0];
        signal.roomId = this._roomIds[this._getRoomTypes()[0]];
        this._server.send(signal);
    }

    refresh() {
        // check if channel is open. otherwise create one
        if (this._isConnected() || this._isConnecting()) return;

        // only reconnect if peer is caller
        if (!this._isCaller) return;

        this._connect();
    }

    _isConnected() {
        return this._channel && this._channel.readyState === 'open';
    }

    _isConnecting() {
        return this._channel && this._channel.readyState === 'connecting';
    }

    sendDisplayName(displayName) {
        if (!this._isConnected()) return;
        super.sendDisplayName(displayName);
    }
}

class WSPeer extends Peer {

    constructor(serverConnection, isCaller, peerId, roomType, roomId) {
        super(serverConnection, isCaller, peerId, roomType, roomId);

        this.rtcSupported = false;

        if (!this._isCaller) return; // we will listen for a caller
        this._sendSignal();
    }

    _send(chunk) {
        // patched: raw binary frame instead of base64 JSON; the server converts it for clients that need base64
        this._server.sendBinary(this._peerId, chunk);
    }

    _partitionWindow() {
        return 3; // patched: partitions in flight over the server relay
    }

    sendJSON(message) {
        message.to = this._peerId;
        message.roomType = this._getRoomTypes()[0];
        message.roomId = this._roomIds[this._getRoomTypes()[0]];
        this._server.send(message);
    }

    _sendSignal(connected = false) {
        this.sendJSON({type: 'signal', connected: connected});
    }

    onServerMessage(message) {
        this._peerId = message.sender.id;
        Events.fire('peer-connected', {peerId: message.sender.id, connectionHash: this.getConnectionHash()})
        if (message.connected) return;
        this._sendSignal(true);
    }

    getConnectionHash() {
        // Todo: implement SubtleCrypto asymmetric encryption and create connectionHash from public keys
        return "";
    }

    refresh() {
        // patched: called by PeersManager when this peer re-joins; nothing to re-establish over the relay
    }
}

class PeersManager {

    constructor(serverConnection) {
        this.peers = {};
        this._server = serverConnection;
        PdLog.peers = this; // patched: diagnostics
        Events.on('signal', e => this._onMessage(e.detail));
        Events.on('peers', e => this._onPeers(e.detail));
        Events.on('files-selected', e => this._onFilesSelected(e.detail));
        Events.on('respond-to-files-transfer-request', e => this._onRespondToFileTransferRequest(e.detail))
        Events.on('send-text', e => this._onSendText(e.detail));
        Events.on('peer-left', e => this._onPeerLeft(e.detail));
        Events.on('peer-joined', e => this._onPeerJoined(e.detail));
        Events.on('peer-connected', e => this._onPeerConnected(e.detail.peerId));
        Events.on('peer-disconnected', e => this._onPeerDisconnected(e.detail));

        // this device closes connection
        Events.on('room-secrets-deleted', e => this._onRoomSecretsDeleted(e.detail));
        Events.on('leave-public-room', e => this._onLeavePublicRoom(e.detail));

        // peer closes connection
        Events.on('secret-room-deleted', e => this._onSecretRoomDeleted(e.detail));

        Events.on('room-secret-regenerated', e => this._onRoomSecretRegenerated(e.detail));
        Events.on('display-name', e => this._onDisplayName(e.detail.displayName));
        Events.on('self-display-name-changed', e => this._notifyPeersDisplayNameChanged(e.detail));
        Events.on('notify-peer-display-name-changed', e => this._notifyPeerDisplayNameChanged(e.detail));
        Events.on('auto-accept-updated', e => this._onAutoAcceptUpdated(e.detail.roomSecret, e.detail.autoAccept));
        Events.on('ws-disconnected', _ => this._onWsDisconnected());
        Events.on('ws-relay', e => this._onWsRelay(e.detail));
        Events.on('ws-relay-binary', e => this._onWsRelayBinary(e.detail)); // patched
        Events.on('ws-config', e => this._onWsConfig(e.detail));
        Events.on('speedtest-request', e => this._onSpeedtestRequest(e.detail.peerId)); // patched
        Events.on('speedtest-answer', e => { // patched: our user answered a speed test request
            const peer = this.peers[e.detail.peerId];
            if (peer) peer.answerSpeedtest(e.detail.id, e.detail.accepted, e.detail.reason);
        });
        Events.on('rtc-failed', e => this._fallbackToWs(e.detail.peerId, this.peers[e.detail.peerId])); // patched
    }

    // patched: run a speed test with one peer and report progress / result to the UI
    _onSpeedtestRequest(peerId) {
        const report = state => Events.fire('speedtest-state', Object.assign({peerId: peerId}, state));
        const peer = this.peers[peerId];
        if (!peer) return report({error: 'missing'});
        peer.runSpeedTest(phase => report({phase: phase}))
            .then(result => report({result: result}))
            .catch(error => report({error: error.message}));
    }

    _onWsConfig(wsConfig) {
        this._wsConfig = wsConfig;
    }

    _onMessage(message) {
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
        PdLog.add('switch-to-relay', { // patched: diagnostics
            peer: peerId.slice(0, 8),
            side: isCaller ? 'caller-timeout-or-ice-failed' : 'callee-got-ws-handshake',
            rtc: oldPeer._conn ? `${oldPeer._conn.connectionState}/${oldPeer._conn.iceConnectionState}` : 'none',
            waited_ms: oldPeer._pdT0 ? Date.now() - oldPeer._pdT0 : undefined
        });
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
    }

    _refreshPeer(peerId, roomType, roomId) {
        if (!this._peerExists(peerId)) return false;

        const peer = this.peers[peerId];
        const roomTypesDiffer = Object.keys(peer._roomIds)[0] !== roomType;
        const roomIdsDiffer = peer._roomIds[roomType] !== roomId;

        // if roomType or roomId for roomType differs peer is already connected
        // -> only update roomSecret and reevaluate auto accept
        if (roomTypesDiffer || roomIdsDiffer) {
            peer._updateRoomIds(roomType, roomId);
            peer._evaluateAutoAccept();

            return true;
        }

        peer.refresh();

        return true;
    }

    _createOrRefreshPeer(isCaller, peerId, roomType, roomId, rtcSupported) {
        if (this._peerExists(peerId)) {
            this._refreshPeer(peerId, roomType, roomId);
            return;
        }

        if (window.isRtcSupported && rtcSupported) {
            const rtcPeer = new RTCPeer(this._server, isCaller, peerId, roomType, roomId, this._wsConfig.rtcConfig);
            this.peers[peerId] = rtcPeer;
            // patched: the device tile only appears once the data channel opens -- give up on RTC after 6s
            if (isCaller && this._wsConfig.wsFallback) setTimeout(() => this._fallbackToWs(peerId, rtcPeer), 6000);
        }
        else if (this._wsConfig.wsFallback) {
            this.peers[peerId] = new WSPeer(this._server, isCaller, peerId, roomType, roomId);
        }
        else {
            console.warn("Websocket fallback is not activated on this instance.\n" +
                "Activate WebRTC in this browser or ask the admin of this instance to activate the websocket fallback.")
        }
    }

    _onPeerJoined(message) {
        this._createOrRefreshPeer(false, message.peer.id, message.roomType, message.roomId, message.peer.rtcSupported);
    }

    _onPeers(message) {
        message.peers.forEach(peer => {
            this._createOrRefreshPeer(true, peer.id, message.roomType, message.roomId, peer.rtcSupported);
        })
    }

    _onWsRelay(message) {
        if (!this._wsConfig.wsFallback) return;

        const messageJSON = JSON.parse(message);
        if (messageJSON.type === 'ws-chunk') message = base64ToArrayBuffer(messageJSON.chunk);
        this.peers[messageJSON.sender.id]._onMessage(message);
    }

    // patched: raw chunk relayed as a binary frame
    _onWsRelayBinary(detail) {
        if (!this._wsConfig.wsFallback) return;
        const peer = this.peers[detail.senderId];
        if (peer) peer._onMessage(detail.chunk);
    }

    _onRespondToFileTransferRequest(detail) {
        this.peers[detail.to]._respondToFileTransferRequest(detail.accepted);
    }

    async _onFilesSelected(message) {
        let files = mime.addMissingMimeTypesToFiles([...message.files]);
        await this.peers[message.to].requestFileTransfer(files);
    }

    _onSendText(message) {
        this.peers[message.to].sendText(message.text);
    }

    _onPeerLeft(message) {
        const leaving = this.peers[message.peerId]; // patched: diagnostics
        PdLog.add('peer-left', {
            peer: String(message.peerId).slice(0, 8),
            disconnect: message.disconnect ? 1 : 0,
            known: leaving ? leaving._transferProtocol() : 'no',
            busy: leaving && leaving._pdXfer ? leaving._pdXfer.dir : undefined
        });
        if (this._peerExists(message.peerId) && this._webRtcSupported(message.peerId)) {
            console.log('WSPeer left:', message.peerId);
        }
        if (message.disconnect === true) {
            // if user actively disconnected from PairDrop server, disconnect all peer to peer connections immediately
            this._disconnectOrRemoveRoomTypeByPeerId(message.peerId, message.roomType);

            // If no peers are connected anymore, we can safely assume that no other tab on the same browser is connected:
            // Tidy up peerIds in localStorage
            if (Object.keys(this.peers).length === 0) {
                BrowserTabsConnector
                    .removeOtherPeerIdsFromLocalStorage()
                    .then(peerIds => {
                        if (!peerIds) return;
                        console.log("successfully removed other peerIds from localStorage");
                    });
            }
        }
    }

    _onPeerConnected(peerId) {
        this._notifyPeerDisplayNameChanged(peerId);
        // patched: tag the tile once PeerUI has created it (listeners of this same event run first)
        setTimeout(() => {
            const peer = this.peers[peerId];
            const tile = document.getElementById(peerId);
            if (peer && tile) tile.dataset.conn = peer instanceof WSPeer ? 'ws' : 'rtc';
        }, 0);
    }

    _peerExists(peerId) {
        return !!this.peers[peerId];
    }

    _webRtcSupported(peerId) {
        return this.peers[peerId].rtcSupported
    }

    _onWsDisconnected() {
        if (!this._wsConfig || !this._wsConfig.wsFallback) return;

        for (const peerId in this.peers) {
            if (!this._webRtcSupported(peerId)) {
                Events.fire('peer-disconnected', peerId);
            }
        }
    }

    _onPeerDisconnected(peerId) {
        const peer = this.peers[peerId];
        if (peer) { // patched: diagnostics -- a running transfer dies here
            PdLog.add('peer-disconnected', {peer: String(peerId).slice(0, 8), proto: peer._transferProtocol(), busy: peer._pdXfer ? peer._pdXfer.dir : undefined, ws: this._server._isConnected() ? 1 : 0});
            peer._pdXferEnd('xfer-abort');
        }
        delete this.peers[peerId];
        if (!peer || !peer._conn) return;
        if (peer._channel) peer._channel.onclose = null;
        peer._conn.close();
        peer._busy = false;
        peer._roomIds = {};
    }

    _onRoomSecretsDeleted(roomSecrets) {
        for (let i=0; i<roomSecrets.length; i++) {
            this._disconnectOrRemoveRoomTypeByRoomId('secret', roomSecrets[i]);
        }
    }

    _onLeavePublicRoom(publicRoomId) {
        this._disconnectOrRemoveRoomTypeByRoomId('public-id', publicRoomId);
    }

    _onSecretRoomDeleted(roomSecret) {
        this._disconnectOrRemoveRoomTypeByRoomId('secret', roomSecret);
    }

    _disconnectOrRemoveRoomTypeByRoomId(roomType, roomId) {
        const peerIds = this._getPeerIdsFromRoomId(roomId);

        if (!peerIds.length) return;

        for (let i=0; i<peerIds.length; i++) {
            this._disconnectOrRemoveRoomTypeByPeerId(peerIds[i], roomType);
        }
    }

    _disconnectOrRemoveRoomTypeByPeerId(peerId, roomType) {
        const peer = this.peers[peerId];

        if (!peer) return;

        if (peer._getRoomTypes().length > 1) {
            peer._removeRoomType(roomType);
        }
        else {
            Events.fire('peer-disconnected', peerId);
        }
    }

    _onRoomSecretRegenerated(message) {
        PersistentStorage
            .updateRoomSecret(message.oldRoomSecret, message.newRoomSecret)
            .then(_ => {
                console.log("successfully regenerated room secret");
                Events.fire("room-secrets", [message.newRoomSecret]);
            })
    }

    _notifyPeersDisplayNameChanged(newDisplayName) {
        this._displayName = newDisplayName ? newDisplayName : this._originalDisplayName;
        for (const peerId in this.peers) {
            this._notifyPeerDisplayNameChanged(peerId);
        }
    }

    _notifyPeerDisplayNameChanged(peerId) {
        const peer = this.peers[peerId];
        if (!peer) return;
        this.peers[peerId].sendDisplayName(this._displayName);
    }

    _onDisplayName(displayName) {
        this._originalDisplayName = displayName;
        // if the displayName has not been changed (yet) set the displayName to the original displayName
        if (!this._displayName) this._displayName = displayName;
    }

    _onAutoAcceptUpdated(roomSecret, autoAccept) {
        const peerId = this._getPeerIdsFromRoomId(roomSecret)[0];

        if (!peerId) return;

        this.peers[peerId]._setAutoAccept(autoAccept);
    }

    _getPeerIdsFromRoomId(roomId) {
        if (!roomId) return [];

        let peerIds = []
        for (const peerId in this.peers) {
            const peer = this.peers[peerId];

            // peer must have same roomId.
            if (Object.values(peer._roomIds).includes(roomId)) {
                peerIds.push(peer._peerId);
            }
        }
        return peerIds;
    }
}

class FileChunker {

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
    }

    _isPartitionEnd() {
        return this._partitionSize >= this._maxPartitionSize;
    }

    isFileEnd() {
        return this._offset >= this._file.size;
    }
}

class FileDigester {

    constructor(meta, totalSize, totalBytesReceived, callback) {
        this._buffer = [];
        this._bytesReceived = 0;
        this._size = meta.size;
        this._name = meta.name;
        this._mime = meta.mime;
        this._totalSize = totalSize;
        this._totalBytesReceived = totalBytesReceived;
        this._callback = callback;
    }

    unchunk(chunk) {
        this._buffer.push(chunk);
        this._bytesReceived += chunk.byteLength || chunk.size;
        this.progress = (this._totalBytesReceived + this._bytesReceived) / this._totalSize;
        if (isNaN(this.progress)) this.progress = 1

        if (this._bytesReceived < this._size) return;
        // we are done
        const blob = new Blob(this._buffer)
        this._buffer = null;
        this._callback(new File([blob], this._name, {
            type: this._mime || "application/octet-stream",
            lastModified: new Date().getTime()
        }));
    }
}
