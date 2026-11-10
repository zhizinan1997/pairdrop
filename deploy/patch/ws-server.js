import {WebSocketServer} from "ws";
import crypto from "crypto"
import fs from "fs"; // patched: connection log file

import Peer from "./peer.js";
import {hasher, randomizer} from "./helper.js";

export default class PairDropWsServer {

    constructor(server, conf) {
        this._conf = conf

        this._rooms = {}; // { roomId: peers[] }

        this._roomSecrets = {}; // { pairKey: roomSecret }
        this._keepAliveTimers = {};
        this._latestPeer = {}; // patched: newest Peer object per peer id

        this._wss = new WebSocketServer({ server });
        // patched: detailed connection log (see _lg* below)
        this._lgInit();
        this._wss.on('connection', (socket, request) => {
            const peer = new Peer(socket, request, conf);
            this._lgAttach(peer, request);
            this._onConnection(peer);
            this._lgConnected(peer);
        });
    }

    _onConnection(peer) {
        this._latestPeer[peer.id] = peer; // patched
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
        }
        // patched: any message counts as a heartbeat; binary frames are raw ws-fallback file chunks
        peer.socket.on('message', (message, isBinary) => {
            this._setKeepAliveTimerToNow(peer);
            this._lgIn(peer, isBinary); // patched: log
            if (isBinary) this._onBinaryChunk(peer, message);
            else this._onMessage(peer, message);
        });
        peer.socket.onerror = e => this._lg('错误', peer, {err: e && (e.message || (e.error && e.error.code)) || String(e)}); // patched: was console.error(e)

        this._keepAlive(peer);

        this._send(peer, {
            type: 'ws-config',
            wsConfig: {
                rtcConfig: this._conf.rtcConfig,
                wsFallback: this._conf.wsFallback
            }
        });

        // send displayName
        this._send(peer, {
            type: 'display-name',
            displayName: peer.name.displayName,
            deviceName: peer.name.deviceName,
            peerId: peer.id,
            peerIdHash: hasher.hashCodeSalted(peer.id)
        });
    }

    _onMessage(sender, message) {
        // Try to parse message
        try {
            message = JSON.parse(message);
        } catch (e) {
            console.warn("WS: Received JSON is malformed");
            return;
        }

        switch (message.type) {
            case 'disconnect':
                sender._lgCloseBy = sender._lgCloseBy || '客户端发来 disconnect（关闭页面）'; // patched: log
                this._onDisconnect(sender);
                break;
            case 'client-log': // patched: browser-side diagnostics, written to the server log
                this._lgClient(sender, message);
                break;
            case 'pong':
                this._setKeepAliveTimerToNow(sender);
                break;
            case 'join-ip-room':
                this._joinIpRoom(sender);
                break;
            case 'room-secrets':
                this._onRoomSecrets(sender, message);
                break;
            case 'room-secrets-deleted':
                this._onRoomSecretsDeleted(sender, message);
                break;
            case 'pair-device-initiate':
                this._onPairDeviceInitiate(sender);
                break;
            case 'pair-device-join':
                this._onPairDeviceJoin(sender, message);
                break;
            case 'pair-device-cancel':
                this._onPairDeviceCancel(sender);
                break;
            case 'regenerate-room-secret':
                this._onRegenerateRoomSecret(sender, message);
                break;
            case 'create-public-room':
                this._onCreatePublicRoom(sender);
                break;
            case 'join-public-room':
                this._onJoinPublicRoom(sender, message);
                break;
            case 'leave-public-room':
                this._onLeavePublicRoom(sender);
                break;
            case 'signal':
                this._signalAndRelay(sender, message);
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
            case 'transfer-cancel': // patched: cancel a relayed file transfer
                // relay ws-fallback
                if (this._conf.wsFallback) {
                    this._signalAndRelay(sender, message);
                }
                else {
                    console.log("Websocket fallback is not activated on this instance.")
                }
        }
    }

    // patched: binary ws-fallback chunk = 36-byte recipient id + raw bytes. Recipients that connected with
    // ws_binary=1 get 36-byte sender id + raw bytes; older clients get the classic base64 'ws-chunk' JSON.
    _onBinaryChunk(sender, data) {
        if (!this._conf.wsFallback || data.length < 36) return;
        const to = data.toString('latin1', 0, 36);
        if (!Peer.isValidUuid(to)) return;
        const recipient = [sender.ip, sender.publicRoomId, ...sender.roomSecrets]
            .map(roomId => roomId && this._rooms[roomId] && this._rooms[roomId][sender.id] && this._rooms[roomId][to])
            .find(Boolean);
        this._lgChunk(sender, to, recipient, data.length - 36); // patched: relay sessions / pauses / drops
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

    _signalAndRelay(sender, message) {
        const room = message.roomType === 'ip'
            ? sender.ip
            : message.roomId;

        this._lgRelayMsg(sender, room, message); // patched: log transfer control messages and undeliverable ones

        // relay message to recipient
        if (message.to && Peer.isValidUuid(message.to) && this._rooms[room]) {
            const recipient = this._rooms[room][message.to];
            delete message.to;
            // add sender
            message.sender = {
                id: sender.id,
                rtcSupported: sender.rtcSupported
            };
            this._send(recipient, message);
        }
    }

    _onDisconnect(sender) {
        this._disconnect(sender);
    }

    _disconnect(sender) {
        // patched: a newer connection with the same peer id owns rooms and keepalive now -- only close this stale one
        if (this._latestPeer[sender.id] && this._latestPeer[sender.id] !== sender) {
            sender._lgCloseBy = sender._lgCloseBy || '旧连接，已被同一设备的新连接取代'; // patched: log
            sender.socket.terminate();
            return;
        }
        delete this._latestPeer[sender.id];

        this._removePairKey(sender.pairKey);
        sender.pairKey = null;

        this._cancelKeepAlive(sender);
        delete this._keepAliveTimers[sender.id];

        this._leaveIpRoom(sender, true);
        this._leaveAllSecretRooms(sender, true);
        this._leavePublicRoom(sender, true);

        sender.socket.terminate();
    }

    _onRoomSecrets(sender, message) {
        if (!message.roomSecrets) return;

        const roomSecrets = message.roomSecrets.filter(roomSecret => {
            return /^[\x00-\x7F]{64,256}$/.test(roomSecret);
        })

        if (!roomSecrets) return;

        this._joinSecretRooms(sender, roomSecrets);
    }

    _onRoomSecretsDeleted(sender, message) {
        for (let i = 0; i<message.roomSecrets.length; i++) {
            this._deleteSecretRoom(message.roomSecrets[i]);
        }
    }

    _deleteSecretRoom(roomSecret) {
        const room = this._rooms[roomSecret];
        if (!room) return;

        for (const peerId in room) {
            const peer = room[peerId];

            this._leaveSecretRoom(peer, roomSecret, true);

            this._send(peer, {
                type: 'secret-room-deleted',
                roomSecret: roomSecret,
            });
        }
    }

    _onPairDeviceInitiate(sender) {
        let roomSecret = randomizer.getRandomString(256);
        let pairKey = this._createPairKey(sender, roomSecret);

        if (sender.pairKey) {
            this._removePairKey(sender.pairKey);
        }
        sender.pairKey = pairKey;

        this._send(sender, {
            type: 'pair-device-initiated',
            roomSecret: roomSecret,
            pairKey: pairKey
        });
        this._joinSecretRoom(sender, roomSecret);
    }

    _onPairDeviceJoin(sender, message) {
        if (sender.rateLimitReached()) {
            this._send(sender, { type: 'join-key-rate-limit' });
            return;
        }

        if (!this._roomSecrets[message.pairKey] || sender.id === this._roomSecrets[message.pairKey].creator.id) {
            this._send(sender, { type: 'pair-device-join-key-invalid' });
            return;
        }

        const roomSecret = this._roomSecrets[message.pairKey].roomSecret;
        const creator = this._roomSecrets[message.pairKey].creator;
        this._removePairKey(message.pairKey);
        this._send(sender, {
            type: 'pair-device-joined',
            roomSecret: roomSecret,
            peerId: creator.id
        });
        this._send(creator, {
            type: 'pair-device-joined',
            roomSecret: roomSecret,
            peerId: sender.id
        });
        this._joinSecretRoom(sender, roomSecret);
        this._removePairKey(sender.pairKey);
    }

    _onPairDeviceCancel(sender) {
        const pairKey = sender.pairKey

        if (!pairKey) return;

        this._removePairKey(pairKey);
        this._send(sender, {
            type: 'pair-device-canceled',
            pairKey: pairKey,
        });
    }

    _onCreatePublicRoom(sender) {
        let publicRoomId = randomizer.getRandomString(5, true).toLowerCase();

        this._send(sender, {
            type: 'public-room-created',
            roomId: publicRoomId
        });

        this._joinPublicRoom(sender, publicRoomId);
    }

    _onJoinPublicRoom(sender, message) {
        if (sender.rateLimitReached()) {
            this._send(sender, { type: 'join-key-rate-limit' });
            return;
        }

        if (!this._rooms[message.publicRoomId] && !message.createIfInvalid) {
            this._send(sender, { type: 'public-room-id-invalid', publicRoomId: message.publicRoomId });
            return;
        }

        this._leavePublicRoom(sender);
        this._joinPublicRoom(sender, message.publicRoomId);
    }

    _onLeavePublicRoom(sender) {
        this._leavePublicRoom(sender, true);
        this._send(sender, { type: 'public-room-left' });
    }

    _onRegenerateRoomSecret(sender, message) {
        const oldRoomSecret = message.roomSecret;
        const newRoomSecret = randomizer.getRandomString(256);

        // notify all other peers
        for (const peerId in this._rooms[oldRoomSecret]) {
            const peer = this._rooms[oldRoomSecret][peerId];
            this._send(peer, {
                type: 'room-secret-regenerated',
                oldRoomSecret: oldRoomSecret,
                newRoomSecret: newRoomSecret,
            });
            peer.removeRoomSecret(oldRoomSecret);
        }
        delete this._rooms[oldRoomSecret];
    }

    _createPairKey(creator, roomSecret) {
        let pairKey;
        do {
            // get randomInt until keyRoom not occupied
            pairKey = crypto.randomInt(1000000, 1999999).toString().substring(1); // include numbers with leading 0s
        } while (pairKey in this._roomSecrets)

        this._roomSecrets[pairKey] = {
            roomSecret: roomSecret,
            creator: creator
        }

        return pairKey;
    }

    _removePairKey(pairKey) {
        if (pairKey in this._roomSecrets) {
            this._roomSecrets[pairKey].creator.pairKey = null
            delete this._roomSecrets[pairKey];
        }
    }

    _joinIpRoom(peer) {
        this._joinRoom(peer, 'ip', peer.ip);
    }

    _joinSecretRoom(peer, roomSecret) {
        this._joinRoom(peer, 'secret', roomSecret);

        // add secret to peer
        peer.addRoomSecret(roomSecret);
    }

    _joinPublicRoom(peer, publicRoomId) {
        // prevent joining of 2 public rooms simultaneously
        this._leavePublicRoom(peer);

        this._joinRoom(peer, 'public-id', publicRoomId);

        peer.publicRoomId = publicRoomId;
    }

    _joinRoom(peer, roomType, roomId) {
        // roomType: 'ip', 'secret' or 'public-id'
        if (this._rooms[roomId] && this._rooms[roomId][peer.id]) {
            // ensures that otherPeers never receive `peer-left` after `peer-joined` on reconnect.
            this._leaveRoom(peer, roomType, roomId);
        }

        // if room doesn't exist, create it
        if (!this._rooms[roomId]) {
            this._rooms[roomId] = {};
        }

        this._notifyPeers(peer, roomType, roomId);

        // add peer to room
        this._rooms[roomId][peer.id] = peer;
    }


    _leaveIpRoom(peer, disconnect = false) {
        this._leaveRoom(peer, 'ip', peer.ip, disconnect);
    }

    _leaveSecretRoom(peer, roomSecret, disconnect = false) {
        this._leaveRoom(peer, 'secret', roomSecret, disconnect)

        //remove secret from peer
        peer.removeRoomSecret(roomSecret);
    }

    _leavePublicRoom(peer, disconnect = false) {
        if (!peer.publicRoomId) return;

        this._leaveRoom(peer, 'public-id', peer.publicRoomId, disconnect);

        peer.publicRoomId = null;
    }

    _leaveRoom(peer, roomType, roomId, disconnect = false) {
        if (!this._rooms[roomId] || !this._rooms[roomId][peer.id]) return;

        // remove peer from room
        delete this._rooms[roomId][peer.id];

        // delete room if empty and abort
        if (!Object.keys(this._rooms[roomId]).length) {
            delete this._rooms[roomId];
            return;
        }

        // notify all other peers that remain in room that peer left
        for (const otherPeerId in this._rooms[roomId]) {
            const otherPeer = this._rooms[roomId][otherPeerId];

            let msg = {
                type: 'peer-left',
                peerId: peer.id,
                roomType: roomType,
                roomId: roomId,
                disconnect: disconnect
            };

            this._send(otherPeer, msg);
        }
    }

    _notifyPeers(peer, roomType, roomId) {
        if (!this._rooms[roomId]) return;

        // notify all other peers that peer joined
        for (const otherPeerId in this._rooms[roomId]) {
            if (otherPeerId === peer.id) continue;
            const otherPeer = this._rooms[roomId][otherPeerId];

            let msg = {
                type: 'peer-joined',
                peer: peer.getInfo(),
                roomType: roomType,
                roomId: roomId
            };

            this._send(otherPeer, msg);
        }

        // notify peer about peers already in the room
        const otherPeers = [];
        for (const otherPeerId in this._rooms[roomId]) {
            if (otherPeerId === peer.id) continue;
            otherPeers.push(this._rooms[roomId][otherPeerId].getInfo());
        }

        let msg = {
            type: 'peers',
            peers: otherPeers,
            roomType: roomType,
            roomId: roomId
        };

        this._send(peer, msg);
    }

    _joinSecretRooms(peer, roomSecrets) {
        for (let i=0; i<roomSecrets.length; i++) {
            this._joinSecretRoom(peer, roomSecrets[i])
        }
    }

    _leaveAllSecretRooms(peer, disconnect = false) {
        for (let i=0; i<peer.roomSecrets.length; i++) {
            this._leaveSecretRoom(peer, peer.roomSecrets[i], disconnect);
        }
    }

    _send(peer, message) {
        if (!peer) return;
        if (this._wss.readyState !== this._wss.OPEN) return;
        message = JSON.stringify(message);
        peer.socket.send(message);
        this._lgOut(peer); // patched: log
    }

    // ---- patched: detailed connection log ----------------------------------------------------------------------
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

    _keepAlive(peer) {
        this._cancelKeepAlive(peer);
        let timeout = 1000;

        if (!this._keepAliveTimers[peer.id]) {
            this._keepAliveTimers[peer.id] = {
                timer: 0,
                lastBeat: Date.now()
            };
        }

        if (Date.now() - this._keepAliveTimers[peer.id].lastBeat > 30 * timeout) {
            // patched: disconnect peer if unresponsive for 30s (upstream: 5s, too strict for cross-border links)
            peer._lgCloseBy = '心跳超时，服务器踢出（30 秒没收到任何消息）'; // patched: log
            this._lgKick(peer);
            this._disconnect(peer);
            return;
        }

        this._send(peer, { type: 'ping' });

        this._keepAliveTimers[peer.id].timer = setTimeout(() => this._keepAlive(peer), timeout);
    }

    _cancelKeepAlive(peer) {
        if (this._keepAliveTimers[peer.id]?.timer) {
            clearTimeout(this._keepAliveTimers[peer.id].timer);
        }
    }

    _setKeepAliveTimerToNow(peer) {
        if (this._keepAliveTimers[peer.id]?.lastBeat) {
            this._keepAliveTimers[peer.id].lastBeat = Date.now();
        }
    }
}

