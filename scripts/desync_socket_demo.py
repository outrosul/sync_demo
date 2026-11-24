"""真实 socket 下的 desync 检测演示。

起一个 lockstep 服务器 + 一个客户端，人为让客户端世界与服务器世界分叉
（客户端本地多推演几帧），再触发客户端 hash 上报，验证服务器能打印 DESYNC。

这是「制造并观测不同步」在真实网络链路上的最小闭环。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_demo.net import protocol as P
from sync_demo.net.server import Server
from sync_demo.net.client import Client
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD


def main():
    server = Server("127.0.0.1", 7865)
    server.mode = "lockstep"
    server.start()
    time.sleep(0.1)

    client = Client("127.0.0.1", 7865, "c1")
    client.connect()
    time.sleep(0.1)

    end = time.monotonic() + 2.0
    while time.monotonic() < end and client.player_id is None:
        time.sleep(0.02)
    print("client player_id =", client.player_id)

    # 客户端本地多推演 3 帧（制造与权威的分叉）
    for _ in range(3):
        sim.tick(client.world, {client.player_id: sim.InputState(right=True)})

    # 服务器期望的客户端 world 未推演这几帧 → hash 必然不同
    h_client = SD.world_hash(client.world)
    h_server = SD.world_hash(server.world)
    print("client hash =", h_client)
    print("server hash =", h_server)
    print("diverged:", h_client != h_server)

    # 走服务器真实的 hash 检测路径：模拟客户端上报
    class FakeConn:
        player_id = client.player_id
        name = "c1"

    server._on_client_hash(FakeConn(), {"hash": h_client, "tick": client.world.tick})

    client.close()
    server.stop()


if __name__ == "__main__":
    main()
