"""平滑移动：渲染插值 + 服务器位置虚影。

**为什么需要平滑**：状态同步下权威快照是离散的（每 tick 一个位置），如果直接
逐快照渲染，角色会「一顿一顿」。做法是**渲染落后逻辑 1 帧**，在相邻两个逻辑状态
之间按渲染时间插值，让位置连续过渡。

参照 KM《同步概述》「渲染的插值」：维护一个渲染时间 ``t_render``，在 S(N-1) 与 S(N)
之间按比例插值；快慢可通过时间比例缩放调整。

**虚影（ghost）**：客户端本地有自己的预测位置（即时），而「服务器确认的权威位置」
是一条**滞后但正确**的轨迹——把权威轨迹用插值平滑后画成「虚影」，玩家能直观看到
「我预测在这，服务器认为我在那」的差距。这个差距就是研究「预测 vs 回拉」的核心。

本模块纯数据：给定「快照序列」输出「每个渲染帧的插值位置」，不碰 pygame。
"""

from sync_demo.core import game as G


class InterpolatedTracker:
    """对某个实体的位置做「落后一帧」的插值平滑。

    输入离散快照位置（定点 Fixed 或 int），输出连续化的插值位置。
    """

    def __init__(self):
        # 只保留相邻的两个逻辑状态，就足以在两帧之间做线性过渡：
        # 更早的历史快照对「当前这一段」毫无贡献，存着只会浪费内存。
        self.prev = None          # 上一个逻辑状态位置（Fixed）
        self.curr = None          # 当前逻辑状态位置（Fixed）
        # 插值因子单独存出来，而不是在当时直接换算成坐标：
        # 因为「同一对 prev/curr」会被多次 sample（每渲染帧都问一次），
        # alpha 由渲染时刻决定，属于采样时参数，不该固化进 push 的时刻。
        self.t = 0.0              # 渲染插值因子 [0,1]

    def push(self, x, y):
        """推入一个新的逻辑状态位置，之前的 curr 变成 prev。

        滚动窗口的核心：新状态进来时，旧 curr 顺位降成 prev，
        于是 sample 永远只在「最新相邻两帧」之间插值，天然落后逻辑一格。
        """
        self.prev = self.curr
        # 入参既可能是定点也可能是普通 int，统一规范成 Fixed 再存，
        # 否则后面 sample 里对 .raw 的定点运算会在混合类型下出错。
        self.curr = (G.Fixed(x) if not isinstance(x, G.Fixed) else x,
                     G.Fixed(y) if not isinstance(y, G.Fixed) else y)
        # 新的一帧刚落地，插值窗口应该从头开始（指向上一个状态），
        # 不清零的话残留的 t 会把 «旧插值进度» 错误地套到新帧上。
        self.t = 0.0

    def sample(self, alpha):
        """按插值因子 alpha（0~1）取 prev 与 curr 之间的位置。

        渲染落后逻辑 1 帧：alpha=0.5 表示在两个逻辑状态正中间。
        返回定点 (x, y)。
        """
        # 刚启动、还没攒够两个快照时的兜底：
        # prev 为空意味着「还没有上一帧」，无法插值，只能退化为返回当前态；
        # 连 curr 都为空说明一个快照都没有，返回原点占位而不报错，
        # 这样调用方不用为开局阶段额外判空。
        if self.prev is None or self.curr is None:
            if self.curr is None:
                return G.Fixed(0), G.Fixed(0)
            return self.curr
        # 定点插值走整数运算：直接在 .raw（已放大定点整数）上做「差量×alpha」，
        # 再吃回 Fixed。定点数值在整数域内是精确的，避免「浮点累加再取整」引入
        # 的漂移；`int(...)` 截断而不是四舍五入，保证结果永远落在
        # prev 与 curr 两个端点之间、绝不越界。若不截断直接用浮点，
        # 不同客户端/不同 CPU 的舍入差异会让同一插值结果逐位不同，破坏确定性。
        px = self.prev[0].raw + int((self.curr[0].raw - self.prev[0].raw) * alpha)
        py = self.prev[1].raw + int((self.curr[1].raw - self.prev[1].raw) * alpha)
        return G.Fixed(px), G.Fixed(py)


class GhostTracker:
    """服务器权威位置的「虚影」：用插值平滑权威轨迹，滞后于本地预测。

    持有所有远程玩家（非自己）的权威位置追踪；本地预测位置由 PredictedClient 提供，
    虚影由权威快照序列插值得出，两者对比即可观察「预测 vs 权威」的差距。
    """

    def __init__(self, clock=None):
        # 每个远程玩家一条独立的插值轨迹：各自的快照节拍、位置都不同，
        # 拆开存才能互不污染地分别平滑。
        self.trackers = {}        # player_id -> InterpolatedTracker
        # 每个远程玩家一个 DeadReckoner：状态同步收不到新快照时，用速度外推兜底，
        # 让 shadow 在丢包空窗期保持惯性移动（而非卡住）。
        self.reckoners = {}       # player_id -> DeadReckoner
        self.clock = clock

    def push_snapshot(self, players):
        """推入权威快照里的所有玩家位置（含速度，供 DR 外推）。"""
        for pd in players:
            pid = pd["player_id"]
            if pid not in self.trackers:
                self.trackers[pid] = InterpolatedTracker()
            self.trackers[pid].push(pd["x"], pd["y"])
            # DR 需要速度 + 位置：速度取自快照的 vx/vy（可能缺省，缺省当 0）
            vx = pd.get("vx", 0)
            vy = pd.get("vy", 0)
            if pid not in self.reckoners:
                self.reckoners[pid] = DeadReckoner(clock=self.clock)
            self.reckoners[pid].update(pd["x"], pd["y"], vx, vy)

    def sample(self, pid, alpha):
        """取某个玩家的虚影插值位置。"""
        tracker = self.trackers.get(pid)
        if tracker is None:
            return G.Fixed(0), G.Fixed(0)
        return tracker.sample(alpha)

    def predict(self, pid):
        """取某个玩家的 DR 外推位置（收不到新包时惯性预测）。"""
        reckon = self.reckoners.get(pid)
        if reckon is None:
            return G.Fixed(0), G.Fixed(0)
        return reckon.predict()

    def last_sync_time(self, pid):
        """返回该玩家最后一次收到权威位置包的时钟秒（没有则返回 None）。

        供调用方判断「多久没收到新包」，以在内插（正常）与外推（超时）之间切换。
        """
        reckon = self.reckoners.get(pid)
        if reckon is None or reckon.ts_last is None:
            return None
        return reckon.ts_last


class DeadReckoner:
    """航位推算（Dead Reckoning）：收不到权威位置包时，用速度外推预测位置。

    参照戴伟策《网络同步》「外插值」与分享三的公式：
        walltime_diff = (tc_cur - tc_start) - (ts_last - ts_start)
        predict_pos   = last_sync_pos + speed * walltime_diff * move_dir

    **作用场景（状态同步）**：客户端 shadow 每 N 毫秒收到一次服务端权威位置，网络波动
    时（丢包/延迟）shadow 收不到新包。若 shadow 停在原地，表现就是「卡住一下」。DR 用
    「最后一次准确位置」+「该位置携带的速度」+「经过的时间」外推，让 shadow 在没包的
    空窗期继续按惯性移动，等下次准确位置到了再拉回修正。

    **为什么只用在状态同步**：DR 依赖 wall clock 时间差，是「表现层平滑」而非「确定
    性推演」——状态同步本就是服务器权威、客户端表现层插值，允许不逐位一致；帧同步则
    必须确定性，位置由 sim 推演 + 回滚保证，不能用 DR。故 DR 挂在 state 模式的 ghost 上。

    **时钟可注入**：与 KCP 同思路，时钟默认 ``time.monotonic``，测试时注入假时钟以秒级
    验证，不用真 sleep，也能保证「同一输入序列 → 同一预测」的可复现性。
    """

    def __init__(self, clock=None):
        # 可注入时钟（秒）。默认单调时钟：DR 只做表现层预测，用 wall clock 足够。
        self.clock = clock if clock is not None else _monotonic
        self.last_pos = None          # 最后一次准确位置（Fixed x, y）
        self.velocity = None          # 最后一次携带的速度（Fixed vx, vy）
        self.ts_last = None           # 最后一次准确位置的接收时刻（clock 秒）

    def update(self, x, y, vx, vy):
        """喂入一个新的权威位置 + 速度（对应「收到一次位置同步包」）。

        :param x/y: 权威位置（Fixed 或 int 定点）。
        :param vx/vy: 该位置携带的线速度（Fixed 或 int 定点）。
        """
        self.last_pos = (G.Fixed(x) if not isinstance(x, G.Fixed) else x,
                         G.Fixed(y) if not isinstance(y, G.Fixed) else y)
        self.velocity = (G.Fixed(vx) if not isinstance(vx, G.Fixed) else vx,
                         G.Fixed(vy) if not isinstance(vy, G.Fixed) else vy)
        self.ts_last = self.clock()

    def predict(self):
        """返回当前时刻的外推预测位置（定点 x, y）。

        若从没收到过位置包（last_pos 为空），返回原点；否则按 DR 公式外推：
            delta_t = now - ts_last（壁钟秒）
            pos = last_pos + velocity * delta_t
        定点运算：速度是「像素/秒」用定点表示，乘以秒（浮点）会破坏定点，故这里把
        delta_t 也按定点整数化（×256 取整）再乘，保持全定点、可复现。
        """
        if self.last_pos is None or self.velocity is None:
            return G.Fixed(0), G.Fixed(0)
        now = self.clock()
        delta_t = now - self.ts_last
        if delta_t < 0:
            delta_t = 0.0
        # 时间差定点化（×256 截断），与速度（定点 像素/秒 ×256）相乘得到定点位移
        dt_fixed = int(delta_t * G.FIXED_SCALE)
        dx = (self.velocity[0].raw * dt_fixed) >> G.FIXED_SHIFT
        dy = (self.velocity[1].raw * dt_fixed) >> G.FIXED_SHIFT
        return self.last_pos[0] + G.Fixed(dx), self.last_pos[1] + G.Fixed(dy)

    def has_estimate(self):
        """是否已有可外推的锚点（收到过至少一次位置包）。"""
        return self.last_pos is not None


def _monotonic():
    import time
    return time.monotonic()
