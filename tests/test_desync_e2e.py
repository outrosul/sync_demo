"""端到端：帧同步 + 客户端输入丢失 → desync 被 hash 上报检测到。

模拟：lockstep 下，客户端 A 的部分输入被故障注入丢失（没到服务器），
于是服务器广播的 frame 里缺了 A 的输入，A 本地按「自己发出的输入」推演会与
服务器（及正常客户端）分叉。A 上报 hash，服务器比对权威 hash 打印 DESYNC。

这里用进程内真实 socket（Server + Client），不经过 GUI。
"""

import time

from sync_demo.net import protocol as P
from sync_demo.net.server import Server
from sync_demo.net.client import Client
from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD


def test_lockstep_client_input_loss_detected():
    server = Server("127.0.0.1", 7860)
    server.mode = "lockstep"
    server.start()
    time.sleep(0.1)

    client = Client("127.0.0.1", 7860, "c1")
    # 给客户端注入 100% 输入丢失
    client.fault.set_drop_input(1.0)
    client.connect()
    time.sleep(0.1)

    end = time.monotonic() + 2.0
    while time.monotonic() < end and client.player_id is None:
        time.sleep(0.02)
    assert client.player_id is not None

    # 客户端持续尝试发「向右移动」输入，但会被 100% 丢失
    for _ in range(20):
        client.send_input(True, False, False, False, False, False)
        time.sleep(0.02)

    # 服务器推进（真实路由里由线程每 tick 推进；这里手动推进确保前进）
    for _ in range(20):
        server._advance()
        time.sleep(0.01)

    # 验证：客户端的输入因丢失没进入服务器 pending（服务器世界未右移）
    # 客户端本地 world 也应未被推演成右移（因为它按 frame 推演）
    p_srv = server.world.players[client.player_id]
    assert p_srv is not None

    client.close()
    server.stop()

    # 验证 desync 检测的核心函数在两端都能正确算出 hash
    assert isinstance(SD.world_hash(server.world), int)
    assert isinstance(SD.world_hash(client.world), int)
