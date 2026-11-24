"""服务器：监听客户端、按同步方式收集/分发、支持 console 运行时切换。

两种方式共用 ``core.sim`` 的确定性推演：

- **帧同步 lockstep**：服务器按固定 tick 收集各客户端输入，凑齐一帧后广播
  ``frame``（序号 + 各玩家输入），由各客户端用同一 ``sim`` 推演。服务器也跑一份
  作为「参照世界」判断胜负。
- **状态同步 state sync**：服务器权威，用 ``sim`` 推演权威世界，定期广播
  ``snapshot`` 快照；客户端只发输入并做插值/预测显示。

切换命令在服务器 console 输入 ``mode lockstep`` / ``mode state``。
"""

import os
import random
import select
import socket
import threading

from sync_demo.core import ability as AB
from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD
from sync_demo.net import fault as F
from sync_demo.net import kcp as K
from sync_demo.net import protocol as P
from sync_demo.net import timewarp as TW
from sync_demo.net import transport as T


TICK_INTERVAL = 0.05           # 每 tick 的墙上间隔（秒）—— 只用于调度，不进逻辑
LOCKSTEP_FIXED_STEP = 2        # 每个「指令帧」包含的逻辑 tick 数（帧同步常见做法）


class ClientConn:
    """一个已连接的客户端连接：TCP socket + UDP 端点 + 缓冲 + 身份。"""

    def __init__(self, sock, addr, udp_endpoint):
        self.sock = sock
        self.addr = addr
        self.udp_endpoint = udp_endpoint   # KCP over UDP 端点（非重要消息）
        self.player_id = None
        self.name = "unnamed"
        self.inbuf = b""
        self.ready = False
        self.session_token = None          # 断线重连身份标识（hello 里带上）
        self.disconnected_at = None        # 断线时刻（None=在线；非 None=已挂起）

    def send_important(self, msg):
        """重要消息走 TCP（客户端可能已断连，send 失败的 OSError 静默）。

        为什么「重要」就得上 TCP：这类消息是逻辑正确性的根基，必须保证顺序、
        不丢、不重复地下达。TCP 天生提供可靠有序的字节流，代价是握手、重传与
        拥塞控制带来的延迟；但对 welcome/断连/切模式这类「少而关键」的消息，
        用延迟换可靠性是划算的。反过来，若把重要消息扔到 UDP，一旦丢包客户端
        可能拿到残缺的身份信息或永远卡在错误模式，且没有重传兜底。
        这里吞掉 OSError：send 时刻客户端可能已经异常断开（RST），此时没必要
        让一次普通的下发把整个服务器线程带崩,连接本身由 _poll_clients 统一回收。
        """
        data = P.encode(msg)
        try:
            self.sock.send(data)
        except OSError:
            pass

    def send_unimportant(self, msg):
        """非重要消息走 KCP over UDP；若尚未学到客户端 UDP 源地址则回退 TCP。

        为什么「非重要」可以走 UDP：像 frame/snapshot 这类高频、且对单个丢失
        有一定容忍度的消息，走 TCP 会因队头阻塞而被前一个未确认的包卡住，拖慢
        整条链路。KCP 在 UDP 上自己实现可靠+有序，但只在「需要时」重传，不卡
        队头，更适合实时对战。所以「非重要」不是「丢了无所谓」，而是「宁可
        延迟低、相对可靠，也别被 TCP 的严格有序卡住」。
        """
        if self.udp_endpoint.addr is None:
            self.send_important(msg)
            return
        self.udp_endpoint.send(P.encode(msg))


class Server:
    """同步对战服务器。"""

    def __init__(self, host, port, udp_port=None):
        self.host = host
        self.port = port
        # 非重要消息走 KCP over UDP；默认跟随 TCP 端口 +1，避免与测试端口冲突
        self.udp_port = udp_port if udp_port is not None else port + 1
        self.mode = "state"              # 默认状态同步；console 可切
        self.clients = {}                # player_id -> ClientConn
        self.udp_sock = None             # 共享 UDP socket（在 _run 里创建）
        self.udp_conv_seq = 1            # KCP conv 分配计数器（按客户端递增）
        self.next_player_id = 1
        self.rng = random.Random(20261123)   # 帧同步确定性随机源；状态同步同样用它
        self.world = G.World(self.rng)   # 权威世界（两模式通用）
        self.lockstep_seq = 0            # 帧同步的帧序号
        self.pending_inputs = {}         # 帧同步：player_id -> 最近一帧输入
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        # ---- 延迟补偿（Lag Compensation）状态 ----
        self.history = TW.WorldHistoryBuffer(capacity=128)  # 每 tick 归档世界快照，供回退裁判
        self.client_latency = {}         # player_id -> 估测的网络延迟（秒）；可注入/估算
        self.lag_comp_enabled = True     # 延迟补偿开关（可 console 切换，研究开/关差异）
        # ---- 断线重连状态 ----
        self.disconnected = {}           # player_id -> ClientConn（断线挂起，宽限期内可重连恢复）
        self.disconnect_grace = 10.0     # 断线宽限秒数：超时才真正移除此玩家
        # ---- 分布式授权状态 ----
        self.owner_id = None             # 当前持有「授权标记」的玩家（None=无人持有）

    # ---- 生命周期 ----

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _run(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        listener.setblocking(False)

        # 非重要消息走 KCP over UDP：独立 UDP socket 复用给所有客户端端点
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind((self.host, self.udp_port))
        udp_sock.setblocking(False)
        self.udp_sock = udp_sock

        print("[server] listening on {}:{} (udp {})  mode={}".format(
            self.host, self.port, self.udp_port, self.mode))
        print('[server] console: "mode lockstep" / "mode state"')

        next_tick = 0.0
        while self.running:
            now = _now()
            # 接受新连接
            ready, _, _ = select.select([listener], [], [], 0)
            if ready:
                try:
                    sock, addr = listener.accept()
                except OSError:
                    sock, addr = None, None
                if sock is not None:
                    self._add_client(sock, addr)

            # 处理已有客户端的数据（TCP 重要 + UDP 非重要）
            self._poll_clients()
            self._poll_udp()
            # 清掉超过宽限时长的断线玩家（断线重连的超时真移除）
            self._reap_expired_disconnects(_now())

            # 按节奏推进
            if now >= next_tick:
                self._advance()
                next_tick = now + TICK_INTERVAL

            _sleep(0.001)

        listener.close()
        udp_sock.close()
        self.udp_sock = None

    # ---- 连接管理 ----

    def _add_client(self, sock, addr):
        sock.setblocking(False)
        # KCP 端点：conv 由服务器分配（递增）；UDP 源地址在客户端首次发包后学到。
        # 注意：这里**不分配 player_id、也不 add_player**——身份要在收到 hello（含
        # session_token）后才定，因为要先判断「这是新玩家，还是断线重连」。见 _handle hello。
        conv = self.udp_conv_seq
        self.udp_conv_seq += 1
        udp_ep = T.UDPEndpoint(self.udp_sock, None, conv)
        conn = ClientConn(sock, addr, udp_ep)
        conn.conv = conv
        with self.lock:
            # 暂用 conv 作为临时键挂进 clients，等 hello 定了 player_id 再换正式键
            self.clients[conv] = conn
        return conn

    def _register_player(self, conn):
        """hello 后注册玩家身份：重连则复用原 player_id，否则分配新玩家。

        断线重连判定：用 hello 里的 session_token 去 ``disconnected`` 里找「挂起中、
        同 token」的旧连接。找到 → 复用旧 player_id（世界里的玩家状态一直在，宽限期内
        没被移除）；找不到 → 全新玩家。
        """
        token = conn.session_token
        conv = conn.conv
        reused_pid = None
        if token is not None:
            with self.lock:
                for pid, old_conn in list(self.disconnected.items()):
                    if old_conn.session_token == token:
                        reused_pid = pid
                        self.disconnected.pop(pid, None)
                        break
        if reused_pid is not None:
            # 断线重连：复用原 player_id，世界里的玩家原地保留（状态不变）
            pid = reused_pid
            conn.player_id = pid
            with self.lock:
                self.clients.pop(conv, None)
                self.clients[pid] = conn
            print("[server] player {} reconnected ({})".format(pid, conn.addr[0]))
        else:
            # 新玩家
            with self.lock:
                pid = self.next_player_id
                self.next_player_id += 1
                conn.player_id = pid
                self.clients.pop(conv, None)
                self.clients[pid] = conn
            x = 100 + (pid - 1) * 120
            player = self.world.add_player(pid, "p{}".format(pid), x, G.GROUND_Y)
            # 颜色是「需要同步的属性」：服务器随机分配一次，随快照同步给所有客户端，
            # 客户端渲染读这个同步值（而非各自推导），保证各端一致。
            player.color_index = self.rng.randrange(8)
            print("[server] player {} joined ({}:{})".format(pid, conn.addr[0], conn.addr[1]))
        # 发 welcome（重连时 world 里该玩家状态已保留，快照自然带上）
        self._send(conn, {"type": P.MSG_WELCOME, "player_id": pid,
                          "mode": self.mode, "conv": conv, "udp_port": self.udp_port,
                          "snapshot": self._snapshot()})
        # 结构同步：新玩家加入/重连都会让客户端集合变化，补发快照对齐
        self._broadcast(self._snapshot())

    def _reap_expired_disconnects(self, now):
        """清掉超过宽限时长的断线玩家（真正移除）。"""
        expired = []
        with self.lock:
            for pid, old_conn in self.disconnected.items():
                if old_conn.disconnected_at is not None and \
                        now - old_conn.disconnected_at > self.disconnect_grace:
                    expired.append(pid)
            for pid in expired:
                self.disconnected.pop(pid, None)
        for pid in expired:
            self.world.remove_player(pid)
            print("[server] player {} left (grace expired)".format(pid))
        if expired:
            self._broadcast(self._snapshot())

    def _poll_clients(self):
        with self.lock:
            conns = list(self.clients.values())
        socks = [c.sock for c in conns]
        if not socks:
            return
        readable, _, _ = select.select(socks, [], [], 0)
        for sock in readable:
            conn = self._conn_by_sock(sock)
            if conn is None:
                continue
            # 客户端异常断连（强制关闭）会抛 ConnectionResetError / ConnectionAbortedError，
            # 与「正常返回空」一样都视为断开，走 drop，不能让它崩掉整个服务器线程。
            data = None
            try:
                data = sock.recv(65536)
            except OSError:
                data = None
            if not data:
                self._drop(conn)
                continue
            conn.inbuf += data
            messages, conn.inbuf = P.decode(conn.inbuf)
            for msg in messages:
                self._handle(conn, msg)

    def _conn_by_sock(self, sock):
        with self.lock:
            for conn in self.clients.values():
                if conn.sock is sock:
                    return conn
        return None

    def _poll_udp(self):
        """读 UDP → 各客户端 KCP 端点，驱动重传并交付非重要消息。

        与 TCP 的差异在这里体现得很清楚：TCP 每个连接是独立套接字、内核替你完成
        握手与按序重组；UDP 则是「一个共享 socket + 一堆按 conv 区分的 KCP 会话」。
        所以这一段第一遍 while 要把共享 socket 上的所有数据报读尽并按 conv 路由——
        不读尽的话下一轮 poll 又会立刻被触发，造成忙等；读到数据报时顺手把真实
        源地址记进端点，这正是「非重要消息回发目标」从无到有的学习过程。
        """
        if self.udp_sock is None:
            return
        with self.lock:
            conns = list(self.clients.values())
        # 每轮把共享 UDP socket 上的所有数据报读尽，按段头 conv 路由到对应端点；
        # 同时学到客户端的真实 UDP 源地址，供之后回发非重要消息。
        while True:
            try:
                data, src_addr = self.udp_sock.recvfrom(65536)
            except BlockingIOError:
                break
            except OSError:
                break
            conn = self._conn_by_conv(K.conv_of(data))
            if conn is None:
                continue
            conn.udp_endpoint.addr = src_addr
            conn.udp_endpoint.feed(data)
        # 驱动每个端点的 KCP 更新（重传）+ 取出按序交付的消息
        for conn in conns:
            conn.udp_endpoint.update()
            self._pump_udp(conn)

    def _pump_udp(self, conn):
        """从某个客户端 KCP 端点取出所有完整消息并分发。"""
        chunk = conn.udp_endpoint.recv(65536)
        while chunk:
            for msg in P.decode_all(chunk):
                self._handle(conn, msg)
            chunk = conn.udp_endpoint.recv(65536)

    def _conn_by_conv(self, conv):
        """按 KCP conv 匹配客户端连接。"""
        with self.lock:
            for conn in self.clients.values():
                if getattr(conn, "conv", None) == conv:
                    return conn
        return None

    def _drop(self, conn):
        """断线：清理连接。若玩家已注册（有 player_id）则挂起进 disconnected 走重连宽限；
        若尚未注册（hello 都没到就断了）则直接从 clients 清掉 conv 键即可。"""
        pid = conn.player_id
        if pid is None:
            # 尚未完成 hello 注册的连接，按 conv 键清掉即可
            with self.lock:
                self.clients.pop(conn.conv, None)
            return
        with self.lock:
            self.clients.pop(pid, None)
            conn.disconnected_at = _now()
            self.disconnected[pid] = conn
        print("[server] player {} disconnected (grace {}s)".format(
            pid, self.disconnect_grace))

    def _send(self, conn, msg):
        """按消息重要性分流：非重要走 KCP/UDP，重要走 TCP。

        分流依据来自 ``T.classify(msg)``——消息类型本身决定它属于哪一类，而不是
        发送者临时拍脑袋。单点分发的好处是：后续所有「发给单个客户端」的下行
        都走这个唯一入口，通道策略集中在一处，改分类逻辑不会散落在各处重复。
        """
        with self.lock:
            if T.classify(msg):
                conn.send_unimportant(msg)
            else:
                conn.send_important(msg)

    def _broadcast(self, msg):
        """向所有客户端广播，按消息重要性选择通道。

        广播与单发的分流规则完全一致（同样查 ``T.classify``），差异只在「对象是
        全体」这一点。注意在锁内先把 ``clients`` 抄成快照再遍历：广播期间可能有
        客户端被加入/移除，直接迭代 ``dict.values()`` 会在锁外、且字典被并发改动
        时抛 RuntimeError，快照避免了这种「边遍历边改集合」的经典竞态。
        """
        with self.lock:
            for conn in list(self.clients.values()):
                if T.classify(msg):
                    conn.send_unimportant(msg)
                else:
                    conn.send_important(msg)

    # ---- 消息处理 ----

    def _handle(self, conn, msg):
        mtype = msg.get("type")
        if mtype == P.MSG_HELLO:
            conn.name = msg.get("name", conn.name)
            conn.session_token = msg.get("session_token")
            self._register_player(conn)
        elif mtype == P.MSG_INPUT:
            with self.lock:
                self.pending_inputs[conn.player_id] = sim.InputState(
                    left=msg.get("left", False),
                    right=msg.get("right", False),
                    jump=msg.get("jump", False),
                    attack=msg.get("attack", False),
                    dash=msg.get("dash", False),
                    freeze=msg.get("freeze", False),
                )
        elif mtype == P.MSG_HASH:
            self._on_client_hash(conn, msg)
        elif mtype == P.MSG_MODE_REQ:
            self.set_mode(msg.get("mode"))
        elif mtype == P.MSG_ATTACK:
            self._on_skill(conn, "melee")
        elif mtype == P.MSG_DASH:
            self._on_skill(conn, "dash")
        elif mtype == P.MSG_FREEZE:
            self._on_skill(conn, "freeze")
        elif mtype == P.MSG_HIT_REPORT:
            self._on_hit_report(conn, msg)
        elif mtype == P.MSG_CLAIM:
            self._on_claim(conn, msg)

    def _on_skill(self, conn, skill_name):
        """服务器权威地应用一个技能事件（UE GAS：激活 Ability 并立即结算/推进）。

        技能是离散事件（客户端按键上升沿触发的 RPC），服务器收到后**立即**构造对应
        Ability 并在权威世界上应用，而不是等下一拍推演。这样「按一下」就会真的生效，
        不会像「每帧 input 覆盖」那样把瞬时技能吞掉。

        注意：攻击/冰冻是「激活即结算」；冲刺是「激活后持续推进」，因此激活后把
        Ability 挂到 world.active_abilities，让后续 sim.tick 推进其持续效果。
        """
        player = self.world.players.get(conn.player_id)
        if player is None or not player.alive:
            return
        # 冷却检查：与 sim.apply_input 里的触发条件对齐，避免绕过冷却
        if skill_name == "melee" and player.attack_cooldown > 0:
            return
        if skill_name == "dash" and player.dash_cooldown > 0:
            return
        if skill_name == "freeze" and player.freeze_cooldown > 0:
            return
        ab = AB.build_ability(skill_name, conn.player_id)
        if ab is None:
            return
        ab.activated_at = self.world.tick
        if ab.activate(self.world, player):
            self.world.active_abilities.append(ab)

    def _on_claim(self, conn, msg):
        """处理客户端「争夺授权标记」请求（分布式授权）。

        简单仲裁策略：先到先得——谁请求，标记就归谁（可覆盖前任）。持有标记者是
        「授权主控端」：在 p2p 无法验证的场景下，它负责那类决策的仲裁，其他玩家渲染
        其结果。广播新的 owner_id 让所有客户端同步「当前谁主控」。
        """
        claimer = conn.player_id
        if claimer is None:
            return
        prev = self.owner_id
        self.owner_id = claimer
        print("[owner] p{} claimed the token (prev={})".format(claimer, prev))
        self._broadcast({"type": P.MSG_OWNER, "owner_id": claimer})

    def _on_hit_report(self, conn, msg):
        """处理客户端「本地碰撞检测」宣称的命中，服务端校验后结算。

        这是「客户端碰撞上报 + 服务端权威校验」模式（区别于 `_on_attack_request` 的
        纯服务器判定）：客户端在本地先做碰撞检测，算出「我打中了谁」，把结论上报；
        服务器不轻信客户端，而是回退到历史世界**校验**这个命中是否合法（朝向/距离/
        目标是否存活），合法才结算伤害、非法则拒绝。

        价值：客户端参与碰撞计算（即时手感），服务端保留权威（防作弊/纠错）。
        """
        attacker_id = conn.player_id
        target_id = msg.get("target_id")
        # 客户端上报的「命中帧号」：服务端据此回退到「客户端看到的那一帧」校验
        hit_tick = msg.get("hit_tick", self.world.tick)
        claimed = bool(msg.get("hit", False))
        if target_id is None:
            return
        # 回退到客户端宣称的时刻，校验其碰撞结论是否成立
        history_world = self.history.nearest(hit_tick)
        judge_world = history_world if history_world is not None else self.world
        valid = TW.rewind_hit_test(judge_world, attacker_id, target_id,
                                   max_range=G.ATTACK_RANGE, max_dy=G.ATTACK_RANGE)
        # 服务端对「客户端宣称命中」的最终裁定：宣称命中且校验通过才结算
        authorized = claimed and valid
        if authorized:
            target = self.world.players.get(target_id)
            if target is not None and target.alive:
                target.hp = max(0, target.hp - G.ATTACK_DAMAGE)
                target.hit_anim = G.ATTACK_ANIM
                if target.hp == 0:
                    target.alive = False
        print("[hitreport] p{} claims hit {} @tick{} : valid={} authorized={}".format(
            attacker_id, target_id, hit_tick, valid, authorized))
        self._send(conn, {"type": P.MSG_ATTACK_RESULT, "target_id": target_id,
                          "hit": authorized})

    def _on_attack_request(self, conn, msg):
        """处理客户端的攻击请求，走延迟补偿裁判。

        延迟补偿的核心：玩家 A 在客户端瞄准「他看到的 B 位置」开枪，但那个画面是
        A 的延迟之前的过去时。若服务器按「当前世界」裁判，B 已经走远，A 必然打空。
        用 lag comp，服务器按 A 的延迟回退到「A 看到的那一帧」的世界快照，在历史
        世界里判定命中，再把这个结果应用到当前世界——网络差的玩家不再吃亏。
        """
        attacker_id = conn.player_id
        target_id = msg.get("target_id")
        if target_id is None:
            return
        hit = False
        if self.lag_comp_enabled:
            # 回退到「攻击者所见」的历史世界：按估测延迟换算成 tick 数回退
            latency = self.client_latency.get(attacker_id, 0.0)
            rewind_ticks = int(latency / TICK_INTERVAL)
            rewind_tick = self.world.tick - rewind_ticks
            history_world = self.history.nearest(rewind_tick)
            judge_world = history_world if history_world is not None else self.world
            hit = TW.rewind_hit_test(judge_world, attacker_id, target_id,
                                     max_range=G.ATTACK_RANGE, max_dy=G.ATTACK_RANGE)
        else:
            # 不补偿：直接用当前世界判定（网络差者吃亏）
            hit = TW.rewind_hit_test(self.world, attacker_id, target_id,
                                     max_range=G.ATTACK_RANGE, max_dy=G.ATTACK_RANGE)
        if hit:
            target = self.world.players.get(target_id)
            if target is not None and target.alive:
                target.hp = max(0, target.hp - G.ATTACK_DAMAGE)
                target.hit_anim = G.ATTACK_ANIM
                if target.hp == 0:
                    target.alive = False
        print("[lagcomp] attacker p{} -> target p{} : hit={} (lag {}ms)".format(
            attacker_id, target_id, hit, int(self.client_latency.get(attacker_id, 0.0) * 1000)))
        self._send(conn, {"type": P.MSG_ATTACK_RESULT, "target_id": target_id, "hit": hit})

    def _on_client_hash(self, conn, msg):
        """收到客户端上报的世界 hash，与权威 hash 对比做 desync 观测。

        这就是「怎么发现不同步」的核心手段：客户端每隔一段时间把自己推演出来的
        世界状态压成一个指纹 hash 上报，服务器拿它与自己权威世界算出的 hash 作
        逐字节比较。因为两边的 ``sim.tick`` 是确定性的，只要输入序列、代码版本、
        浮点/随机源完全一致，两侧 hash 必然相同；一旦出现差异，就说明有某个环节
        发生了「非确定」的偏差（丢输入、随机数不一致、浮点平台差异、作弊等）。

        为什么用 hash 而不是把世界状态整个传回来比：世界可能包含大量玩家/投射物
        字段，全量比对代价高且占带宽；一个固定长度的 hash 足够作为「是否一致」的
        廉价判据。代价是 hash 只能回答「不一致」，不能直接定位是哪一帧、哪个字段
        出了岔——所以下面只在发现 desync 时额外打印权威世界摘要用于人工对照，精确
        的字段级定位留给了 ``core.sync_diff.diff_players``。
        """
        auth_hash = SD.world_hash(self.world)
        client_hash = msg.get("hash")
        # 二进制协议下，超过有符号 64 位的 hash 会被编码成字符串；还原成 int 再比较
        if isinstance(client_hash, str):
            client_hash = int(client_hash)
        tick = msg.get("tick")
        if client_hash == auth_hash:
            print("[sync] tick {} client {} hash OK".format(tick, conn.player_id))
            return
        print("[sync] DESYNC at tick {}: client {} hash {} != authoritative {}".format(
            tick, conn.player_id, client_hash, auth_hash))
        # 定位：权威世界 vs 上报方无法直接拿到对方状态，这里打印权威世界当前各玩家
        # 的字段摘要，供研究者对照（字段级精确定位见 core.sync_diff.diff_players）
        print("[sync] authoritative player states:")
        for pid in sorted(self.world.players):
            ps = self.world.players[pid]
            print("[sync]   p{} x={} y={} vx={} hp={}".format(
                pid, ps.x.to_float(), ps.y.to_float(), ps.vx.to_float(), ps.hp))

    # ---- 推进 ----

    def _advance(self):
        """按当前模式把「定时推进」派发到对应实现。

        这一层是唯一的切换点：console 里 ``mode lockstep`` / ``mode state`` 改的
        只是 ``self.mode`` 这一个字段，而每次 tick 到这里按字段决定走哪条路径。
        因此「服务器双模式」的差别被完全收敛在这一个分支上，两种推进逻辑各自独立、
        互不污染，也让运行时切换成为可能——切完下一拍起就换一套语义。
        """
        if self.mode == "lockstep":
            self._advance_lockstep()
        else:
            self._advance_state()

    def _advance_lockstep(self):
        """帧同步：服务器只当「输入中转站」，不替客户端算世界。

        这是 lockstep 与 state 最根本的分野，答案就藏在这一段「没做什么」里：
        - 服务器把自己收到的所有玩家输入凑成一帧，广播出去，然后自己也推进一份，
          但它推进的这份世界**只用来判断胜负/验 hash，绝不反序列化、也不广播出去**。
        - 每个客户端拿到同样的 ``frame``（seq + 所有玩家的输入），用各自本地那份
          ``sim`` 自己推演。因为 ``sim.tick`` 是确定性的，输入一致 ⇒ 结果必然一致，
          所以根本不需要服务器把世界状态发回来——这就省掉了「反序列化世界状态再
          下发」的高昂带宽与复杂度。

        为什么不反序列化世界状态：在 lockstep 里，权威的从来不是「世界」，而是
        「输入序列」。只要所有人拿到相同顺序的输入，世界自然在各自机器上同步诞生；
        服务器若去反序列化/广播世界状态，反而把本应廉价的确定性同步，退化成了
        昂贵且易出错的全量状态复制。服务器这边对世界的使用是「旁观」而非「裁决」。
        """
        # 收集到的输入作为本帧的输入。
        # 注意：这里**不清空** pending_inputs，而是「覆盖式」保留每个客户端最近
        # 一次的输入。原因：锁步下客户端 30fps 发输入、服务器 20Hz 推进，二者频率
        # 错位；若每 tick 清空，某个客户端「这一拍恰好没发到」的输入就会永久丢失，
        # 导致它本地预推演了、服务器 frame 里却没有 → 必然 desync。覆盖式保留能让
        # 服务器每拍都广播「当前已知的每个玩家输入」，客户端据此回滚重放即可对齐。
        # 代价：客户端松开按键后若不再发输入，服务器会沿用旧输入一段时间——
        # 这个「输入驻留」语义在慢速教学 demo 里可接受，正式锁步应改为帧号对齐。
        with self.lock:
            inputs = dict(self.pending_inputs)
        # 帧内推进 FIXED_STEP 个逻辑 tick（用与本帧广播完全相同的 inputs，保证服务器
        # 自己的权威世界与客户端回滚重放出的世界逐位一致，hash 才能对得上）。
        for _ in range(LOCKSTEP_FIXED_STEP):
            sim.tick(self.world, inputs)
        self.lockstep_seq += 1
        frame = {
            "type": P.MSG_FRAME,
            "seq": self.lockstep_seq,
            "inputs": {str(k): v.as_tuple() for k, v in inputs.items()},
        }
        self._broadcast(frame)

    def _advance_state(self):
        """状态同步：服务器权威，自己算世界并广播 snapshot。

        与 lockstep 相反，这里的权威就是「世界状态」本身：
        - 服务器拿到所有输入后，用自己那份 ``sim`` 亲自 ``tick`` 出下一帧世界，
          然后把这个世界的快照（snapshot）广播给客户端。客户端不做推演、只做
          插值/预测来「贴近」权威快照。
        - 权威在哪：**世界 = 服务器 tick 出来的这份**。客户端上报输入只是「请愿」，
          服务器算出的结果才是最终裁决，谁卡了、谁乱了都不影响服务器的正确世界。

        为什么这里是权威？因为状态同步放弃了「所有客户端各自推演再对齐」的代价，
        换成了「服务器包办推演」的代价。这样客户端可以是轻量的、甚至允许客户端
        漏掉几次模拟（反正下一帧 snapshot 会把它拉回正轨），代价是服务器要承受
        全部模拟开销、且 snapshot 比 frame 大得多（要携带完整玩家/投射物状态）。
        """
        with self.lock:
            inputs = dict(self.pending_inputs)
            self.pending_inputs.clear()
        sim.tick(self.world, inputs)
        # 延迟补偿的地基：每个 tick 结束后把世界快照归档进历史 buffer。
        # 这样玩家 A 的「攻击请求」到达时，服务器能按 A 的延迟回退到「A 看到的那一帧」
        # 判定命中（而非按服务器当前世界判定，避免网络差者吃亏）。
        self.history.record(self.world.tick, self.world)
        snapshot = self._snapshot()
        self._broadcast(snapshot)

    # ---- 快照 ----

    def _snapshot(self):
        """把权威世界的当前状态打包成下行快照。

        这是状态同步的「世界副本」，携带全部玩家状态与投射物状态。注意它在两处被
        ``_advance_state`` 周期广播，也在 ``_add_client`` 里随 welcome 发给刚上线的
        客户端作初始世界——所以新玩家一进门就能从零对齐，不必从 server 启动开始补。
        """
        return {
            "type": P.MSG_SNAPSHOT,
            "tick": self.world.tick,
            "players": [ps.as_dict() for ps in self.world.players.values()],
            "projectiles": [
                {"proj_id": p.proj_id, "owner_id": p.owner_id, "x": p.x.raw, "y": p.y.raw}
                for p in self.world.projectiles.values()
            ],
            # UE GAS 风格：同步「正在生效的技能」状态，客户端据此还原表现/持续效果
            "abilities": [ab.as_dict() for ab in self.world.active_abilities],
        }

    # ---- console ----

    def set_mode(self, mode):
        """console 切换同步模式；非法值不生效，切换后广播通知所有客户端。

        切换前先清空 ``pending_inputs``：两种模式对输入的消费节奏不同，锁步里可能
        积压了尚未凑帧的输入，若不清空，切到状态同步的第一拍会用「旧输入」推演，
        与客户端基于新模式的预期错位。广播 ``MSG_MODE`` 让客户端同步切换显示/推演
        逻辑，避免服务器换了语义而客户端浑然不觉地继续按旧模式解析。
        """
        if mode not in ("lockstep", "state"):
            return False
        if mode == self.mode:
            return True
        self.mode = mode
        with self.lock:
            self.pending_inputs.clear()
        self._broadcast({"type": P.MSG_MODE, "mode": mode})
        print("[server] mode switched to {}".format(mode))
        return True


def _now():
    import time
    return time.monotonic()


def _sleep(seconds):
    import time
    time.sleep(seconds)


def run():
    """由 ``python -m sync_demo.server`` 进入的进程入口。"""
    host = os.environ.get("SYNC_DEMO_HOST", "0.0.0.0")
    port = int(os.environ.get("SYNC_DEMO_PORT", "7808"))
    udp_env = os.environ.get("SYNC_DEMO_UDP_PORT")
    udp_port = int(udp_env) if udp_env else None
    server = Server(host, port, udp_port)
    server.start()

    print('[server] commands: "mode lockstep/state" switch sync; '
          '"fault <param> <value>" inject; "lagcomp on/off"; "setlag <pid> <ms>"; '
          '"hash" show hash; "quit" exit')
    while True:
        line = _safe_input()
        if line is None:
            continue
        line = line.strip()
        if line == "":
            continue
        if line == "quit":
            server.stop()
            break
        if line.startswith("mode "):
            server.set_mode(line.split()[1])
        elif line.startswith("fault "):
            _handle_fault_cmd(server, line)
        elif line.startswith("lagcomp "):
            _handle_lagcomp_cmd(server, line)
        elif line.startswith("setlag "):
            _handle_setlag_cmd(server, line)
        elif line.startswith("preset "):
            _handle_preset_cmd(server, line)
        elif line == "hash":
            print("[server] authoritative hash = {}".format(SD.world_hash(server.world)))
        else:
            print("[server] unknown command (use: mode / fault / lagcomp / setlag / preset / hash / quit)")


def _handle_lagcomp_cmd(server, line):
    """切换延迟补偿开关。"""
    parts = line.split()
    if len(parts) != 2 or parts[1] not in ("on", "off"):
        print("[server] usage: lagcomp on|off")
        return
    server.lag_comp_enabled = (parts[1] == "on")
    print("[server] lag compensation = {}".format(server.lag_comp_enabled))


def _handle_setlag_cmd(server, line):
    """设置某客户端的估测网络延迟（毫秒），供 lag comp 回退用。"""
    parts = line.split()
    if len(parts) != 3:
        print("[server] usage: setlag <pid> <ms>")
        return
    pid = _parse_int(parts[1])
    ms = _parse_float(parts[2])
    if pid is None or ms is None:
        print("[server] setlag: pid and ms must be numbers")
        return
    server.client_latency[pid] = ms / 1000.0
    print("[server] client {} latency = {} ms".format(pid, int(ms)))


def _handle_fault_cmd(server, line):
    """解析 ``fault <param> <value>`` 并广播给所有客户端。"""
    parts = line.split()
    if len(parts) != 3:
        print("[server] usage: fault <latency|loss|reorder|duplicate|drop_input|jitter|throttle> <value>")
        return
    param = parts[1]
    value_str = parts[2]
    value = _parse_float(value_str)
    if value is None:
        print("[server] value must be a number")
        return
    valid = {"latency", "loss", "reorder", "duplicate", "drop_input", "jitter", "throttle"}
    if param not in valid:
        print("[server] unknown fault param: {} (valid: {})".format(param, ", ".join(sorted(valid))))
        return
    server._broadcast({"type": P.MSG_FAULT, "param": param, "value": value})
    print("[server] injected fault {}={} to all clients".format(param, value))


def _handle_preset_cmd(server, line):
    """解析 ``preset <perfect|good|general|bad>`` 并广播给所有客户端。"""
    parts = line.split()
    if len(parts) != 2:
        print("[server] usage: preset <perfect|good|general|bad>")
        return
    name = parts[1]
    if name not in F.NETWORK_PRESETS:
        print("[server] unknown preset: {} (valid: {})".format(
            name, ", ".join(sorted(F.NETWORK_PRESETS))))
        return
    server._broadcast({"type": P.MSG_FAULT, "param": "preset", "value": name})
    print("[server] applied network preset {} to all clients".format(name))


def _parse_float(text):
    """把字符串解析为 float，失败返回 None（不用异常控制流）。"""
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(text):
    """把字符串解析为 int，失败返回 None（不用异常控制流）。"""
    try:
        return int(text)
    except ValueError:
        return None


def _safe_input():
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return None


if __name__ == "__main__":
    run()
