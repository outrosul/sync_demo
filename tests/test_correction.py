"""纠错平滑（纠错帧数可调）测试：1帧跳变 vs N帧平滑。"""

from sync_demo.core import game as G
from sync_demo.net import correction as CR


def _fx(v):
    return G.Fixed.from_float(v)


def test_one_frame_correction_jumps():
    """纠错帧数=1：一步到位，立即纠正。"""
    cr = CR.Corrector()
    cr.start_correction((_fx(100), _fx(0)), (_fx(150), _fx(0)), frames=1)
    first = cr.step()
    assert first == (_fx(150), _fx(0))   # 第一帧就到达目标
    assert cr.is_correcting() is False


def test_multi_frame_correction_interpolates():
    """纠错帧数>1：分多帧逐步逼近。"""
    cr = CR.Corrector()
    cr.start_correction((_fx(100), _fx(0)), (_fx(140), _fx(0)), frames=4)
    positions = []
    while True:
        pos = cr.step()
        if pos is None:
            break
        positions.append(pos[0].to_float())
    # 4 帧从 100 逼近 140，最后一帧应到达 140
    assert positions[-1] == 140.0
    # 中间帧应介于 100 和 140 之间（不是一步跳）
    assert positions[0] < 140.0
    assert len(positions) == 4


def test_correction_reaches_exact_target():
    cr = CR.Corrector()
    cr.start_correction((_fx(0), _fx(0)), (_fx(200), _fx(-50)), frames=10)
    last = None
    while True:
        pos = cr.step()
        if pos is None:
            break
        last = pos
    assert last == (_fx(200), _fx(-50))


def test_correction_monotonic_x():
    """纠错过程中 x 单调逼近（不反复）。"""
    cr = CR.Corrector()
    cr.start_correction((_fx(100), _fx(0)), (_fx(200), _fx(0)), frames=10)
    prev = None
    while True:
        pos = cr.step()
        if pos is None:
            break
        if prev is not None:
            assert pos[0].raw >= prev[0].raw
        prev = pos
