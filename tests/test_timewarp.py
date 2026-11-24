"""延迟补偿历史世界 buffer 测试：归档 / 回退 / 命中判定。"""

import random

from sync_demo.core import game as G
from sync_demo.net import timewarp as TW


def _two_player_world():
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 120, G.GROUND_Y)
    w.players[1].facing = 1
    return w


def test_buffer_records_and_gets_history():
    buf = TW.WorldHistoryBuffer(capacity=8)
    w = _two_player_world()
    for tick in range(10):
        # 先推进世界（p2 右移），再归档——tick=N 归档的是「第 N+1 次右移后」的状态
        w.players[2].x = w.players[2].x + G.Fixed.from_float(10)
        w.tick = tick
        buf.record(tick, w)
    # 取 tick=3：p2 已右移 4 次（tick 0~3 各一次）= 120 + 40 = 160
    h = buf.get(3)
    assert h is not None
    assert h.players[2].x.to_float() == 160.0


def test_buffer_evicts_oldest():
    buf = TW.WorldHistoryBuffer(capacity=4)
    w = _two_player_world()
    for tick in range(10):
        w.tick = tick
        buf.record(tick, w)
    # 容量 4，tick 0~5 应被淘汰，只剩 6~9
    assert buf.get(0) is None
    assert buf.get(5) is None
    assert buf.get(6) is not None
    assert buf.latest_tick() == 9


def test_nearest_returns_latest_not_exceeding():
    buf = TW.WorldHistoryBuffer(capacity=16)
    w = _two_player_world()
    for tick in (2, 5, 9):
        w.tick = tick
        buf.record(tick, w)
    # nearest(6) 应返回 tick=5（≤6 且最新）
    h = buf.nearest(6)
    assert h is not None
    assert h.tick == 5


def test_rewind_hit_test_hit():
    w = _two_player_world()
    # p2 在 p1 正前方 20 像素，朝向右，应命中
    assert TW.rewind_hit_test(w, 1, 2, max_range=60, max_dy=60) is True


def test_rewind_hit_test_wrong_direction():
    w = _two_player_world()
    w.players[1].facing = -1   # 背对 p2
    assert TW.rewind_hit_test(w, 1, 2, max_range=60, max_dy=60) is False
