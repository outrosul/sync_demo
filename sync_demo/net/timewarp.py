"""延迟补偿（Lag Compensation）的历史世界 buffer 与回退裁判。

**为什么需要**：状态同步下，服务器是权威。玩家 A 在客户端看到 B 在位置 X，于是
朝 X 开枪。但 A 看到的 X 是「经过网络延迟的过去画面」，等 A 的射击请求到服务器时，
服务器世界里的 B 已经走到 X'（更靠前）。如果服务器按「当前世界」裁判，A 明明瞄准
了却打不中——这不公平。网络越差的玩家越吃亏。

**延迟补偿的做法（Valve Source 引擎经典方案）**：服务器维护**过去若干帧的世界快照**。
收到 A 的射击请求时，服务器按「A 的网络延迟」回退到「A 发出射击那一刻」的世界快照，
在那个历史世界里判断 A 的子弹有没有命中 B，再把结果应用到「当前世界」。

**本模块职责**（纯数据，不碰 socket / 渲染）：
- ``WorldHistoryBuffer``：每 tick 归档一次世界快照，保留最近 N 帧，支持按 tick 取历史。
- ``rewind_and_judge`` 的辅助：给定历史世界 + 攻击者朝向/射程，判定是否命中某个目标。

依赖 ``core.game`` / ``core.sim``（确定性推演）与 ``net.prediction.snapshot_world``（深拷贝）。
"""

from sync_demo.core import game as G
from sync_demo.net import prediction as PR


class WorldHistoryBuffer:
    """按 tick 归档世界快照的环形历史 buffer。

    每个逻辑 tick 结束时把当前世界深拷贝一份存进去；回卷到过去的 tick 时，
    取出该 tick 的世界快照做命中判定。只保留最近 ``capacity`` 帧，超出丢最旧的。
    """

    def __init__(self, capacity=128):
        self.capacity = capacity
        self.frames = {}          # tick -> 世界快照（深拷贝）
        self.tick_order = []      # 按序记录 tick，用于淘汰最旧

    def record(self, tick, world):
        """归档一帧：把 world 深拷贝存入，标记为 tick。"""
        self.frames[tick] = PR.snapshot_world(world)
        self.tick_order.append(tick)
        # 淘汰超出容量的最旧帧
        while len(self.tick_order) > self.capacity:
            oldest = self.tick_order.pop(0)
            self.frames.pop(oldest, None)

    def get(self, tick):
        """取某个 tick 的世界快照；不存在返回 None。"""
        return self.frames.get(tick)

    def nearest(self, tick):
        """取「不超过 tick 且最接近」的世界快照（用于回退到攻击发出的近似时刻）。

        网络延迟通常不正好落在某个整数 tick 上，这里取「≤ 目标 tick 且最新」的那帧，
        保证回退的裁判世界是「攻击者当时确实看到」的最近一帧。
        """
        best = None
        best_tick = -1
        for t in self.tick_order:
            if t <= tick and t > best_tick:
                best_tick = t
                best = self.frames[t]
        return best

    def latest_tick(self):
        """返回当前最新归档的 tick；空则返回 -1。"""
        return self.tick_order[-1] if self.tick_order else -1


def rewind_hit_test(history_world, attacker_id, target_id, max_range, max_dy):
    """在某个历史世界里，判定 attacker 的「近战攻击」是否命中 target。

    :param history_world: 回退到的历史世界快照。
    :param attacker_id / target_id: 双方 player_id。
    :param max_range: 水平命中距离（**像素**，int 或 float）。
    :param max_dy: 竖直允许差（**像素**，int 或 float）。
    :returns: True（命中）/ False。
    """
    attacker = history_world.players.get(attacker_id)
    target = history_world.players.get(target_id)
    if attacker is None or target is None:
        return False
    if not attacker.alive or not target.alive:
        return False
    dx = target.x - attacker.x
    dy = target.y - attacker.y
    # 朝向一致 + 水平/竖直都在命中范围内（距离参数是像素 → 转定点像素值比较）
    same_dir = (dx.raw * attacker.facing) > 0
    range_fixed = G.Fixed.from_float(max_range)
    dy_fixed = G.Fixed.from_float(max_dy)
    return same_dir and abs(dx) <= range_fixed and abs(dy) <= dy_fixed
