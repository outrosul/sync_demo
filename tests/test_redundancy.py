"""冗余发包测试：冗余数、去重、抗丢包（任一份即还原）。"""

from sync_demo.net import redundancy as RD


def test_zero_redundancy_sends_one():
    sender = RD.RedundantSender(redundancy=0)
    packets = sender.encode(b"hello")
    assert len(packets) == 1


def test_redundancy_two_sends_three():
    sender = RD.RedundantSender(redundancy=2)
    packets = sender.encode(b"hello")
    assert len(packets) == 3   # 1 原始 + 2 冗余


def test_receiver_reconstructs_from_any_packet():
    sender = RD.RedundantSender(redundancy=2)
    packets = sender.encode(b"hello")
    recv = RD.RedundantReceiver()
    # 只收到一份（模拟其它两份丢了），也能还原
    seq, data, is_new = recv.decode(packets[1])
    assert is_new is True
    assert data == b"hello"


def test_receiver_dedups_duplicates():
    sender = RD.RedundantSender(redundancy=2)
    packets = sender.encode(b"hello")
    recv = RD.RedundantReceiver()
    recv.decode(packets[0])
    # 第二个副本到达 → 去重，返回 is_new=False
    seq, data, is_new = recv.decode(packets[1])
    assert is_new is False
    assert data is None


def test_consecutive_packets_distinct_seq():
    sender = RD.RedundantSender(redundancy=1)
    p1 = sender.encode(b"a")
    p2 = sender.encode(b"b")
    recv = RD.RedundantReceiver()
    s1, d1, _ = recv.decode(p1[0])
    s2, d2, _ = recv.decode(p2[0])
    assert d1 == b"a"
    assert d2 == b"b"
    assert s1 != s2
