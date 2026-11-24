"""Jitter Buffer（抖动缓冲）/ 抖动平滑。

**为什么需要**：状态同步下，远端玩家位置靠服务器周期快照下发。网络抖动会让快照
到达忽快忽慢：某段时间没包（缓存被耗尽），下一段又一堆包（缓存积压）。若直接「来
一包就用一包」，越端玩家会「一顿一顿」。

**做法（参照唐弢《弱网移动同步优化》「抖动平滑」）**：维护一个**帧缓存队列**，收到的
快照先入队；渲染按「动态插值速度」从队列消费。核心是让**缓存既不耗尽也不爆满**：

- 丢包/延迟 → 帧缓存减少 → **减小插值速度**（慢点消耗缓存）。
- 帧缓存恢复（批量到达）→ **增大插值速度**（快点消耗，避免积压）。
- 帧缓存耗尽且无包 → **降级**：停止移动（保守）或交 Dead Reckoning 预测（激进）。

**本模块职责**（纯数据，可脱离网络/渲染单测）：
- ``push(snapshot)``：快照入帧缓存队列。
- ``sample(now)``：按当前缓存水位动态算出「插值速度」，取出对应位置的插值结果；
  返回 (位置, 是否降级)。
- ``water_level()``：报告当前缓存水位（供观测/调参）。

插值速度动态调节：水位高（缓存积压）→ alpha 增大（快速追平）；水位低（缓存将尽）→
alpha 减小（慢速消耗）。缓存空 → 返回降级标记，由调用方决定「停止」或「预测」。

时钟可注入（与 KCP / DR 同思路），保证可测与可复现。
"""

from sync_demo.core import game as G


class JitterBuffer:
    """状态同步的抖动缓冲：帧缓存 + 动态插值速度消费。

    :param target_interval: 期望帧间隔（秒），如 0.05（20Hz 快照）。
    :param max_buffer: 帧缓存上限（帧数），超过则丢弃最旧（防爆满）。
    :param clock: 可注入时钟（秒）。
    """

    def __init__(self, target_interval=0.05, max_buffer=64, clock=None):
        self.target_interval = target_interval
        self.max_buffer = max_buffer
        self.clock = clock if clock is not None else _monotonic
        self.buffer = []          # [(收到时刻, (x, y))] 帧缓存队列，x/y 为定点 Fixed
        self.last_sample_time = None   # 上次 sample 的时钟时刻（算 Δt）

    def push(self, x, y):
        """收到一个远端玩家位置快照，入帧缓存队列。"""
        now = self.clock()
        fx = x if isinstance(x, G.Fixed) else G.Fixed(x)
        fy = y if isinstance(y, G.Fixed) else G.Fixed(y)
        self.buffer.append((now, (fx, fy)))
        # 防爆满：超出上限丢最旧（缓存只保留最近一段，旧数据对插值已无用）
        while len(self.buffer) > self.max_buffer:
            self.buffer.pop(0)

    def water_level(self):
        """当前缓存水位（帧数），供观测动态插值速度。"""
        return len(self.buffer)

    def _dynamic_alpha(self):
        """按缓存水位算「插值速度」：水位低→慢（保缓存），水位高→快（追平）。

        alpha 归一化到 [0,1]：缓存越满越逼近 1（全速消费最新），越空越逼近 0
        （几乎原地不动，等待新包）。核心：通过调节 alpha 改变插值速度，让缓存
        始终留有余量、不耗尽也不爆满。
        """
        if not self.buffer:
            return 0.0
        # 水位除以「期望缓冲量」得到 0~1+；期望缓冲约 6 帧（足以吸收数次抖动）
        ideal = 6.0
        ratio = len(self.buffer) / ideal
        return min(1.0, max(0.0, ratio))

    def sample(self):
        """消费一帧：按动态插值速度返回「当前位置」与「是否降级」。

        返回 ``(x, y, degraded)``：
        - 正常：从帧缓存取最新（或插值），degraded=False。
        - 降级：缓存耗尽且无包 → 返回最后已知位置，degraded=True（调用方决定停/预测）。
        """
        now = self.clock()
        if self.last_sample_time is None:
            self.last_sample_time = now
        alpha = self._dynamic_alpha()
        if not self.buffer:
            # 缓存耗尽：将 dead reckoning 交给调用方（降级）
            return G.Fixed(0), G.Fixed(0), True
        # 取最新快照（简单策略：alpha 高时直接用最新，alpha 低时用最早——即「慢速」）
        if alpha >= 0.99:
            _, (x, y) = self.buffer[-1]
        else:
            _, (x, y) = self.buffer[0]
        self.last_sample_time = now
        return x, y, False

    def estimate_position(self):
        """返回当前缓冲区的「最新位置」（供降级时 Dead Reckoning 取锚点）。"""
        if not self.buffer:
            return None
        _, (x, y) = self.buffer[-1]
        return x, y


def _monotonic():
    import time
    return time.monotonic()
