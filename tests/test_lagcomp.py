"""延迟补偿（Lag Compensation）测试：回退历史世界裁判命中，网络差者不吃亏。"""

import time

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net.server import Server
from sync_demo.net import protocol as P


class _FakeConn:
    def __init__(self, pid):
        self.player_id = pid
        self.name = "c{}".format(pid)

    def send_important(self, msg):
        pass

    def send_unimportant(self, msg):
        pass


def _setup_server_with_two_players():
    """构造一个 state 服务器（不启动线程），手动控制世界做精确的延迟补偿验证。"""
    server = Server("127.0.0.1", 7920)
    server.mode = "state"
    # 注意：不调用 server.start()，避免后台线程自动推进世界与手动 tick 叠加
    server.world.add_player(1, "p1", 100, G.GROUND_Y)
    server.world.add_player(2, "p2", 120, G.GROUND_Y)
    server.world.players[1].facing = 1
    # p2 先静止 2 帧（在 120，p1 看到的画面），再走 18 帧到 192。
    # 这样历史里既有「p2 在 120 静止」的帧（回退目标），也有「p2 走远」的当前帧。
    for _ in range(2):
        sim.tick(server.world, {})
        server.history.record(server.world.tick, server.world)
    for _ in range(18):
        sim.tick(server.world, {2: sim.InputState(right=True)})
        server.history.record(server.world.tick, server.world)
    return server


def test_lag_comp_hit_when_target_moved_away():
    server = _setup_server_with_two_players()
    # p2 走了 18 帧×4px = 72px，从 120 → 192，已远超攻击范围 60。
    # 不补偿：当前世界 p2 距 p1 约 92 像素 > 攻击范围 60，打不中。
    server.lag_comp_enabled = False
    server.world.players[2].hp = G.MAX_HP
    server.world.players[2].alive = True
    server._on_attack_request(_FakeConn(1), {"target_id": 2})
    hp_no_comp = server.world.players[2].hp

    # 补偿：设 p1 延迟 0.9s ≈ 18 tick，回退到「p1 看到 p2 还在 120 附近」的历史世界
    server.lag_comp_enabled = True
    server.client_latency[1] = 0.9
    server.world.players[2].hp = G.MAX_HP
    server.world.players[2].alive = True
    server._on_attack_request(_FakeConn(1), {"target_id": 2})
    hp_comp = server.world.players[2].hp

    # 补偿后 hp 下降（回退命中扣血），不补偿时 hp 不变（按当前世界打空）
    assert hp_comp < hp_no_comp
    server.stop()


def test_lag_comp_disabled_misses_moved_target():
    server = _setup_server_with_two_players()
    server.lag_comp_enabled = False
    server.world.players[2].hp = G.MAX_HP
    server._on_attack_request(_FakeConn(1), {"target_id": 2})
    # 不补偿：p2 已走远，打不中，hp 不变
    assert server.world.players[2].hp == G.MAX_HP
    server.stop()
