"""定点数测试：验证「浮点不相等、Fixed 相等」这一核心教学点。"""

from sync_demo.core import game as G
from sync_demo.core.game import Fixed


def test_fixed_construct_and_convert():
    f = Fixed.from_float(0.5)
    assert f.raw == 128            # 0.5 * 256 = 128
    assert f.to_float() == 0.5
    assert f.to_int() == 0


def test_fixed_add_sub_mul_div():
    a = Fixed.from_float(1.5)
    b = Fixed.from_float(0.25)
    assert (a + b).to_float() == 1.75
    assert (a - b).to_float() == 1.25
    # 定点 × 定点：1.5 * 0.25 = 0.375
    assert (a * b).to_float() == 0.375
    # 定点 × 整数：1.5 * 2 = 3.0
    assert (a * 2).to_float() == 3.0
    # 定点 ÷ 整数：1.5 / 2 = 0.75
    assert (a / 2).to_float() == 0.75


def test_float_accumulation_diverges():
    """浮点累加会漂移：0.1 加 10 次不等于 1.0。"""
    acc = 0.0
    for _ in range(10):
        acc += 0.1
    assert acc != 1.0


def test_fixed_accumulation_is_exact():
    """定点累加精确：0.1（＝256 的 1/10 无法整除，但定点值一致）加 10 次后逐位相等。

    说明：0.1 在定点 8 位下存成 round(0.1*256)=26，加 10 次 = 260，永远是确定值 260，
    不会像浮点那样每次舍入累积漂移。关键不是「等于数学 0.1」，而是「每次结果逐位相同」。
    """
    step = Fixed.from_float(0.1)
    acc = Fixed(0)
    for _ in range(10):
        acc = acc + step
    # 同一种定点运算，结果确定且可复现
    expected = Fixed.from_float(0.1) * 10
    assert acc == expected


def test_fixed_deterministic_across_two_runs():
    """定点运算两次运行结果逐位相等（这正是帧同步要的）。"""
    def run():
        v = Fixed.from_float(-14.0)
        g = Fixed.from_float(1.0)
        for _ in range(50):
            v = v + g
        return v.raw
    assert run() == run()
