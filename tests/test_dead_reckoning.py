"""Dead Reckoning（航位推算）测试：收包用准确位置，没包用速度外推，可复现。"""

from sync_demo.core import game as G
from sync_demo.net import smoothing as SM


class FakeClock:
    """可手动推进的假单调时钟（秒）。"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_dr_uses_exact_position_right_after_update():
    clock = FakeClock()
    dr = SM.DeadReckoner(clock=clock)
    # 位置/速度都用定点（from_float 表示像素语义；内部按 raw 定点存储）
    dr.update(G.Fixed.from_float(100), G.Fixed.from_float(0),
              G.Fixed.from_float(4), G.Fixed.from_float(0))
    # 刚 update，时间未推进，预测应等于准确位置
    px, py = dr.predict()
    assert px.to_float() == 100.0
    assert py.to_float() == 0.0


def test_dr_extrapolates_using_velocity():
    clock = FakeClock()
    dr = SM.DeadReckoner(clock=clock)
    dr.update(G.Fixed.from_float(100), G.Fixed.from_float(0),
              G.Fixed.from_float(4), G.Fixed.from_float(0))
    clock.t = 1.5             # 1.5 秒没收到新包
    px, py = dr.predict()
    # 预测 = 100 + 4*1.5 = 106（定点截断可能略差，用近似）
    assert abs(px.to_float() - 106.0) < 1.0


def test_dr_updates_reset_anchor():
    clock = FakeClock()
    dr = SM.DeadReckoner(clock=clock)
    dr.update(G.Fixed.from_float(100), G.Fixed.from_float(0),
              G.Fixed.from_float(4), G.Fixed.from_float(0))
    clock.t = 0.5
    # 收到新包：位置跳到 200，速度反向向左
    dr.update(G.Fixed.from_float(200), G.Fixed.from_float(0),
              G.Fixed.from_float(-4), G.Fixed.from_float(0))
    px, py = dr.predict()
    assert abs(px.to_float() - 200.0) < 1.0   # 新锚点，外推重置


def test_dr_no_estimate_returns_origin():
    dr = SM.DeadReckoner(clock=FakeClock())
    px, py = dr.predict()
    assert px.to_float() == 0.0
    assert py.to_float() == 0.0
    assert dr.has_estimate() is False


def test_dr_deterministic_given_clock():
    def run():
        clock = FakeClock()
        dr = SM.DeadReckoner(clock=clock)
        dr.update(100, 0, G.Fixed.from_float(4), G.Fixed.from_float(0))
        clock.t = 1.5
        return dr.predict()[0].raw
    assert run() == run()   # 同一假时钟序列，预测逐位一致
