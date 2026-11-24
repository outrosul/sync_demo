"""平滑移动测试：插值连续化 + 虚影追踪。"""

from sync_demo.core import game as G
from sync_demo.net import smoothing as SM


def test_interpolate_midpoint():
    tr = SM.InterpolatedTracker()
    tr.push(G.Fixed.from_float(0), G.Fixed.from_float(0))
    tr.push(G.Fixed.from_float(100), G.Fixed.from_float(0))
    x, y = tr.sample(0.5)
    assert x.to_float() == 50.0
    assert y.to_float() == 0.0


def test_interpolate_alpha_zero_is_prev():
    tr = SM.InterpolatedTracker()
    tr.push(G.Fixed.from_float(10), G.Fixed.from_float(20))
    tr.push(G.Fixed.from_float(30), G.Fixed.from_float(60))
    x, y = tr.sample(0.0)
    assert x.to_float() == 10.0
    assert y.to_float() == 20.0


def test_interpolate_alpha_one_is_curr():
    tr = SM.InterpolatedTracker()
    tr.push(G.Fixed.from_float(10), G.Fixed.from_float(20))
    tr.push(G.Fixed.from_float(30), G.Fixed.from_float(60))
    x, y = tr.sample(1.0)
    assert x.to_float() == 30.0
    assert y.to_float() == 60.0


def test_ghost_tracks_multiple_players():
    ghost = SM.GhostTracker()
    ghost.push_snapshot([
        {"player_id": 1, "x": G.Fixed.from_float(100).raw, "y": G.Fixed.from_float(0).raw},
        {"player_id": 2, "x": G.Fixed.from_float(300).raw, "y": G.Fixed.from_float(0).raw},
    ])
    ghost.push_snapshot([
        {"player_id": 1, "x": G.Fixed.from_float(120).raw, "y": G.Fixed.from_float(0).raw},
        {"player_id": 2, "x": G.Fixed.from_float(280).raw, "y": G.Fixed.from_float(0).raw},
    ])
    # p1 向右移，虚影中点应约 110
    x1, _ = ghost.sample(1, 0.5)
    assert x1.to_float() == 110.0
    # p2 向左移，虚影中点应约 290
    x2, _ = ghost.sample(2, 0.5)
    assert x2.to_float() == 290.0
