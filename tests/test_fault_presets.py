"""网络分级预设 + 带宽限速测试。"""

import random

from sync_demo.net import fault as F


def test_apply_preset_sets_all_params():
    inj = F.FaultInjector(random.Random(0))
    inj.apply_preset("bad")
    assert inj.latency_ms == 1500
    assert inj.loss_rate == 0.10
    assert inj.throttle_kbps == 150
    assert inj.jitter_ms == 200


def test_apply_preset_unknown_returns_false():
    inj = F.FaultInjector(random.Random(0))
    assert inj.apply_preset("nonexistent") is False


def test_apply_preset_known_returns_true():
    inj = F.FaultInjector(random.Random(0))
    assert inj.apply_preset("good") is True


def test_throttle_default_off():
    inj = F.FaultInjector(random.Random(0))
    assert inj.throttle_kbps == 0    # 0 = 不限速


def test_set_throttle_clamps_negative():
    inj = F.FaultInjector(random.Random(0))
    inj.set_throttle(-100)
    assert inj.throttle_kbps == 0
