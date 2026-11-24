"""回放系统测试：录制、序列化、重放一致性。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD
from sync_demo.net import replay as RP


def _two_player_world(seed=1):
    rng = random.Random(seed)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 300, G.GROUND_Y)
    return w


def _input_sequence():
    return [
        {1: sim.InputState(right=True)},
        {1: sim.InputState(jump=True)},
        {2: sim.InputState(left=True)},
        {1: sim.InputState(attack=True), 2: sim.InputState(right=True)},
    ]


def test_recorder_disabled_skips():
    rec = RP.Recorder()
    rec.record({1: sim.InputState(right=True)})
    assert rec.frame_count() == 0   # 默认关，不记录


def test_recorder_enabled_records():
    rec = RP.Recorder()
    rec.enabled = True
    for inputs in _input_sequence():
        rec.record(inputs)
    assert rec.frame_count() == 4


def test_replay_reproduces_same_state():
    # 原始世界推演一遍
    w1 = _two_player_world()
    seq = _input_sequence()
    for inputs in seq:
        sim.tick(w1, inputs)
    # 录制 + 重放到新世界
    rec = RP.Recorder()
    rec.enabled = True
    for inputs in seq:
        rec.record(inputs)
    w2 = _two_player_world()
    RP.replay(w2, rec.frames)
    assert SD.world_hash(w1) == SD.world_hash(w2)


def test_dump_load_roundtrip():
    rec = RP.Recorder()
    rec.enabled = True
    for inputs in _input_sequence():
        rec.record(inputs)
    data = rec.dump()
    rec2 = RP.Recorder()
    rec2.load(data)
    assert rec2.frame_count() == rec.frame_count()
    assert rec2.dump() == data
