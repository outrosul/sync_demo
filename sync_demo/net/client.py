"""客户端：连接服务器、采样输入、按同步方式收发、pygame 渲染。

- 帧同步 lockstep：只发输入，收到 ``frame`` 后用本地 ``sim.tick`` 推演同一世界。
- 状态同步 state sync：发输入，收到 ``snapshot`` 后重建本地显示状态。

渲染只读状态，不参与规则计算。
"""

import ctypes
import os
import random
import select
import socket
import threading
import time
import uuid

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD
from sync_demo.net import correction as CR
from sync_demo.net import fault as F
from sync_demo.net import logic_clock as LC
from sync_demo.net import prediction as PR
from sync_demo.net import protocol as P
from sync_demo.net import smoothing as SM
from sync_demo.net import transport as T

# 每多少帧上报一次本地 world hash（desync 观测）
HASH_REPORT_INTERVAL = 10

# 同步模式的「内部标识符 → 中文显示名」映射。
MODE_LABELS = {"lockstep": "帧同步", "state": "状态同步"}

# 网络分档（极好/好/一般/差），替代「6 个参数逐个自定义」，一键切档。
NET_PRESET_LABELS = {
    "perfect": "极好",
    "good": "好",
    "general": "一般",
    "bad": "差",
}
NET_PRESET_ORDER = ["perfect", "good", "general", "bad"]

# 网速分档当前处于「自定义」还是「某预设」；初始为 perfekta（默认关 = 无故障）
# 用 None 表示「未套预设」（默认正常网络，等价 perfect）
_DEFAULT_NET_PRESET = "perfect"

# 各开关/按钮的悬停提示（tooltip）说明
TOOLTIPS = {
    "lockstep": "帧同步：同步操作指令，各客户端各自确定性推演，流量小但一致性难保证",
    "state": "状态同步：服务器权威计算后下发快照，客户端做表现/插值，反作弊强但流量大",
    "net": "网速分档：一键套用「极好/好/一般/差」四档弱网（延迟/丢包/带宽），点击循环切换",
    "frame_follow": "等帧追帧：帧同步下逻辑帧跟随服务器节奏，收不到帧就等、积压就加速追帧",
    "rollback": "预测回滚：预测偏差超容差时，回滚跳回权威 vs 多帧插值平滑纠正（不回滚）",
    "interp_extrap": "内插外插：远端玩家位置平滑方式——自动按收包间隔切 / 只内插 / 只外推(DR)",
    "render_skip": "渲染跳帧：逻辑每帧照推，渲染每隔 N 帧画一次（减压），点一下切换档位",
    "claim": "争夺标记：分布式授权的授权主控端标记，谁持有谁主控（解决 p2p 无法验证）",
    "snap": "回拉容差：预测与权威偏差超过此像素值才回拉，越大越平滑但越偏权威",
}

# 中文字体候选（Windows / Linux / macOS 常见路径）
_CHINESE_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def load_chinese_font(pygame, size):
    """加载中文字体（微软雅黑等），找不到则回退 pygame 默认字体。"""
    for path in _CHINESE_FONT_CANDIDATES:
        try:
            return pygame.font.Font(path, size)
        except (OSError, FileNotFoundError):
            continue
    return pygame.font.Font(None, size)


# 玩家身体颜色调色板（确定性：由 player_id 取模，所有客户端算出来一致）。
# 顺序固定、只依赖 player_id，不涉及随机 → 各端看到的颜色必定相同。
_PLAYER_COLOR_PALETTE = [
    (70, 180, 230),    # 蓝
    (230, 180, 70),    # 橙
    (180, 120, 230),   # 紫
    (230, 120, 120),   # 红
    (120, 220, 160),   # 绿
    (220, 220, 100),   # 黄
    (160, 160, 200),   # 灰蓝
    (200, 140, 160),   # 粉
]


def player_color(color_index):
    """按「同步来的颜色索引」取调色板 RGB（颜色是同步属性，不是本地推导）。"""
    return _PLAYER_COLOR_PALETTE[int(color_index) % len(_PLAYER_COLOR_PALETTE)]


def _grab_focus(pygame):
    """让游戏窗口主动获得键盘焦点（Windows 用 win32 API，其它平台跳过）。

    为什么需要：pygame 窗口刚创建时键盘焦点可能仍在启动它的终端上，KEYDOWN
    事件全被终端吃掉，游戏里「按什么都没反应」；用户随便按一下窗口才让焦点
    切过来。这里在启动和点击时主动抢一次焦点。
    """
    if not hasattr(ctypes, "windll"):
        return                      # 非 Windows 平台没有 windll，直接跳过
    wm_info = pygame.display.get_wm_info()
    hwnd = wm_info.get("window")
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 1)          # SW_NORMAL：显示/还原窗口
    user32.BringWindowToTop(hwnd)       # 提到 z 序顶层
    user32.SetForegroundWindow(hwnd)    # 置前（Windows 会尽量遵从）
    user32.SetFocus(hwnd)               # 抢键盘焦点
    # 注意：这里**不能**调 pygame.event.pump()——它会在事件循环内部抢先消费/丢弃
    # 事件，导致后续 KEYDOWN 丢失（表现为「点一下窗口后突然按不了键」）。


class Client:
    """一个客户端会话。"""

    def __init__(self, host, port, name, udp_port=None):
        self.host = host
        self.port = port
        # 非重要消息走 KCP over UDP；默认跟随 TCP 端口 +1
        self.udp_port = udp_port if udp_port is not None else port + 1
        self.name = name
        self.sock = None
        self.transport = None            # 双通道 Transport（连接后持有 TCP + UDP 端点）
        self.udp_endpoint = None         # 客户端单一 KCP 端点
        self.player_id = None
        self.mode = "state"
        self.rng = random.Random(20261123)   # 与服务器相同的确定性种子（帧同步需要）
        self.world = G.World(self.rng)       # 本地世界（帧同步自己推演；状态同步按快照重建）
        self.inbuf = b""
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.latest_snapshot = None
        self.fault = F.FaultInjector(random.Random(1234))   # 本客户端的上行故障注入
        self.hash_report_counter = 0
        self.ghost = SM.GhostTracker()       # 服务器权威位置的虚影（滞后插值）
        # 断线重连：进程启动生成一个稳定 token 标识身份，重连时 server 凭它恢复原 player_id
        self.session_token = "{}-{}".format(name, uuid.uuid4().hex[:8])

        # ---- 帧同步（lockstep）的「本地预推演 + 回滚」状态 ----
        # 客户端每次发出输入后，本地立刻 sim.tick 一步先行动（预测），等服务器
        # 广播的 frame 到了，再按 frame 里「实际被采纳的输入序列」回滚重放。这样
        # 按键到画面的延迟只是「发出到本地推演」这 0ms，而不是「绕服务器一圈」的 rtt。
        self.predicted_inputs = []           # 未确认输入流水（本地预测用），元素为 InputState
        self.confirmed_world = None          # 上一个已确认 frame 之后的世界快照（回滚基准）
        self.LOCKSTEP_STEP = 2               # 与服务器 LOCKSTEP_FIXED_STEP 一致（每个 frame 的逻辑步数）
        self.snap_threshold = 0.0            # 回拉容差（像素）：预测与权威偏差 > 此值才回拉；
                                             # 0 = 总是回拉（精确但抖动），越大越平滑（允许小误差）
        self.extrapolate_threshold = 0.3     # 外推阈值（秒）：state 模式远端玩家距上次收包超过
                                             # 此值 → 切到 Dead Reckoning 外推（惯性移动），否则内插
        self.interp_extrap_mode = "auto"     # 内插↔外插切换开关：auto=按收包间隔自动 / interp=只内插 /
                                             # extrap=只外推（DR 一直外推）

        # ---- 预测回滚 vs 预测不回滚 开关 ----
        # 帧同步预测失败时的两种纠偏方案（对应《帧同步手感优化》两种方案）：
        # - True（预测回滚）：偏差超阈值时直接把世界「跳」回权威（我们的 `_rollback_and_replay`）。
        # - False（预测不回滚）：不回滚，用 Corrector 从「当前预测位置」平滑插值到权威（多帧渐进），
        #   把「回滚跳变」软化成「状态同步式的插值纠正」，画面更平滑但略滞后。
        self.predict_rollback_enabled = True
        self.corrector = CR.Corrector()      # 预测不回滚时用于平滑纠偏
        self.correct_frames = 6              # 预测不回滚的纠错帧数（越大越平滑越滞后）
        self.render_skip = 0                 # 渲染跳帧：每 N 帧渲染 1 次（0=每帧渲染，渲染跳帧减压）
        self._frame_counter = 0              # 渲染跳帧计数
        self.owner_id = None                 # 分布式授权：当前持有「授权标记」的玩家（服务器广播）
        self.net_preset = None               # 网速分档：None=自定义 / perfect/good/general/bad（一键切档）

        # ---- 帧同步「等帧 / 追帧」调度器 ----
        # 锁步下客户端逻辑帧应跟随服务器帧节奏：收到的 frame 先入队，由调度器按固定
        # 步长决定「每拍消费几帧」。队列空 → 等帧（0 帧，逻辑停下）；积压 → 追帧
        # （多帧加速消费）。这让网络波动时客户端能正确地「停」或「追」，而非收到即跑。
        self.frame_scheduler = LC.FixedStepScheduler(fixed_step=0.05, max_catch_up=8)
        self.use_frame_scheduler = False   # 等帧/追帧开关：默认关（保持「收到即推演」）

        # pygame 组件（延迟初始化，避免无显示环境时 import 失败）
        self.pygame = None
        self.screen = None
        self.font = None            # HUD 用字体（pygame 内置默认字体，处处可用）
        self._hud = None            # HUD 控件几何布局缓存（每帧重算）
        self._fps = 0.0             # 最近一帧 FPS（仅表现层显示用）
        self._key_state_fn = None   # 可注入的按键状态源（测试用；None=走事件驱动 held_keys）
        self._held_keys = {}        # KEYDOWN/KEYUP 事件维护的按键状态（keycode → bool）
        self._focused = True        # 窗口是否获得键盘焦点（失焦时清空按键状态）
        self._last_key_seen = (0, 0)  # 最近一次 KEYDOWN 的 (keycode, 累计次数)，诊断用
        self._prev_attack = False   # 边沿检测：上一帧 attack 状态（RPC 技能事件用）
        self._prev_dash = False     # 边沿检测：上一帧 dash 状态
        self._prev_freeze = False   # 边沿检测：上一帧 freeze 状态

    # ---- 生命周期 ----

    def connect(self):
        # 用 Transport.connect_client 建立 TCP + UDP 双连接；conv 先用固定值 1，
        # 收到 welcome 后按服务器分配的 conv 修正（保证多客户端按 conv 正确路由）。
        self.transport = T.Transport.connect_client(
            self.host, self.port, self.udp_port, conv=1)
        self.sock = self.transport.tcp_sock
        self.udp_endpoint = self.transport.udp_endpoint
        # 带上 session_token：断线重连时服务器凭它识别「是不是之前那个玩家」，恢复原身份
        self._send({"type": P.MSG_HELLO, "name": self.name,
                    "session_token": self.session_token})
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _fix_udp_conv(self, udp_port, conv):
        """按服务器分配的 conv / udp_port 修正本地 KCP 端点（此时尚未发 UDP 段，安全）。"""
        if self.udp_endpoint is None:
            return
        self.udp_endpoint.conv = conv
        self.udp_endpoint.kcp.conv = conv
        self.udp_endpoint.addr = (self.host, udp_port)

    def close(self):
        """关闭连接：停止线程 + 关 TCP socket（触发服务器感知断线，走重连宽限）。"""
        self.running = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _run(self):
        last_ms = time.monotonic()
        while self.running:
            # 推进故障注入器的虚拟时钟（上行/下行延迟共用）
            now_ms = time.monotonic()
            self.fault.tick(int((now_ms - last_ms) * 1000))
            last_ms = now_ms
            # 重要消息走 TCP
            readable, _, _ = select.select([self.sock], [], [], 0.02)
            if readable:
                data = None
                try:
                    data = self.sock.recv(65536)
                except OSError:
                    data = None   # socket 被 close() 关闭 → 视为断开，退出循环
                if not data:
                    self.running = False
                    break
                self.inbuf += data
                messages, self.inbuf = P.decode(self.inbuf)
                for msg in messages:
                    self._receive(msg)
            # 非重要消息走 KCP over UDP
            self._poll_udp()
            # 下行延迟到期的消息，此刻交货
            self._drain_downlink()

    def _poll_udp(self):
        """读 UDP → 本地 KCP 端点，驱动更新并交付非重要消息。"""
        if self.udp_endpoint is None:
            return
        while True:
            try:
                data, _ = self.udp_endpoint.sock.recvfrom(65536)
            except BlockingIOError:
                break
            except OSError:
                break
            self.udp_endpoint.feed(data)
        self.udp_endpoint.update()
        chunk = self.udp_endpoint.recv(65536)
        while chunk:
            for msg in P.decode_all(chunk):
                self._receive(msg)
            chunk = self.udp_endpoint.recv(65536)

    def _receive(self, msg):
        """下行故障注入：对收到的服务器消息做丢包 / 延迟处理后投递。

        丢包走 ``fault.send`` 的随机丢弃语义；延迟走 ``fault.delay`` 压入其延迟
        队列，由 ``_run`` 轮询 ``_drain_downlink`` 到期交货（不阻塞渲染线程）。
        默认故障全关时消息直接进入 ``_handle``。
        """
        ok, _ = self.fault.send(msg)
        if not ok:
            return
        if self.fault.latency_ms > 0 or self.fault.jitter_ms > 0:
            self.fault.delay(msg)
        else:
            self._handle(msg)

    def _drain_downlink(self):
        """把下行延迟队列里到期的消息交给 ``_handle``。"""
        for msg in self.fault.pending_deliveries():
            self._handle(msg)

    def _send(self, msg):
        """发送消息：按重要性分流，输入类消息经故障注入。"""
        if self.sock is None:
            return
        if msg.get("type") == P.MSG_INPUT:
            # 输入走 send_input 语义：可能重复 / 丢失
            out = self.fault.send_input(msg)
            for m in out:
                self._send_via_channel(m)
        else:
            self._send_via_channel(msg)

    def _send_via_channel(self, msg):
        """按 classify 分流：非重要走 KCP/UDP，重要走 TCP。"""
        if T.classify(msg):
            # UDP 端点可能要等 welcome 后才建立；尚未建立时回退 TCP
            if self.udp_endpoint is None:
                self._sock_send(P.encode(msg))
                return
            self.udp_endpoint.send(P.encode(msg))
        else:
            self._sock_send(P.encode(msg))

    def _sock_send(self, data):
        try:
            self.sock.send(data)
        except OSError:
            pass

    # ---- 消息处理 ----

    def _handle(self, msg):
        mtype = msg.get("type")
        if mtype == P.MSG_WELCOME:
            self.player_id = msg.get("player_id")
            self.mode = msg.get("mode", self.mode)
            # 拿到服务器分配的 conv / udp_port 后修正本地 KCP 端点
            if self.udp_endpoint is not None:
                self._fix_udp_conv(msg.get("udp_port", self.udp_port), msg.get("conv", 1))
            self._apply_snapshot(msg.get("snapshot"))
        elif mtype == P.MSG_FRAME:
            # 帧同步（lockstep）的核心约定：服务器不广播位置，只广播「这一帧
            # 所有人按了什么键」。位置是「确定性函数」的结果——同样的输入喂给
            # 同样的 sim.tick 一定得到同样的世界，所以各客户端各自算一遍就能
            # 和服务器、和其他玩家完全一致，根本不需要复制别人的坐标。
            inputs = {}
            for k, tup in (msg.get("inputs") or {}).items():
                _mk = sim.InputState(*tup)
                inputs[int(k)] = _mk
            if self.use_frame_scheduler:
                # 等帧/追帧模式：帧先入调度器队列，由主循环按固定步长消费。
                # 队列空时逻辑停下「等帧」，积压时「追帧」——网络波动下正确节奏。
                self.frame_scheduler.push(inputs)
            else:
                # 默认：收到即回滚重放（保留原有行为）
                self._rollback_and_replay(inputs)
                self._maybe_report_hash()
        elif mtype == P.MSG_SNAPSHOT:
            # 状态同步（state sync）则相反：服务器是唯一权威，直接下发「世界
            # 现在的完整样子」。本地无需也「不该」自己算——因为本地没有权威状态，
            # 也算不准；直接把快照盖上去重建即可，避免本地预测状态和权威状态
            # 各自越积越偏。
            self._apply_snapshot(msg)
        elif mtype == P.MSG_MODE:
            self.mode = msg.get("mode", self.mode)
        elif mtype == P.MSG_FAULT:
            self._apply_fault(msg)
        elif mtype == P.MSG_OWNER:
            self.owner_id = msg.get("owner_id")

    def claim_owner(self):
        """请求争夺「授权标记」（分布式授权）。"""
        self._send({"type": P.MSG_CLAIM})

    def _apply_fault(self, msg):
        """按服务器下发的故障参数配置本客户端的注入器。"""
        name = msg.get("param")
        value = msg.get("value")
        if name == "latency":
            self.fault.set_latency(value)
        elif name == "loss":
            self.fault.set_loss(value)
        elif name == "reorder":
            self.fault.set_reorder(value)
        elif name == "duplicate":
            self.fault.set_duplicate(value)
        elif name == "drop_input":
            self.fault.set_drop_input(value)
        elif name == "jitter":
            self.fault.set_jitter(value)
        elif name == "throttle":
            self.fault.set_throttle(value)
        elif name == "preset":
            self.fault.apply_preset(str(value))

    def _maybe_report_hash(self):
        """帧同步模式下，每 N 帧上报本地世界 hash 给服务器做 desync 观测。"""
        if self.mode != "lockstep":
            return
        self.hash_report_counter += 1
        if self.hash_report_counter % HASH_REPORT_INTERVAL != 0:
            return
        h = SD.world_hash(self.world)
        self._send({"type": P.MSG_HASH, "tick": self.world.tick, "hash": h})

    def _apply_snapshot(self, snap):
        if not snap:
            return
        with self.lock:
            self.latest_snapshot = snap
            self.world.tick = snap.get("tick", 0)
            # 「重建」而非「逐字段差分合并」是关键：快照是服务器拍下的完整世界
            # 状态，直接整体替换本地 players 字典，能一次清除掉本地可能残留的、
            # 已失效的旧实体，避免服务器已删掉/复活的对象在客户端「幽灵残留」。
            # 若改成逐个 merge，任何两端状态不一致都会累积且难以察觉。
            # 重建玩家：快照里 x/y/vx/vy 都是定点原始整数(raw)，需还原成 Fixed
            self.world.players = {}
            for pd in snap.get("players", []):
                ps = G.PlayerState(pd["player_id"], pd.get("name", ""), pd["x"], pd["y"])
                ps.vx = G.Fixed(pd["vx"])
                ps.vy = G.Fixed(pd["vy"])
                ps.facing = pd["facing"]
                ps.on_ground = pd["on_ground"]
                ps.hp = pd["hp"]
                ps.alive = pd["alive"]
                ps.attack_timer = pd.get("attack_timer", 0)
                ps.attack_cooldown = pd.get("attack_cooldown", 0)
                ps.hit_facing = pd.get("hit_facing", 1)
                ps.hit_active = pd.get("hit_active", False)
                ps.attack_anim = pd.get("attack_anim", 0)
                ps.hit_anim = pd.get("hit_anim", 0)
                ps.dash_timer = pd.get("dash_timer", 0)
                ps.dash_dir = pd.get("dash_dir", 1)
                ps.dash_cooldown = pd.get("dash_cooldown", 0)
                ps.frozen_timer = pd.get("frozen_timer", 0)
                ps.freeze_cooldown = pd.get("freeze_cooldown", 0)
                ps.color_index = pd.get("color_index", 0)
                self.world.players[ps.player_id] = ps
            self.world.projectiles = {}
            # 快照同时是「虚影」的数据源：把服务器这帧认为的每个玩家位置喂给
            # ghost 追踪器。ghost 会在时间轴上插值出一份「滞后于最新、但更平滑」
            # 的位置，渲染远端玩家时用它画半透明轮廓，避免快照跳变造成的抖动。
            self.ghost.push_snapshot(snap.get("players", []))
            # 关键：快照是服务器权威世界，重建后的 world 必须同步为新的「确认基线」。
            # 否则锁步下后续 frame 回滚会从「旧的 confirmed_world（缺新加入的玩家）」
            # 出发，把刚补发快照加进去的新玩家又「回滚掉」——这正是老客户端永远缺
            # 新玩家、hash 永久不同步的根因。
            self.confirmed_world = PR.snapshot_world(self.world)

    # ---- 输入采样 ----

    def send_input(self, left, right, jump, attack, dash, freeze):
        inp = sim.InputState(left, right, jump, attack, dash, freeze)
        self._send({"type": P.MSG_INPUT, "left": left, "right": right,
                    "jump": jump, "attack": attack, "dash": dash, "freeze": freeze})
        # 帧同步（lockstep）下做「本地预推演」：发出输入后立刻对本地点 tick 一步，
        # 画面即时响应，不用等服务器 frame 绕一圈回来。这一步是「预测」；等 frame
        # 到了若发现输入没被服务器采纳，再回滚（见 _handle 的 MSG_FRAME 分支）。
        if self.mode == "lockstep":
            self._predict_local(inp)

    def _predict_local(self, inp):
        """本地预测：从「已确认基线」重放所有未确认输入，得到预测世界。

        关键：预测不是在当前 world 上盲目 tick+1（那样会与服务器 +2 步长错位，
        正是之前 c1 比服务器少 2 tick 的根因）。而是每次都以 confirmed_world 为
        起点，把「已发出但服务器尚未确认」的输入按序重放一遍，得到即时显示用的
        预测世界。这样 world.tick 始终 = confirmed 基准 + 预测步数，与服务器对齐。
        """
        # 从确认基线出发（首帧尚未有基线则用当前世界）
        if self.confirmed_world is not None:
            base = PR.snapshot_world(self.confirmed_world)
        else:
            base = PR.snapshot_world(self.world)
        # 追加本次输入到未确认流水
        self.predicted_inputs.append(inp)
        # 重放：每个未确认输入推进 LOCKSTEP_FIXED_STEP 步（与服务器 frame 步长一致）
        for pinp in self.predicted_inputs:
            for _ in range(self.LOCKSTEP_STEP):
                sim.tick(base, {self.player_id: pinp})
        self.world = base

    def _rollback_and_replay(self, frame_inputs):
        """帧同步的「回拉」：回滚到已确认基线，再按服务器帧输入重放。

        带「误差容忍」：每次 frame 到来都无条件回拉会让画面频繁跳（哪怕偏差只有
        1 像素）。所以先算出「权威重放世界」与「本地当前预测世界」的本地玩家位置
        偏差，偏差 ≤ ``snap_threshold`` 时**只更新确认基线、不把显示世界拉回去**
        （保持预测位置，画面平滑）；只有偏差超过阈值才真正回拉。这样在「小误差
        保持平滑」与「大误差纠偏」之间取得可调平衡——阈值越大画面越稳但越偏权威，
        阈值为 0 时等价于「总是精确回拉」。
        """
        # ① 从确认基线出发，用官方本帧输入推演出「权威世界」（与服务器一致）
        if self.confirmed_world is not None:
            authoritative = PR.snapshot_world(self.confirmed_world)
        else:
            authoritative = PR.snapshot_world(self.world)
        for _ in range(self.LOCKSTEP_STEP):
            sim.tick(authoritative, frame_inputs)

        # ② 计算本地玩家位置偏差（权威 vs 当前预测世界）
        deviation = self._deviation_px(authoritative, self.world)

        # ③ 偏差超过阈值才纠偏；否则保留预测世界，只让权威成为新的确认基线
        if self.snap_threshold <= 0.0 or deviation > self.snap_threshold:
            if self.predict_rollback_enabled:
                # 预测回滚：直接把世界「跳」回权威（跳变，但精确）
                self.world = authoritative
            else:
                # 预测不回滚：不回滚，改用 Corrector 从「当前预测位置」平滑插值到
                # 权威位置（多帧渐进）。把「回滚跳变」软化为「插值纠正」，画面平滑。
                self._start_smooth_correction(authoritative)
        # 无论回滚与否，「确认基线」都要前进到新权威（后续预测都从这里重放）
        self.confirmed_world = PR.snapshot_world(authoritative)
        # 预测流水已通过重放落定，清空
        self.predicted_inputs = []
        # 喂 ghost（锁步下本地世界即权威位置）
        self._feed_ghost_from_world()

    def _start_smooth_correction(self, authoritative):
        """预测不回滚：启动 Corrector，让本地玩家从「当前预测位置」平滑过渡到权威位置。

        取本地玩家在两个世界里的位置作为起止点，用 N 帧插值（纠错帧数可调，这里
        固定一个平滑值，见 self.correct_frames）。Corrector 由主循环每帧 step 驱动。
        """
        if self.player_id is None:
            self.world = authoritative
            return
        cur = self.world.players.get(self.player_id)
        target = authoritative.players.get(self.player_id)
        if cur is None or target is None:
            self.world = authoritative
            return
        from_pos = (cur.x, cur.y)
        to_pos = (target.x, target.y)
        self.corrector.start_correction(from_pos, to_pos, frames=self.correct_frames)
        # 世界其余部分（血条/状态）仍用权威，只有本地玩家位置走平滑纠正
        self.world = authoritative

    def _deviation_px(self, world_a, world_b):
        """计算「本地玩家」在 world_a 与 world_b 之间的位置偏差（像素）。

        用欧氏距离，取绝对值（定点转浮点后再算，仅用于阈值比较，不进逻辑运算）。
        只比本地玩家自己：远端玩家的偏差由各自客户端自己观测，本地只关心「我」
        的预测是否漂了。
        """
        if self.player_id is None:
            return 0.0
        pa = world_a.players.get(self.player_id)
        pb = world_b.players.get(self.player_id)
        if pa is None or pb is None:
            return 0.0
        dx = pa.x.to_float() - pb.x.to_float()
        dy = pa.y.to_float() - pb.y.to_float()
        return (dx * dx + dy * dy) ** 0.5

    def _feed_ghost_from_world(self):
        """把本地世界当前位置喂给 ghost 追踪器（frame 模式用）。"""
        players = [
            {"player_id": ps.player_id, "x": ps.x.raw, "y": ps.y.raw}
            for ps in self.world.players.values()
        ]
        self.ghost.push_snapshot(players)

    # ---- HUD ----

    def _hud_layout(self, pygame):
        """计算 HUD 控件几何（全部中文显示）。

        开关按「适用模式」分组：帧同步专属（等帧追帧/预测回滚/渲染跳帧）、状态同步专属
        （内插外插）、通用（争夺标记）。渲染时按当前 self.mode 只显示适用的开关。

        返回 dict：``mode``、``net``、``switches``（(key, rect, 适用模式集合)）、``snap``。
        """
        layout = {"mode": [], "net": None, "switches": [], "snap": None}
        # 第一行：同步模式（帧同步 / 状态同步）
        mx = 10
        for mode in ("lockstep", "state"):
            layout["mode"].append((pygame.Rect(mx, 10, 88, 24), mode))
            mx += 96
        # 第二行：网速分档（一键切档，通用）
        layout["net"] = pygame.Rect(10, 42, 184, 24)
        y = 74
        # 开关组：每个 (key, rect, 适用模式集合)。位置固定，渲染时按 mode 过滤。
        # 适用模式集合用 "lockstep" / "state"；空集合 = 通用（都显示）。
        switch_specs = [
            ("frame_follow", "等帧追帧", {"lockstep"}),
            ("rollback", "预测回滚", {"lockstep"}),
            ("interp_extrap", "内插外插", {"state"}),
            ("render_skip", "渲染跳帧", {"lockstep"}),
            ("claim", "争夺标记", set()),
        ]
        for key, _label, modes in switch_specs:
            layout["switches"].append((key, pygame.Rect(10, y, 184, 24), modes))
            y += 28
        # 回拉容差（snap）单独一行（通用）
        dec_rect = pygame.Rect(140, y, 24, 24)
        inc_rect = pygame.Rect(170, y, 24, 24)
        layout["snap"] = ("snap", dec_rect, inc_rect)
        return layout

    def _all_switches(self):
        """返回所有开关 [(key, rect), ...]（布局固定，全部显示，禁用态用灰色）。"""
        return [(key, rect) for key, rect, _modes in self._hud["switches"]]

    def _switch_enabled(self, key):
        """判断某开关在当前模式下是否可用（不可用则置灰、不响应点击）。"""
        for k, _rect, modes in self._hud["switches"]:
            if k == key:
                # modes 空 = 通用（始终可用）；否则当前模式必须在 modes 里
                return (not modes) or (self.mode in modes)
        return True

    def _handle_click(self, pos):
        """处理 HUD 面板的左键点击（模式切换 / 网速分档 / 开关 / 回拉容差）。"""
        if self._hud is None or self.pygame is None:
            return
        for rect, mode in self._hud["mode"]:
            if rect.collidepoint(pos):
                self._request_mode(mode)
                return
        # 网速分档
        net = self._hud.get("net")
        if net is not None and net.collidepoint(pos):
            self._cycle_net_preset()
            return
        # 开关组（禁用的开关不响应点击）
        for key, rect in self._all_switches():
            if rect.collidepoint(pos):
                if self._switch_enabled(key):
                    self._toggle_switch(key)
                return
        # 回拉容差行
        snap = self._hud["snap"]
        if snap is not None:
            _name, dec_rect, inc_rect = snap
            if dec_rect.collidepoint(pos):
                self._apply_snap_incr(-1)
                return
            if inc_rect.collidepoint(pos):
                self._apply_snap_incr(1)
                return

    def _toggle_switch(self, key):
        """按 key 切换对应开关。"""
        if key == "frame_follow":
            self.use_frame_scheduler = not self.use_frame_scheduler
        elif key == "rollback":
            self._toggle_rollback()
        elif key == "interp_extrap":
            self._toggle_interp_extrap()
        elif key == "render_skip":
            self._toggle_render_skip()
        elif key == "claim":
            self.claim_owner()

    def _cycle_net_preset(self):
        """一键切网速分档：极好 → 好 → 一般 → 差 → 极好 …"""
        if self.net_preset is None:
            self.net_preset = NET_PRESET_ORDER[0]
        idx = NET_PRESET_ORDER.index(self.net_preset)
        self.net_preset = NET_PRESET_ORDER[(idx + 1) % len(NET_PRESET_ORDER)]
        self.fault.apply_preset(self.net_preset)

    def _toggle_render_skip(self):
        """循环切换渲染跳帧：0（每帧）→ 1（隔帧）→ 2（隔两帧）→ 0 …"""
        self.render_skip = (self.render_skip + 1) % 3

    def _toggle_interp_extrap(self):
        """循环切换「内插↔外插」模式：auto → interp → extrap → auto …"""
        modes = ["auto", "interp", "extrap"]
        idx = modes.index(self.interp_extrap_mode) if self.interp_extrap_mode in modes else 0
        self.interp_extrap_mode = modes[(idx + 1) % len(modes)]

    def _toggle_rollback(self):
        """切换「预测回滚 / 预测不回滚」。"""
        self.predict_rollback_enabled = not self.predict_rollback_enabled

    def _apply_snap_incr(self, delta):
        """调整回拉容差（snap threshold），步进 2 像素，下限 0。"""
        self.snap_threshold = max(0.0, self.snap_threshold + delta * 2.0)

    def _request_mode(self, mode):
        """请求服务器切换同步模式（服务器校验后广播 MSG_MODE 生效）。"""
        self._send({"type": P.MSG_MODE_REQ, "mode": mode})

    # ---- 渲染 ----

    def run_gui(self):
        """在主线程启动 pygame 主循环（只在客户端进程调用）。"""
        import pygame
        self.pygame = pygame
        pygame.init()
        self.screen = pygame.display.set_mode((G.MAP_WIDTH, G.MAP_HEIGHT))
        pygame.display.set_caption("同步对战演示 - 客户端 {}".format(self.player_id))
        # 关键：关闭 SDL 的文本输入（IME）模式。开启时字母键会被输入法接管，
        # KEYDOWN 事件可能被抑制或延迟（表现为「字母键没反应、只有空格/方向键能用」）。
        # 本 demo 只需要物理按键，不需要文本输入，所以显式关掉。
        pygame.key.stop_text_input()
        # 主动把窗口置顶并抢键盘焦点：否则窗口刚打开时焦点可能在终端上，
        # 按键事件全发给了终端，游戏收不到（表现为「按什么都没反应」）。
        _grab_focus(pygame)
        clock = pygame.time.Clock()
        self._hud = self._hud_layout(pygame)
        self.font = load_chinese_font(pygame, 16)
        self._focused = True

        while self.running:
            # 先 pump：让 SDL 把系统消息（含键盘事件）拉进 pygame 事件队列。
            # 放在循环最前面是安全用法（不在事件处理中间调），确保按键能及时到达。
            pygame.event.pump()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_click(event.pos)
                # 窗口焦点变化：失焦时清空按键状态（避免 KEYUP 被系统吞掉导致按键卡住）。
                # 重获焦点时**不清空**——否则按住键的瞬间被清掉，表现为「突然不能动」。
                elif event.type == pygame.WINDOWFOCUSGAINED:
                    self._focused = True
                elif event.type == pygame.WINDOWFOCUSLOST:
                    self._focused = False
                    self._held_keys.clear()
                # 按键状态：用 KEYDOWN/KEYUP 事件维护字典，方向键/Shift/字母键统一，
                # 不依赖 get_pressed 的 keycode↔scancode 混淆（那个会让方向键永久失效）
                elif event.type == pygame.KEYDOWN:
                    self._held_keys[event.key] = True
                    self._last_key_seen = (event.key, self._last_key_seen[1] + 1)
                elif event.type == pygame.KEYUP:
                    self._held_keys[event.key] = False

            # 按键采样：事件驱动 held_keys（KEYDOWN/KEYUP 的 event.key 是 keycode，
            # 字典查找可靠）。不用 get_pressed 按 keycode 索引——pygame 的 get_pressed
            # 返回 ScancodeWrapper，按 scancode 索引，用 keycode 查恒为 False。
            if self._key_state_fn is not None:
                keys = self._key_state_fn()
                left = bool(keys.get(pygame.K_a) or keys.get(pygame.K_LEFT))
                right = bool(keys.get(pygame.K_d) or keys.get(pygame.K_RIGHT))
                jump = bool(keys.get(pygame.K_w) or keys.get(pygame.K_UP) or keys.get(pygame.K_SPACE))
                attack = bool(keys.get(pygame.K_j) or keys.get(pygame.K_z))
                dash = bool(keys.get(pygame.K_k) or keys.get(pygame.K_LSHIFT))
                freeze = bool(keys.get(pygame.K_l) or keys.get(pygame.K_x))
            else:
                hk = self._held_keys
                left = hk.get(pygame.K_a, False) or hk.get(pygame.K_LEFT, False)
                right = hk.get(pygame.K_d, False) or hk.get(pygame.K_RIGHT, False)
                jump = hk.get(pygame.K_w, False) or hk.get(pygame.K_UP, False) or hk.get(pygame.K_SPACE, False)
                attack = hk.get(pygame.K_j, False) or hk.get(pygame.K_z, False)
                dash = hk.get(pygame.K_k, False) or hk.get(pygame.K_LSHIFT, False)
                freeze = hk.get(pygame.K_l, False) or hk.get(pygame.K_x, False)
            # 渲染循环每帧都采样键鼠并上报输入：客户端永远「只发输入、不发位置」，
            # 位置由 sim.tick（锁步）或服务器快照（状态同步）决定，客户端无权
            # 直接写死坐标——这是本 demo 两条同步路线的共同前提，少了它同步就崩了。
            self.send_input(left, right, jump, attack, dash, freeze)

            # 技能是「离散事件」不是「每帧状态」：攻击/冲刺/冰冻用「按键上升沿」触发
            # 一次 RPC 事件（走可靠通道），避免「每帧 input 覆盖」把瞬时的攻击按键吞掉。
            # （移动/跳跃是连续状态，仍走每帧 MSG_INPUT。）
            if attack and not self._prev_attack:
                self._send({"type": P.MSG_ATTACK, "target_id": None})
            if dash and not self._prev_dash:
                self._send({"type": P.MSG_DASH})
            if freeze and not self._prev_freeze:
                self._send({"type": P.MSG_FREEZE})
            self._prev_attack = attack
            self._prev_dash = dash
            self._prev_freeze = freeze

            # 等帧/追帧模式：每拍从调度器队列消费帧（空=等帧，多帧=追帧），
            # 让帧同步逻辑帧真正跟随服务器节奏（而非收到即推演）。
            if self.use_frame_scheduler and self.mode == "lockstep":
                consumed = self.frame_scheduler.step()
                for inputs in consumed:
                    self._rollback_and_replay(inputs)
                    self._maybe_report_hash()

            # 预测不回滚：驱动 Corrector 平滑纠偏（多帧插值逼近权威，而非跳变）
            self._apply_correction_step()

            self._fps = clock.get_fps()
            # 渲染跳帧：逻辑（上面输入采样/等帧追帧/纠错）每帧照做，渲染可按
            # render_skip 跳过几帧，模拟「渲染帧跳帧减压、逻辑帧照推」的分离。
            # 0 = 每帧渲染；N = 每 N+1 帧渲染 1 次。
            self._frame_counter += 1
            should_render = (self.render_skip == 0 or
                             self._frame_counter % (self.render_skip + 1) == 0)
            if should_render:
                self._draw()
                pygame.display.flip()
            clock.tick(30)

        pygame.quit()

    def _apply_correction_step(self):
        """每帧驱动 Corrector：把本地玩家位置平滑逼近权威位置（预测不回滚用）。"""
        if not self.corrector.is_correcting() or self.player_id is None:
            return
        pos = self.corrector.step()
        if pos is None:
            return
        player = self.world.players.get(self.player_id)
        if player is not None:
            player.x = pos[0]
            player.y = pos[1]

    def _draw(self):
        if self.screen is None:
            return
        pygame = self.pygame
        self.screen.fill((30, 30, 40))
        # 地板
        pygame.draw.rect(self.screen, (90, 90, 100),
                         (0, G.GROUND_Y, G.MAP_WIDTH, G.MAP_HEIGHT - G.GROUND_Y))
        with self.lock:
            players = list(self.world.players.values())
            projectiles = list(self.world.projectiles.values())
        for ps in players:
            if not ps.alive:
                continue
            px = ps.x.to_int()
            py = ps.y.to_int()
            self._draw_player(pygame, ps, px, py)
            # 远端玩家额外画一个「权威虚影」：本地玩家靠 sim.tick 自己预测，
            # 位置永远即时、连续；远端玩家只有服务器拍下的离散快照，直接拿最新
            # 快照画会一步一跳。于是用 ghost 落后一帧的插值位置画一个半透明轮廓，
            # 视觉上「服务器认为他在那」的虚影与实心身体之间有了缓冲，抖动更少。
            if ps.player_id != self.player_id:
                self._draw_ghost(pygame, ps)
        for p in projectiles:
            pygame.draw.circle(self.screen, (240, 200, 60),
                               (p.x.to_int(), p.y.to_int()), 5)
        self._draw_hud(pygame, players)

    def _draw_hud(self, pygame, players):
        """画调试 HUD：左上按钮面板 + 右上调试信息 + 底部按键提示。

        HUD 全是「观测/调试面」，不参与世界规则：因为渲染只能读状态，任何调试
        信息都从这里看出——FPS 看渲染是否卡顿、mode/tick 看同步是否对齐、故障
        参数看当前注入的网损，这些都没法从纯逻辑层直接观察到。
        """
        if self.font is None:
            self.font = load_chinese_font(pygame, 16)
        if self._hud is None:
            self._hud = self._hud_layout(pygame)
        self._draw_hud_panel(pygame)
        self._draw_hud_debug(pygame, players)
        self._draw_hud_hints(pygame)

    def _draw_hud_panel(self, pygame):
        """画左上角按钮面板（全中文）：模式单选 + 网速分档 + 开关组 + 回拉容差 + 悬停提示。"""
        # 半透明底色
        panel = pygame.Surface((210, 220), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        self.screen.blit(panel, (4, 6))
        mouse = pygame.mouse.get_pos()
        hover_tip = None
        # 模式切换（帧同步 / 状态同步）：单选，选中项高亮 + ● 标记
        for rect, mode in self._hud["mode"]:
            active = (mode == self.mode)
            if active:
                color = (60, 160, 60)
                border = (120, 255, 120)
            else:
                color = (50, 50, 70)
                border = (120, 120, 120)
            pygame.draw.rect(self.screen, color, rect)
            pygame.draw.rect(self.screen, border, rect, 2 if active else 1)
            label = ("● " if active else "  ") + MODE_LABELS.get(mode, mode)
            self._blit_text(pygame, label, rect.centerx, rect.centery, (255, 255, 255))
            if rect.collidepoint(mouse):
                hover_tip = TOOLTIPS.get(mode)
        # 网速分档（一键切档）
        net = self._hud.get("net")
        if net is not None:
            label = "网速:{}".format(NET_PRESET_LABELS.get(self.net_preset, "极好"))
            pygame.draw.rect(self.screen, (60, 60, 90), net)
            pygame.draw.rect(self.screen, (200, 200, 200), net, 1)
            self._blit_text(pygame, label, net.centerx, net.centery, (255, 255, 255))
            if net.collidepoint(mouse):
                hover_tip = TOOLTIPS.get("net")
        # 开关组（禁用的开关置灰、不可操作）
        for key, rect in self._all_switches():
            state, color = self._switch_state(key)
            enabled = self._switch_enabled(key)
            if not enabled:
                # 不适用于当前模式：置灰，文字变暗，边框淡化
                color = (45, 45, 55)
                border_color = (70, 70, 70)
                text_color = (120, 120, 120)
            else:
                border_color = (200, 200, 200)
                text_color = (255, 255, 255)
            pygame.draw.rect(self.screen, color, rect)
            pygame.draw.rect(self.screen, border_color, rect, 1)
            self._blit_text(pygame, state, rect.centerx, rect.centery, text_color)
            if rect.collidepoint(mouse) and enabled:
                hover_tip = TOOLTIPS.get(key)
        # 回拉容差
        snap = self._hud["snap"]
        if snap is not None:
            _name, dec_rect, inc_rect = snap
            self._blit_text(pygame, "容差", 10, dec_rect.centery, (210, 210, 210), left=True)
            self._draw_button(pygame, dec_rect, "-")
            self._draw_button(pygame, inc_rect, "+")
            self._blit_text(pygame, "{:.0f}px".format(self.snap_threshold),
                            95, dec_rect.centery, (255, 240, 160), left=True)
            if dec_rect.collidepoint(mouse) or inc_rect.collidepoint(mouse):
                hover_tip = TOOLTIPS.get("snap")
        # 悬停提示 tooltip
        if hover_tip is not None:
            self._draw_tooltip(pygame, hover_tip, mouse)

    def _draw_tooltip(self, pygame, text, mouse_pos):
        """在鼠标旁画一个半透明的说明提示框（tooltip）。"""
        surf = self.font.render(text, True, (255, 255, 255))
        pad = 6
        tw = surf.get_width() + pad * 2
        th = surf.get_height() + pad * 2
        tx = min(mouse_pos[0] + 14, G.MAP_WIDTH - tw - 4)
        ty = min(mouse_pos[1] + 14, G.MAP_HEIGHT - th - 4)
        box = pygame.Surface((tw, th), pygame.SRCALPHA)
        box.fill((20, 20, 30, 220))
        self.screen.blit(box, (tx, ty))
        self.screen.blit(surf, (tx + pad, ty + pad))

    def _switch_state(self, key):
        """返回开关按钮的「中文状态文本 + 底色」。"""
        if key == "frame_follow":
            return ("等帧追帧:开" if self.use_frame_scheduler else "等帧追帧:关",
                    (90, 170, 90) if self.use_frame_scheduler else (70, 70, 90))
        if key == "rollback":
            return ("预测回滚:开" if self.predict_rollback_enabled else "预测回滚:关",
                    (90, 170, 90) if self.predict_rollback_enabled else (70, 70, 90))
        if key == "interp_extrap":
            label = {"auto": "内插外插:自动", "interp": "内插外插:内插",
                     "extrap": "内插外插:外推"}.get(self.interp_extrap_mode, "内插外插")
            return (label, (70, 70, 90))
        if key == "render_skip":
            label = "渲染跳帧:{}".format("每帧" if self.render_skip == 0 else
                                         "隔{}帧".format(self.render_skip))
            return (label, (90, 170, 90) if self.render_skip == 0 else (70, 70, 90))
        if key == "claim":
            mine = (self.owner_id == self.player_id)
            return ("争夺标记:我" if mine else "争夺标记",
                    (90, 170, 90) if mine else (70, 70, 90))
        return (key, (70, 70, 90))

    def _draw_button(self, pygame, rect, label):
        """画一个 +/- 小按钮（圆角底色 + 描边 + 文字）。"""
        pygame.draw.rect(self.screen, (60, 60, 80), rect)
        pygame.draw.rect(self.screen, (200, 200, 200), rect, 1)
        self._blit_text(pygame, label, rect.centerx, rect.centery, (255, 255, 255))

    def _draw_hud_debug(self, pygame, players):
        """画右上角调试信息（全中文）：帧率 / 模式 / tick / 玩家数 / 网速 / 容差 / 当前按键。"""
        mode_label = MODE_LABELS.get(self.mode, self.mode)
        net_label = NET_PRESET_LABELS.get(self.net_preset, "自定义")
        keys_label = self._held_keys_text()
        focus_label = "有焦点" if self._focused else "无焦点(点窗口)"
        last_key_code, last_key_count = self._last_key_seen
        if last_key_count > 0:
            last_key_name = pygame.key.name(last_key_code)
            last_key_label = "{}#{}".format(last_key_name, last_key_count)
        else:
            last_key_label = "未收到"
        lines = [
            "帧率 {:.0f}".format(self._fps),
            "模式 {}".format(mode_label),
            "tick {}".format(self.world.tick),
            "玩家 {}".format(len(players)),
            "网速 {}".format(net_label),
            "容差 {:.0f}px".format(self.snap_threshold),
            "按键 {}".format(keys_label),
            "键盘 {}".format(focus_label),
            "最近键 {}".format(last_key_label),
        ]
        y = 10
        for text in lines:
            self._blit_text(pygame, text, G.MAP_WIDTH - 8, y, (200, 220, 255), right=True)
            y += 18

    def _held_keys_text(self):
        """当前按下的键（调试用）：把 held_keys 里 True 的 keycode 映射回名字。"""
        pygame = self.pygame
        if pygame is None:
            return "-"
        # 关注的操作键 → 显示名
        mapping = [
            (pygame.K_a, "A"), (pygame.K_d, "D"), (pygame.K_w, "W"),
            (pygame.K_j, "J"), (pygame.K_k, "K"), (pygame.K_l, "L"),
            (pygame.K_LEFT, "←"), (pygame.K_RIGHT, "→"),
            (pygame.K_UP, "↑"), (pygame.K_DOWN, "↓"),
            (pygame.K_SPACE, "空格"), (pygame.K_LSHIFT, "Shift"),
        ]
        held = [label for key, label in mapping if self._held_keys.get(key)]
        return "+".join(held) if held else "-"

    def _draw_hud_hints(self, pygame):
        """画画面底部一行按键提示（中文，浅灰小字，位于地板下方）。"""
        hint = ("A/← D/→ 移动 | W/↑/空格 跳跃 | J/Z 攻击 | "
                "K/LShift 冲刺 | L/X 冰冻")
        x = 10
        y = G.GROUND_Y + 22
        self._blit_text(pygame, hint, x, y, (150, 150, 150), left=True)

    def _blit_text(self, pygame, text, x, y, color, left=False, right=False):
        """按锚点绘制一行文字：默认垂直居中，left/right 控制水平锚点。"""
        surf = self.font.render(text, True, color)
        if right:
            x = x - surf.get_width()
        elif left:
            pass
        else:
            x = x - surf.get_width() // 2
        y = y - surf.get_height() // 2
        self.screen.blit(surf, (x, y))

    def _draw_ghost(self, pygame, ps):
        """画远端玩家的服务器权威虚影（滞后插值位置，半透明轮廓）。

        ghost.sample 第三个参数 0.5 是「回看时长」（秒）：不取最新、而是取稍早
        时刻插值出的位置。这样即便最新快照因网络抖动忽快忽慢，虚影也只在平滑
        曲线上移动，观感稳定；代价是虚影比实心身体略「落后」，以此换取不跳变。

        Dead Reckoning 外推：状态同步下若某远端玩家「太久没收到新包」，内插来源
        枯竭（prev/curr 停在旧值），虚影会卡住。此时改用 DR 用「最后位置 + 速度 ×
        时间差」外推，让虚影在丢包空窗期继续惯性移动，等下次准确位置到再拉回。
        是否切换由「距上次收包时间 > extrapolate_threshold」判定。
        """
        # 按「内插↔外插」开关 mode 决定用内插还是外推：
        # - auto：按距上次收包时间 > extrapolate_threshold 时切外推（默认）
        # - interp：只内插（收不到包就停在旧值，不动）
        # - extrap：只外推（DR 一直用「最后位置+速度×时间差」惯性预测）
        use_extrapolate = False
        if self.mode == "state":
            if self.interp_extrap_mode == "extrap":
                use_extrapolate = True
            elif self.interp_extrap_mode == "interp":
                use_extrapolate = False
            else:   # auto
                last = self.ghost.last_sync_time(ps.player_id)
                if last is not None:
                    use_extrapolate = (time.monotonic() - last) > self.extrapolate_threshold
        if use_extrapolate:
            gx, gy = self.ghost.predict(ps.player_id)
        else:
            gx, gy = self.ghost.sample(ps.player_id, 0.5)
        px = gx.to_int()
        py = gy.to_int()
        # 半透明青色轮廓，表示「服务器认为他在这」，与实心身体形成虚实对比
        surf = pygame.Surface((24, 40), pygame.SRCALPHA)
        pygame.draw.rect(surf, (180, 230, 255, 90), (0, 0, 24, 40), 2)
        self.screen.blit(surf, (px - 12, py - 40))

    def _draw_player(self, pygame, ps, px, py):
        """画单个玩家，含冲刺线、攻击动作、受击动作、冰冻效果。"""
        # 冲刺线（拖尾）：冲刺中沿反方向画渐隐线段，颜色随同步来的 color_index
        if ps.dash_timer > 0:
            color = player_color(ps.color_index)
            for i in range(1, 4):
                alpha = 255 - i * 60
                tail_x = px - ps.dash_dir * i * 10
                surf = pygame.Surface((16, 32), pygame.SRCALPHA)
                surf.fill((color[0], color[1], color[2], alpha))
                self.screen.blit(surf, (tail_x - 8, py - 36))

        # 身体颜色：读「同步来的属性」color_index（服务器分配、随快照同步，各端一致）
        base_color = player_color(ps.color_index)

        # 受击动作：身体抖动 + 变白闪烁
        if ps.hit_anim > 0:
            jitter = (ps.hit_anim % 2) * 3 - 1
            body_color = (255, 255, 255)
            px = px + jitter
        else:
            body_color = base_color

        # 冰冻效果：身体覆盖青蓝色冰层
        if ps.frozen_timer > 0:
            body_color = (120, 200, 240)

        # 攻击动作：手臂前伸（在朝向侧画一条伸出的「手臂」）
        if ps.attack_anim > 0:
            arm_len = (G.ATTACK_ANIM - ps.attack_anim) * 3
            arm_x = px + ps.facing * (14 + arm_len)
            pygame.draw.line(self.screen, body_color, (px, py - 20), (arm_x, py - 20), 5)

        # 身体
        pygame.draw.rect(self.screen, body_color, (px - 12, py - 40, 24, 40))

        # 冰冻高亮边（冻结时画青蓝描边）
        if ps.frozen_timer > 0:
            pygame.draw.rect(self.screen, (200, 240, 255), (px - 12, py - 40, 24, 40), 2)

        # 朝向标记
        fwd = px + ps.facing * 14
        pygame.draw.line(self.screen, (250, 250, 250), (px, py - 20), (fwd, py - 20), 2)

        # 血条：自己绿色、敌人红色（底框深灰）
        pygame.draw.rect(self.screen, (60, 60, 60), (px - 12, py - 50, 24, 4))
        hpw = int(24 * ps.hp / G.MAX_HP)
        hp_color = (0, 210, 80) if ps.player_id == self.player_id else (220, 50, 50)
        pygame.draw.rect(self.screen, hp_color, (px - 12, py - 50, hpw, 4))

        # 头顶数字：表示「第几个客户端」（player_id）
        pid_text = str(ps.player_id)
        pid_surf = self.font.render(pid_text, True, (255, 255, 255))
        self.screen.blit(pid_surf, (px - pid_surf.get_width() // 2, py - 66))


def run():
    host = os.environ.get("SYNC_DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("SYNC_DEMO_PORT", "7808"))
    name = os.environ.get("SYNC_DEMO_NAME", "client")
    client = Client(host, port, name)
    client.connect()
    client.run_gui()


if __name__ == "__main__":
    run()
