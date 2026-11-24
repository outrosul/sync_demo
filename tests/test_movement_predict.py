"""移动预测（半个 RTT 先行）测试：摇杆历史 + 半 RTT 累加。"""

from sync_demo.core import game as G
from sync_demo.net import movement_predict as MP


def test_predict_uses_half_rtt_history():
    pred = MP.MovementPredictor()
    pred.on_sync(G.Fixed.from_float(100), G.Fixed.from_float(0))
    # 记录 10 个 tick 的右移（方向 +1）
    for _ in range(10):
        pred.record_input(1)
    # 半个 RTT = 3 tick，预测应把「半 RTT 前」的 3 个右移累加 = +3×MOVE_SPEED
    dx = pred.predict_delta(3)
    assert dx.to_float() == 3.0 * G.MOVE_SPEED.to_float()


def test_predict_no_history_returns_zero():
    pred = MP.MovementPredictor()
    pred.on_sync(G.Fixed.from_float(100), G.Fixed.from_float(0))
    assert pred.predict_delta(5).to_float() == 0.0


def test_predict_adds_delta_to_sync_pos():
    pred = MP.MovementPredictor()
    pred.on_sync(G.Fixed.from_float(100), G.Fixed.from_float(0))
    for _ in range(6):
        pred.record_input(1)
    px, py = pred.predict(3)
    # 预测位置 = 100 + 3×速度
    assert abs(px.to_float() - (100 + 3 * G.MOVE_SPEED.to_float())) < 0.01


def test_record_input_respects_capacity():
    pred = MP.MovementPredictor(history_capacity=5)
    for _ in range(10):
        pred.record_input(1)
    assert len(pred.direction_history) == 5


def test_left_direction_subtracts():
    pred = MP.MovementPredictor()
    pred.on_sync(G.Fixed.from_float(100), G.Fixed.from_float(0))
    for _ in range(4):
        pred.record_input(-1)   # 左移
    dx = pred.predict_delta(4)
    assert dx.to_float() == -4.0 * G.MOVE_SPEED.to_float()
