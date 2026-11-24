"""移动预测（半个 RTT 先行 + 摇杆历史累加）。

**为什么需要**：状态同步下，移动指令发出后要**延迟半个 RTT** 才被服务端处理并对齐。
于是客户端看到的「服务端权威位置」其实是「半个 RTT 之前」的旧位置；若直接跟随，
弱网下手感明显滞后。做法是**往前预测半个 RTT**：

    预测位置 = 上次同步位置
             + 上次同步方向 × 帧间隔 × 速度
             + Σ(半个 RTT 前记录的摇杆方向 × 帧间隔 × 速度)

关键在第二个求和项：客户端要保存「过去若干个 tick 的摇杆/移动方向」历史 buffer，
收到一次权威位置同步时，把「半个 RTT 之前的那些输入」按时间累加进预测位置，从而
「追平」服务端尚未反馈的那半个 RTT。

**本模块职责**（纯数据可测，不碰 socket/渲染）：
- ``record_input(direction)``：每 tick 记录一次移动方向（-1 左 / 0 停 / +1 右）。
- ``on_sync(pos, half_rtt_ticks)``：收到权威位置后，用半个 RTT 前的摇杆历史算预测位置。
- ``predict_delta(half_rtt_ticks)``：返回「半个 RTT 前那些输入」应累加的位移。

参照唐弢《弱网移动同步优化》第二节。方向历史用定点位移（速度 × 帧间隔）累加，
全整数运算保证可复现。
"""

from sync_demo.core import game as G


class MovementPredictor:
    """移动预测器：摇杆历史 buffer + 半个 RTT 先行累加。

    :param move_speed: 每 tick 移动距离（定点 Fixed，如 G.MOVE_SPEED）。
    :param history_capacity: 摇杆历史最大条数（tick 数）。
    """

    def __init__(self, move_speed=None, history_capacity=256):
        self.move_speed = move_speed if move_speed is not None else G.MOVE_SPEED
        self.history_capacity = history_capacity
        self.direction_history = []   # 最近若干 tick 的移动方向 [-1, 0, +1]，队尾最新
        self.last_sync_pos = None     # 上次权威同步位置（Fixed x, y）

    def record_input(self, direction):
        """记录一个 tick 的移动方向（-1 / 0 / +1）。"""
        self.direction_history.append(int(direction))
        while len(self.direction_history) > self.history_capacity:
            self.direction_history.pop(0)

    def on_sync(self, x, y):
        """收到一次权威位置同步，更新「上次同步位置」锚点。"""
        fx = x if isinstance(x, G.Fixed) else G.Fixed(x)
        fy = y if isinstance(y, G.Fixed) else G.Fixed(y)
        self.last_sync_pos = (fx, fy)

    def predict_delta(self, half_rtt_ticks):
        """返回「半个 RTT 内」的摇杆输入应累加的位移（定点 x 增量）。

        服务端权威位置滞后半个 RTT：它反映的是「半个 RTT 之前」的世界。所以要
        预测「半个 RTT 前 → 现在」这段的移动，即累加**最近 half_rtt_ticks 个 tick**
        的摇杆方向（这段输入服务端还没算进去）。
        """
        if not self.direction_history:
            return G.Fixed(0)
        n = len(self.direction_history)
        take = min(half_rtt_ticks, n)
        window = self.direction_history[-take:]
        total_dir = sum(window)
        # 位移 = 方向总和 × 每 tick 位移（定点乘整数）
        return self.move_speed * total_dir

    def predict(self, half_rtt_ticks):
        """返回预测位置 = 上次同步位置 + 半个 RTT 前的位移。"""
        if self.last_sync_pos is None:
            return G.Fixed(0), G.Fixed(0)
        dx = self.predict_delta(half_rtt_ticks)
        return self.last_sync_pos[0] + dx, self.last_sync_pos[1]
