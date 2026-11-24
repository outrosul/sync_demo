"""Jitter Buffer（抖动缓冲）测试：缓存水位、动态插值速度、耗尽降级。"""

from sync_demo.core import game as G
from sync_demo.net import jitter_buffer as JB


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_push_and_water_level():
    clock = FakeClock()
    buf = JB.JitterBuffer(target_interval=0.05, clock=clock)
    for i in range(5):
        buf.push(G.Fixed.from_float(100 + i), G.Fixed.from_float(0))
    assert buf.water_level() == 5


def test_empty_buffer_degrades():
    clock = FakeClock()
    buf = JB.JitterBuffer(clock=clock)
    x, y, degraded = buf.sample()
    assert degraded is True


def test_low_water_slows_down():
    """水位低 → alpha 小 → 慢速消费（取最早帧）。"""
    clock = FakeClock()
    buf = JB.JitterBuffer(target_interval=0.05, clock=clock)
    buf.push(G.Fixed.from_float(100), G.Fixed.from_float(0))
    buf.push(G.Fixed.from_float(110), G.Fixed.from_float(0))
    alpha = buf._dynamic_alpha()
    assert alpha < 1.0   # 只有 2 帧，未到理想缓冲，速度被压低


def test_high_water_speeds_up():
    """水位高 → alpha 逼近 1 → 全速消费最新。"""
    clock = FakeClock()
    buf = JB.JitterBuffer(target_interval=0.05, max_buffer=64, clock=clock)
    for i in range(20):
        buf.push(G.Fixed.from_float(100 + i), G.Fixed.from_float(0))
    alpha = buf._dynamic_alpha()
    assert alpha >= 0.99


def test_max_buffer_evicts_oldest():
    clock = FakeClock()
    buf = JB.JitterBuffer(target_interval=0.05, max_buffer=4, clock=clock)
    for i in range(10):
        buf.push(G.Fixed.from_float(100 + i), G.Fixed.from_float(0))
    # 上限 4，只保留最近 4 帧
    assert buf.water_level() == 4


def test_estimate_position_returns_latest():
    clock = FakeClock()
    buf = JB.JitterBuffer(clock=clock)
    buf.push(G.Fixed.from_float(100), G.Fixed.from_float(0))
    buf.push(G.Fixed.from_float(120), G.Fixed.from_float(0))
    pos = buf.estimate_position()
    assert pos is not None
    assert pos[0].to_float() == 120.0
