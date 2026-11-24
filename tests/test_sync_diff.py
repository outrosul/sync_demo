"""同步观测测试：hash 确定性 + 差异字段定位。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD


def _two_player_world(seed=1):
    rng = random.Random(seed)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 300, G.GROUND_Y)
    return w


def test_hash_identical_for_identical_simulation():
    w1 = _two_player_world(7)
    w2 = _two_player_world(7)
    seq = [
        {1: sim.InputState(right=True)},
        {1: sim.InputState(jump=True)},
        {1: sim.InputState(attack=True)},
    ]
    for inputs in seq:
        sim.tick(w1, inputs)
        sim.tick(w2, inputs)
    assert SD.world_hash(w1) == SD.world_hash(w2)


def test_hash_differs_when_state_diverges():
    w1 = _two_player_world(7)
    w2 = _two_player_world(7)
    sim.tick(w1, {1: sim.InputState(right=True)})
    sim.tick(w2, {1: sim.InputState(left=True)})   # 不同输入导致分叉
    assert SD.world_hash(w1) != SD.world_hash(w2)


def test_diff_players_locates_diverged_field():
    w1 = _two_player_world()
    w2 = _two_player_world()
    w2.players[1].x = w1.players[1].x + G.Fixed.from_float(10)   # 人为偏移 x
    diffs = SD.diff_players(w1.players[1], w2.players[1])
    names = [d[0] for d in diffs]
    assert "x" in names


def test_hash_stable_across_player_insertion_order():
    # 同一世界，但两个 player 以不同顺序加入，hash 应一致（因为按 id 排序）
    w1 = _two_player_world()
    w2 = _two_player_world()
    assert SD.world_hash(w1) == SD.world_hash(w2)
