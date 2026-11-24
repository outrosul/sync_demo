"""故障注入测试：验证各故障开关行为与可复现性。"""

import random

from sync_demo.net import fault as F


def test_default_no_fault():
    inj = F.FaultInjector(random.Random(0))
    ok, dup = inj.send({"type": "x"})
    assert ok is True
    assert dup is False


def test_loss_drops_some():
    inj = F.FaultInjector(random.Random(1))
    inj.set_loss(1.0)          # 全丢
    ok, _ = inj.send({"type": "x"})
    assert ok is False


def test_latency_defers_delivery():
    inj = F.FaultInjector(random.Random(2))
    inj.set_latency(100)
    inj.delay({"type": "x"})
    # 时间未到 100ms，不应投递
    inj.tick(50)
    assert inj.pending_deliveries() == []
    inj.tick(60)               # 累计 110ms
    assert len(inj.pending_deliveries()) == 1


def test_drop_input_and_duplicate():
    inj = F.FaultInjector(random.Random(3))
    inj.set_drop_input(1.0)
    assert inj.send_input({"type": "i"}) == []
    inj2 = F.FaultInjector(random.Random(4))
    inj2.set_drop_input(0.0)
    inj2.set_duplicate(1.0)
    out = inj2.send_input({"type": "i"})
    assert len(out) == 2


def test_reorder_swaps_pair():
    inj = F.FaultInjector(random.Random(5))
    inj.set_reorder(1.0)
    msgs = [1, 2]
    assert inj.reorder_pair(msgs) == [2, 1]


def test_deterministic_given_seed():
    inj1 = F.FaultInjector(random.Random(9))
    inj1.set_loss(0.5)
    inj2 = F.FaultInjector(random.Random(9))
    inj2.set_loss(0.5)
    r1 = [inj1.send({"t": i})[0] for i in range(20)]
    r2 = [inj2.send({"t": i})[0] for i in range(20)]
    assert r1 == r2
