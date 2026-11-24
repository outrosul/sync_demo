"""回归测试：客户端异常断连（强制 RST）不应让服务器线程崩溃。"""

import socket
import time

from sync_demo.net import protocol as P
from sync_demo.net.server import Server


class _RawClient:
    """最小 TCP 客户端：只连 + 发 hello + 强制 RST 断开。"""

    def __init__(self, port):
        self.port = port
        self.sock = None

    def connect_and_force_reset(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        # 发一条 hello
        self.sock.sendall(P.encode({"type": P.MSG_HELLO, "name": "x"}))
        # 强制 RST：设 SO_LINGER 为 {1,0} 后 close，触发对端 ConnectionResetError
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                             b"\x01\x00\x00\x00\x00\x00\x00\x00")
        self.sock.close()


def test_force_reset_does_not_crash_server():
    server = Server("127.0.0.1", 7870)
    server.mode = "state"
    server.start()
    time.sleep(0.1)

    # 连 3 个客户端并各自强制 RST
    for _ in range(3):
        c = _RawClient(7870)
        c.connect_and_force_reset()

    # 给服务器时间处理 reset，然后验证它仍在运行（没有崩）
    time.sleep(0.3)

    # 服务器线程还活着：再连一个正常客户端应能成功加入
    ok = socket.create_connection(("127.0.0.1", 7870), timeout=3)
    ok.sendall(P.encode({"type": P.MSG_HELLO, "name": "alive"}))
    time.sleep(0.2)
    ok.close()

    # 服务器应该把 3 个异常客户端都 drop 了，且仍能接受新连接
    assert server.running is True

    server.stop()


def test_force_reset_dropped_client_removed():
    server = Server("127.0.0.1", 7871)
    server.mode = "state"
    server.start()
    time.sleep(0.1)

    c = _RawClient(7871)
    c.connect_and_force_reset()
    time.sleep(0.3)

    # 强制 reset 的客户端应被 drop（不再在 clients 里）
    with server.lock:
        remaining = len(server.clients)
    # 3 个客户端都 disconnect 后应无残留（或仅剩 healthy 的，这里只连了 1 个异常）
    assert remaining == 0

    server.stop()
