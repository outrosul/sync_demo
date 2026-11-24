"""预测与回滚（回拉）：状态同步下的客户端先行 + 权威回滚。

**为什么需要预测**：状态同步下，客户端的输入要先到服务器、被服务器权威推演、
再广播快照回来，一来一回多了 ``rtt`` 延迟。为了即时手感，客户端**先本地预测**：
用自己发出的输入对本地世界推演，画面立刻响应；等权威快照到了，再**回滚（回拉）**：
丢弃预测结果、回到权威状态、把「尚未被服务器确认」的输入重放一遍。

**本模块职责**（纯数据，不碰 socket / 渲染）：

- ``PredictedClient``：持有「权威世界」+「预测缓冲」，``predict(inputs)`` 每 tick 本地
  推演；``on_snapshot(snapshot, unacked_inputs)`` 收到权威快照后回滚并重放未确认输入。
- ``history buffer``：记录每 tick 的世界状态快照，供回滚到任意历史帧。

**核心研究点**（这是同步研究的关键）：

1. **预测错了会怎样**：本地预测的输入序列与服务器实际采纳的不同 → 回滚后画面「跳」一下。
2. **回滚代价**：需要保存历史 world 状态（内存/算力）。
3. **重放**：把未确认的输入按序重放，得到「以权威为起点」的预测结果。
"""

from sync_demo.core import game as G
from sync_demo.core import sim


def snapshot_world(world):
    """深拷贝一个世界状态（用于历史 buffer / 回滚）。

    为什么必须「深拷贝」而不是存一个引用：回滚的目标是「还原到过去某一帧
    的完整世界」。如果这里只存 ``world`` 这个对象引用，那 history 里所有
    快照都会指向同一个、被后续 tick 一路改写的最新对象——回滚时拿到的
    永远是「当下」，过去的任何一帧都找不回来了。模拟里世界状态由一堆可变
    对象（PlayerState / Projectile）拼成，浅拷贝只复制外壳、里面的字段仍
    共享同一份内存，照样会被写坏。所以这里要逐字段造一份全新的对象图，
    让每个快照都真正「冻结」在记录它的那一刻，读它时才不会被后续改动污染。
    """
    new_world = G.World(world.rng)
    new_world.tick = world.tick
    new_world.next_proj_id = world.next_proj_id
    for pid, ps in world.players.items():
        # 玩家坐标/朝向是普通值，直接复制即可；但速度等字段是 Fixed（定点数），
        # 内部是可变的 raw 数值，不能直接赋值引用，要经 raw 重新构造一份独立副本。
        np = G.PlayerState(ps.player_id, ps.name, ps.x, ps.y)
        np.vx = G.Fixed(ps.vx.raw)
        np.vy = G.Fixed(ps.vy.raw)
        np.facing = ps.facing
        np.on_ground = ps.on_ground
        np.hp = ps.hp
        np.alive = ps.alive
        np.attack_timer = ps.attack_timer
        np.attack_cooldown = ps.attack_cooldown
        np.hit_facing = ps.hit_facing
        np.hit_active = ps.hit_active
        np.attack_anim = ps.attack_anim
        np.hit_anim = ps.hit_anim
        np.dash_timer = ps.dash_timer
        np.dash_dir = ps.dash_dir
        np.dash_cooldown = ps.dash_cooldown
        np.frozen_timer = ps.frozen_timer
        np.freeze_cooldown = ps.freeze_cooldown
        np.color_index = ps.color_index
        new_world.players[pid] = np
    for pid, proj in world.projectiles.items():
        new_world.projectiles[pid] = G.Projectile(
            proj.proj_id, proj.owner_id, proj.x, proj.y, proj.vx, proj.vy, proj.damage)
    return new_world


class PredictedClient:
    """状态同步下带预测 + 回滚的客户端逻辑（研究用）。"""

    def __init__(self, rng):
        self.world = G.World(rng)      # 当前显示的预测世界
        self.ack_tick = 0              # 已被服务器确认的最高权威 tick
        self.unacked_inputs = []       # [(tick, player_id, InputState)] 未确认输入流水
        self.history = []              # [(tick, world_snapshot)] 历史 buffer

    def predict(self, player_id, inp):
        """本地预测一步：把「我的输入」立即用于本地推演，画面即时响应。

        返回本 tick 的输入序号（用于回滚时重放）。

        为什么「本地先算」：真实状态同步里，玩家按键要先发给服务器，服务器
        权威推演后把快照广播回来，这份往返 ``rtt`` 可能几十上百毫秒。若不预测，
        画面要等快照回来才动，操作和反馈之间就隔着这段延迟，手感「飘」。预测的
        本质是「赌」服务器会采纳我这份输入：先把输入立刻喂给本地模拟跑一步、
        立即渲染，玩家就感觉不到这段网络延迟。代价是本地结果只是「预测」，可能
        和权威不一致，之后要靠 on_snapshot 回滚纠正。
        """
        tick = self.world.tick
        # 把输入按 (tick, player, 输入) 记进未确认序列：服务器未来确认到哪一帧之前，
        # 这些输入都可能在回滚时重放；所以必须原样留档，不能只推演不留记录。
        self.unacked_inputs.append((tick, player_id, inp))
        sim.tick(self.world, {player_id: inp})
        self._record_history(tick)
        return tick

    def record_predictions(self, player_id, inputs):
        """一次预测多步：``inputs`` 为按序的 InputState 列表。"""
        for inp in inputs:
            tick = self.world.tick
            self.unacked_inputs.append((tick, player_id, inp))
            sim.tick(self.world, {player_id: inp})
            self._record_history(tick)

    def on_snapshot(self, snapshot_tick, rebuild_fn):
        """收到权威快照后回滚。

        :param snapshot_tick: 权威快照的 tick。
        :param rebuild_fn: func(world) —— 把 ``self.world`` 重建为权威快照状态。
        :returns: (rollback_happened, unacked_count) 供观测「是否回滚了、重放了多少」。

        为什么是「回滚到权威 + 重放未确认输入」两步，而不是直接接着权威快照继续跑：

        1. 回滚到权威：本地预测可能和服务器不一致（输入被服务器拒绝 / 排序不同 /
           其他玩家影响）。预测结果不可信，必须彻底丢弃，以服务器这帧权威状态
           为新起点。若不回滚，本地就永远带着「预测出来的偏差」越走越偏。

        2. 重放未确认输入：回滚后到 ``snapshot_tick`` 为止是权威的，但玩家手上
           可能还有几个「发出去、服务器尚未来得及确认」的输入。这些输入不能丢
           （丢了操作就凭空消失），要在权威起点上按原序重放一遍，得到「以权威
           为起点、含我新操作」的预测结果，画面才不会在回滚瞬间又卡回「没反应」。
        """
        rollback = False
        if snapshot_tick < self.world.tick:
            # 快照 tick 落后于本地已推演到的 tick，说明本地跑在了权威前面；
            # 这之间的本地帧都是「预测」，现在要倒回去，属一次真正的回滚。
            rollback = True
        # 回滚到权威：让调用方把 world 置为权威状态
        rebuild_fn(self.world)
        self.ack_tick = snapshot_tick
        # 丢弃已确认的输入，重放未确认的
        # 只保留 tick >= snapshot_tick 的输入：这些还没被这个快照「盖棺定论」，
        # 需要在权威起点上重放；更早的已经体现在快照里，不能再重放一遍。
        unacked = [u for u in self.unacked_inputs if u[0] >= snapshot_tick]
        for _tick, pid, inp in unacked:
            sim.tick(self.world, {pid: inp})
        self.unacked_inputs = []
        return rollback, len(unacked)

    def _record_history(self, tick):
        # 只保留最近 64 帧历史（够回滚用），避免无限增长。
        # 为什么要有上限：每帧都深拷贝一整个世界状态，内存随时间线性膨胀且不回收，
        # 长对局会越吃越多；而回滚只可能回退到「刚过去的一小段」（rtt 量级），
        # 太老的历史帧永远用不上。于是截断到固定长度，内存有界、回滚够用即可。
        self.history.append((tick, snapshot_world(self.world)))
        if len(self.history) > 64:
            self.history.pop(0)

    def rollback_to(self, tick, rebuild_fn):
        """研究用：手动回滚到历史某帧（演示「回拉」效果）。"""
        # 找 tick 对应的历史快照
        target = None
        for t, snap in self.history:
            if t == tick:
                target = snap
        if target is None:
            return False
        self.world = snapshot_world(target)
        rebuild_fn(self.world)
        return True
