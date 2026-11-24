"""分布式授权（标记争夺）测试。"""

import time

from sync_demo.net.server import Server
from sync_demo.net import protocol as P


class _FakeConn:
    def __init__(self, pid):
        self.player_id = pid

    def send_important(self, msg):
        pass

    def send_unimportant(self, msg):
        pass


def _setup():
    server = Server("127.0.0.1", 7960)
    server.world.add_player(1, "p1", 100, 440)
    server.world.add_player(2, "p2", 300, 440)
    return server


def test_claim_grants_owner():
    server = _setup()
    assert server.owner_id is None
    server._on_claim(_FakeConn(1), {})
    assert server.owner_id == 1


def test_claim_overwrites_previous_owner():
    server = _setup()
    server._on_claim(_FakeConn(1), {})
    server._on_claim(_FakeConn(2), {})   # p2 争夺，覆盖 p1
    assert server.owner_id == 2


def test_owner_broadcast_after_claim():
    server = _setup()
    # 捕获广播的消息
    captured = []
    def _fake_broadcast(msg):
        captured.append(msg)
    server._broadcast = _fake_broadcast
    server._on_claim(_FakeConn(1), {})
    assert captured and captured[0]["type"] == P.MSG_OWNER
    assert captured[0]["owner_id"] == 1
