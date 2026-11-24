"""纠错平滑（纠错帧数可调）：从「预测位置」平滑过渡到「权威位置」。

**为什么需要**：状态同步下，客户端预测出错、收到权威位置纠偏时，若直接「跳到」权威
位置，表现为突跳（顿挫）；若用多帧缓慢插值逼近，则平滑但滞后。这是「纠错帧数」的取舍：

- 纠错帧数 = 1：立即纠正 → 快但突跳、顿挫感强。
- 纠错帧数 = N：用 N 帧线性插值平滑逼近 → 平滑但轨迹「拖尾」、滞后。

参照高旻健《从零同步探索》「预测与纠正」两类策略。本模块把「纠错帧数」做成可调参数，
让研究者能直观对比「跳变 vs 平滑」的差异。

**本模块职责**（纯数据可测）：
- ``start_correction(current, target, frames)``：发起一次纠错，记录起止点 + 剩余帧数。
- ``step()``：每帧返回「当前这一帧应显示的插值位置」，帧数递减；纠错完返回 None。
- ``is_correcting()``：是否仍在纠错中。

定点全整数插值，保证可复现。
"""

from sync_demo.core import game as G


class Corrector:
    """纠错平滑器：多帧线性插值从 current 逼近 target。"""

    def __init__(self):
        self.start_pos = None    # 纠错起点（Fixed x, y）
        self.target_pos = None   # 纠错终点（权威位置，Fixed x, y）
        self.remaining = 0       # 剩余纠错帧数
        self.total = 0           # 纠错总帧数（算进度用）

    def start_correction(self, current, target, frames):
        """发起一次纠错：从 current 用 frames 帧插值到 target。

        :param current: 当前（预测）位置（Fixed 或 int 定点）。
        :param target: 目标（权威）位置（Fixed 或 int 定点）。
        :param frames: 纠错帧数（1 = 立即，N = N 帧平滑）。
        """
        self.start_pos = (G.Fixed(current[0]) if not isinstance(current[0], G.Fixed) else current[0],
                          G.Fixed(current[1]) if not isinstance(current[1], G.Fixed) else current[1])
        self.target_pos = (G.Fixed(target[0]) if not isinstance(target[0], G.Fixed) else target[0],
                           G.Fixed(target[1]) if not isinstance(target[1], G.Fixed) else target[1])
        self.remaining = max(1, int(frames))
        self.total = self.remaining

    def step(self):
        """推进一帧：返回本帧应显示的插值位置；纠错完成返回 None。"""
        if self.remaining <= 0 or self.start_pos is None:
            return None
        # 进度 t = 1 - remaining/total，从 0 到 1
        t = 1.0 - float(self.remaining) / float(self.total)
        sx, sy = self.start_pos
        tx, ty = self.target_pos
        # 定点线性插值（全整数，避免浮点漂移；用 raw 定点整数算插值）
        px_raw = sx.raw + int((tx.raw - sx.raw) * t)
        py_raw = sy.raw + int((ty.raw - sy.raw) * t)
        self.remaining -= 1
        if self.remaining <= 0:
            # 纠错结束，明确落在目标点
            return self.target_pos
        return G.Fixed(px_raw), G.Fixed(py_raw)

    def is_correcting(self):
        """是否仍在纠错中。"""
        return self.remaining > 0
