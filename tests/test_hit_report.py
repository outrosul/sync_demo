"""客户端碰撞上报 + 服务端校验测试：宣称命中经校验才结算。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net.server import Server


class _FakeConn:
    def __init__(self, pid):
        self.player_id = pid

    def send_important(self, msg):
        pass

    def send_unimportant(self, msg):
        pass


def _setup():
    server = Server("127.0.0.1", 7930)
    server.world.add_player(1, "p1", 100, G.GROUND_Y)
    server.world.add_player(2, "p2", 120, G.GROUND_Y)
    server.world.players[1].facing = 1   # p1 朝右，面向 p2
    # 归档历史：tick 0 时 p1、p2 都在
    for _ in range(3):
        sim.tick(server.world, {})
        server.history.record(server.world.tick, server.world)
    return server


def test_valid_claim_authorized():
    server = _setup()
    # p1 朝向右，p2 在前方 20 像素 → 合法命中
    hp0 = server.world.players[2].hp
    server._on_hit_report(_FakeConn(1), {"target_id": 2, "hit": True,
                                         "hit_tick": server.world.tick})
    assert server.world.players[2].hp < hp0


def test_false_claim_rejected():
    server = _setup()
    hp0 = server.world.players[2].hp
    # 让 p1 背对 p2，并把「背对状态」归档进历史，使服务端回退时看到的是背对
    server.world.players[1].facing = -1
    sim.tick(server.world, {})
    server.history.record(server.world.tick, server.world)
    server._on_hit_report(_FakeConn(1), {"target_id": 2, "hit": True,
                                         "hit_tick": server.world.tick})
    assert server.world.players[2].hp == hp0


def test_claimed_false_rejected():
    server = _setup()
    hp0 = server.world.players[2].hp
    # 客户端自己都说没命中（hit=False），即使校验通过也不结算
    server._on_hit_report(_FakeConn(1), {"target_id": 2, "hit": False,
                                         "hit_tick": server.world.tick})
    assert server.world.players[2].hp == hp0
