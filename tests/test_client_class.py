"""端到端：用真实的 Client 类（走 select 线程路径）连接服务器。"""

import time

from sync_demo.net import protocol as P
from sync_demo.net.server import Server
from sync_demo.net.client import Client


def test_client_class_connects_and_receives_welcome():
    server = Server("127.0.0.1", 7845)
    server.mode = "state"
    server.start()
    time.sleep(0.1)

    client = Client("127.0.0.1", 7845, "c1")
    client.connect()

    # 等待服务器 welcome 被处理（线程 _run 里走 select）
    end = time.monotonic() + 3.0
    while time.monotonic() < end and client.player_id is None:
        time.sleep(0.02)

    assert client.player_id is not None

    client.close()
    server.stop()
