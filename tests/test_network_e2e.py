"""端到端联调：真实 socket 起服务器 + 两个客户端，验证同图可见与双模式切换。

不依赖 pygame GUI，直接驱动 net 层。
"""

import socket
import threading
import time

from sync_demo.net import protocol as P
from sync_demo.net.server import Server


class RawClient:
    """裸 socket 客户端，只收发协议、不渲染。"""

    def __init__(self, port):
        self.port = port
        self.sock = None
        self.player_id = None
        self.messages = []
        self.inbuf = b""

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        self.sock.setblocking(False)
        self._send({"type": P.MSG_HELLO, "name": "raw"})
        # 等 welcome 并解析 player_id
        welcome = self._wait_for(P.MSG_WELCOME)
        self.player_id = welcome.get("player_id")

    def _send(self, msg):
        self.sock.send(P.encode(msg))

    def _wait_for(self, mtype, timeout=2.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            msgs, self.inbuf = self._drain()
            for m in msgs:
                self.messages.append(m)
                if m.get("type") == mtype:
                    return m
            time.sleep(0.01)
        raise AssertionError("timeout waiting for {}".format(mtype))

    def _drain(self):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return [], self.inbuf
        if not data:
            return [], self.inbuf
        self.inbuf += data
        return P.decode(self.inbuf)

    def send_input(self, **kw):
        msg = {"type": P.MSG_INPUT}
        msg.update(kw)
        self._send(msg)


def test_two_clients_share_map_state_mode():
    server = Server("127.0.0.1", 7841)
    server.mode = "state"
    server.start()
    time.sleep(0.1)

    c1 = RawClient(7841)
    c2 = RawClient(7841)
    c1.connect()
    c2.connect()

    # 两个客户端都应拿到 welcome 里的 player_id
    assert c1.player_id is not None
    assert c2.player_id is not None
    assert c1.player_id != c2.player_id

    # 状态同步下，服务器持续广播快照；等到两人都出现的快照（同图可见）
    end = time.monotonic() + 3.0
    pids = []
    while time.monotonic() < end:
        snap = c1._wait_for(P.MSG_SNAPSHOT, timeout=0.5)
        pids = [p["player_id"] for p in snap.get("players", [])]
        if c1.player_id in pids and c2.player_id in pids:
            break
    assert c1.player_id in pids
    assert c2.player_id in pids          # 同图可见

    server.stop()
    c1.sock.close()
    c2.sock.close()


def test_switch_mode_broadcasts():
    server = Server("127.0.0.1", 7842)
    server.mode = "state"
    server.start()
    time.sleep(0.1)
    c1 = RawClient(7842)
    c1.connect()

    assert server.set_mode("lockstep") is True
    m = c1._wait_for(P.MSG_MODE, timeout=3.0)
    assert m["mode"] == "lockstep"

    # 帧同步下应收到 frame 消息
    c1.send_input(left=True)
    f = c1._wait_for(P.MSG_FRAME, timeout=3.0)
    assert f["seq"] >= 1

    server.stop()
    c1.sock.close()
