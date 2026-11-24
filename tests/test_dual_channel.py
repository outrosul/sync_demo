"""双通道分流端到端验证：重要消息走 TCP、非重要消息走 KCP(UDP)。

起一个 ``Server``（lockstep）+ 一个裸双通道客户端，验证：

- 重要消息（hello / welcome）通过 TCP 被对端收到；
- 非重要消息（input）通过 KCP over UDP 被对端收到，且 frame 从 UDP 侧回传。

不依赖 pygame，纯 socket + KCP。
"""

import socket
import time

from sync_demo.net import protocol as P
from sync_demo.net import transport as T
from sync_demo.net.server import Server


class DualClient:
    """裸双通道客户端：TCP（重要）+ KCP over UDP（非重要），记录每条消息来自哪条通道。"""

    def __init__(self, tcp_port, udp_port, conv=1):
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.conv = conv
        self.tcp = None
        self.udp = None
        self.endpoint = None
        self.player_id = None
        # channel -> [messages]，channel 为 "tcp" 或 "udp"
        self.received = {"tcp": [], "udp": []}
        self.tcp_inbuf = b""

    def connect(self):
        """建立 TCP 连接并发 hello（重要），随后建立 UDP 端点。"""
        self.tcp = socket.create_connection(("127.0.0.1", self.tcp_port), timeout=3)
        self.tcp.setblocking(False)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setblocking(False)
        self.endpoint = T.UDPEndpoint(self.udp, ("127.0.0.1", self.udp_port), self.conv)
        self.send_important({"type": P.MSG_HELLO, "name": "dual"})

    def send_important(self, msg):
        """重要消息走 TCP。"""
        self.tcp.send(P.encode(msg))

    def send_unimportant(self, msg):
        """非重要消息走 KCP over UDP。"""
        self.endpoint.send(P.encode(msg))

    def poll(self):
        """读一次 TCP 与 UDP，把收到的消息按通道归类。"""
        # 重要消息：TCP
        try:
            data = self.tcp.recv(65536)
        except BlockingIOError:
            data = b""
        except OSError:
            data = b""
        if data:
            self.tcp_inbuf += data
            msgs, self.tcp_inbuf = P.decode(self.tcp_inbuf)
            self.received["tcp"].extend(msgs)
        # 非重要消息：UDP -> KCP
        while True:
            try:
                udp_data, _ = self.udp.recvfrom(65536)
            except BlockingIOError:
                break
            except OSError:
                break
            self.endpoint.feed(udp_data)
        self.endpoint.update()
        chunk = self.endpoint.recv(65536)
        while chunk:
            for m in P.decode_all(chunk):
                self.received["udp"].append(m)
            chunk = self.endpoint.recv(65536)

    def wait_for(self, mtype, channel, timeout=3.0):
        """在指定通道上等待某类型消息，返回该消息（超时抛 AssertionError）。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.poll()
            for m in self.received[channel]:
                if m.get("type") == mtype:
                    return m
            time.sleep(0.005)
        raise AssertionError("timeout waiting for {} over {}".format(mtype, channel))


def test_dual_channel_splits_important_and_unimportant():
    server = Server("127.0.0.1", 7855)
    server.mode = "lockstep"
    server.start()
    time.sleep(0.1)

    client = DualClient(7855, 7856, conv=1)
    client.connect()

    # 1) 重要消息：hello 已走 TCP；服务端回 welcome 也应走 TCP
    welcome = client.wait_for(P.MSG_WELCOME, "tcp", timeout=3.0)
    client.player_id = welcome.get("player_id")
    assert client.player_id is not None
    # 确认 welcome 没有出现在 UDP 通道，证明重要消息确实只走 TCP
    assert all(m.get("type") != P.MSG_WELCOME for m in client.received["udp"])

    # 2) 非重要消息：input 走 UDP/KCP，服务端收集后回 frame（非重要）也走 UDP
    client.send_unimportant({"type": P.MSG_INPUT, "left": True})
    frame = client.wait_for(P.MSG_FRAME, "udp", timeout=4.0)
    # frame 里应包含本玩家的输入，证明 input 确实经 UDP/KCP 到了服务端
    inputs = frame.get("inputs") or {}
    my_input = inputs.get(str(client.player_id))
    assert my_input is not None
    assert my_input[0] is True         # left=True 已到达服务器

    # 关键是「携带 left=True 的 frame」只能走 UDP：任何经 TCP 回传的 frame 都不应
    # 含本玩家的 left=True（早期未建 UDP 路径时空 frame 走 TCP 回退属预期行为）。
    def _frame_carries_left(tcp_frame):
        data = (tcp_frame.get("inputs") or {}).get(str(client.player_id))
        return data is not None and data[0] is True

    assert not any(_frame_carries_left(m) for m in client.received["tcp"])

    server.stop()
    client.tcp.close()
    client.udp.close()
