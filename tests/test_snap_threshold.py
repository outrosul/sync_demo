"""回拉容差（snap threshold）测试：偏差在阈值内不回拉，超过阈值才回拉。"""

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net.client import Client
from sync_demo.net import prediction as PR


def _make_client():
    c = Client("127.0.0.1", 7808, "t")
    c.player_id = 1
    c.world = G.World(__import__("random").Random(1))
    c.world.add_player(1, "me", 100, G.GROUND_Y)
    c.confirmed_world = PR.snapshot_world(c.world)
    return c


def test_snap_zero_always_rolls_back():
    c = _make_client()
    c.snap_threshold = 0.0
    # 本地预测让玩家右移很远
    c._predict_local(sim.InputState(right=True))
    predicted_x = c.world.players[1].x.to_float()
    # 服务器本帧无输入（玩家应停在原地），权威与预测偏差大
    c._rollback_and_replay({})
    # threshold=0 总是回拉：世界应回到权威（无输入 = 原地）
    assert c.world.players[1].x.to_float() == 100.0


def test_snap_large_keeps_prediction():
    c = _make_client()
    c.snap_threshold = 1000.0   # 很大的容差，几乎永不回拉
    c._predict_local(sim.InputState(right=True))
    predicted_x = c.world.players[1].x.to_float()
    c._rollback_and_replay({})   # 权威无输入，玩家应原地
    # 容差足够大 → 不回拉，保持预测位置
    assert c.world.players[1].x.to_float() == predicted_x


def test_snap_middle_rolls_back_only_when_exceeding():
    c = _make_client()
    # 预测右移一步 = MOVE_SPEED ≈ 8 像素（2 tick × 4）
    c._predict_local(sim.InputState(right=True))
    predicted_x = c.world.players[1].x.to_float()
    # 设容差 = 超过预测偏差的一半，应回拉
    c.snap_threshold = (predicted_x - 100.0) / 2.0
    c._rollback_and_replay({})
    assert c.world.players[1].x.to_float() == 100.0
