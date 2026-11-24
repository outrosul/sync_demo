"""KCP 最小实现测试：可靠交付 + 乱序重排 + 丢包重传。

用「假时钟」驱动 KCP，避免真实 sleep，测试秒级完成且确定。
"""

from sync_demo.net import kcp as K


class FakeClock:
    """可手动推进的假单调时钟。"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class Wire:
    """模拟有损的 UDP 通道：可注入丢包。"""

    def __init__(self, loss=0.0):
        self.loss = loss
        self.queue_a_to_b = []
        self.queue_b_to_a = []
        self.a_clock = FakeClock()
        self.b_clock = FakeClock()

    def a_to_b(self, data):
        _maybe_drop(self.queue_a_to_b, data, self.loss)

    def b_to_a(self, data):
        _maybe_drop(self.queue_b_to_a, data, self.loss)


def _maybe_drop(queue, data, loss):
    import random
    if random.random() < loss:
        return
    queue.append(data)


def _make_pair(loss=0.0):
    wire = Wire(loss)
    a = K.KCP(conv=1, send_callback=wire.a_to_b, clock=wire.a_clock)
    b = K.KCP(conv=1, send_callback=wire.b_to_a, clock=wire.b_clock)
    return a, b, wire


def _pump(a, b, wire, rounds=200):
    """来回泵若干轮，每轮把两个假时钟各推进一次超时时长，确保重传发生。"""
    for _ in range(rounds):
        for data in wire.queue_a_to_b:
            b.input(data)
        wire.queue_a_to_b = []
        for data in wire.queue_b_to_a:
            a.input(data)
        wire.queue_b_to_a = []
        a.update()
        b.update()
        # 推进假时钟，触发 KCP 超时重传（0.2s）
        wire.a_clock.advance(K.DEFAULT_TIMEOUT)
        wire.b_clock.advance(K.DEFAULT_TIMEOUT)


def test_kcp_reliable_delivery():
    a, b, wire = _make_pair(loss=0.0)
    payload = b"hello kcp " * 100
    a.send(payload)
    a.flush()
    _pump(a, b, wire, rounds=200)
    received = b""
    chunk = b.recv(65536)
    while chunk:
        received += chunk
        chunk = b.recv(65536)
    assert received == payload


def test_kcp_reorders_out_of_order():
    def _discard(data):
        return None

    b = K.KCP(1, send_callback=_discard)
    # 直接构造两个数据段，seq 分别为 0 与 4（AAAA 长 4），手动颠倒送入 b
    seg0 = K.Segment(1, K.SEG_TYPE_DATA, 0, b"AAAA")
    seg1 = K.Segment(1, K.SEG_TYPE_DATA, 4, b"BBBB")
    # 先送 seg1（乱序），再送 seg0
    b.input(seg1.encode())
    b.input(seg0.encode())
    received = b""
    chunk = b.recv(65536)
    while chunk:
        received += chunk
        chunk = b.recv(65536)
    assert received == b"AAAABBBB"          # 按序重排后交付


def test_kcp_recovers_from_loss():
    # 30% 丢包率下仍能靠超时重传可靠交付
    a, b, wire = _make_pair(loss=0.3)
    payload = b"x" * 400
    a.send(payload)
    a.flush()
    _pump(a, b, wire, rounds=300)
    received = b""
    chunk = b.recv(65536)
    while chunk:
        received += chunk
        chunk = b.recv(65536)
    assert received == payload
