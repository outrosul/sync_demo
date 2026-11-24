"""预测与回滚测试：验证「预测先行、权威回滚、重放未确认输入」。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net import prediction as PR


def _mk():
    rng = random.Random(42)
    w = G.World(rng)
    w.add_player(1, "me", 100, G.GROUND_Y)
    return w


def test_predict_advances_immediately():
    pc = PR.PredictedClient(random.Random(42))
    # 先造一个与 _mk 相同的世界
    pc.world = _mk()
    x0 = pc.world.players[1].x
    pc.predict(1, sim.InputState(right=True))
    assert pc.world.players[1].x > x0          # 预测立刻生效


def test_rollback_to_authority_and_replay():
    pc = PR.PredictedClient(random.Random(42))
    pc.world = _mk()
    # 预测 5 步右移
    for _ in range(5):
        pc.predict(1, sim.InputState(right=True))
    predicted_x = pc.world.players[1].x.to_float()

    # 服务器权威快照 tick=0（即：服务器还没采纳任何输入 → 权威位置还是起点）
    def rebuild(world):
        world.players = {}
        world.add_player(1, "me", 100, G.GROUND_Y)
    rollback, unacked = pc.on_snapshot(0, rebuild)

    assert rollback is True
    assert unacked == 5                        # 5 个未确认输入被重放
    # 重放后位置应回到「从权威起点 + 5 步」的预测结果
    assert pc.world.players[1].x == predicted_x


def test_history_buffer_recorded():
    pc = PR.PredictedClient(random.Random(42))
    pc.world = _mk()
    for _ in range(30):
        pc.predict(1, sim.InputState(right=True))
    assert len(pc.history) > 0
    assert len(pc.history) <= 64               # 历史 buffer 有上限


def test_no_rollback_when_snapshot_is_ahead():
    pc = PR.PredictedClient(random.Random(42))
    pc.world = _mk()
    pc.predict(1, sim.InputState(right=True))
    # 权威快照 tick 已经 >= 本地 tick（服务器领先，无需回滚）
    def rebuild(world):
        pass
    rollback, unacked = pc.on_snapshot(pc.world.tick + 1, rebuild)
    assert rollback is False
