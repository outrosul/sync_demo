"""双通道传输：TCP（重要数据）+ KCP（非重要数据）。

**通道划分原则**：

- **重要数据走 TCP**：握手、欢迎、模式切换、权威快照、断线 —— 必须可靠且保序，
  丢一个都不能接受（否则状态撕裂）。
- **非重要数据走 KCP（UDP 之上）**：高频输入、帧同步指令 —— 允许丢失 / 乱序，
  靠下一帧或预测补偿；KCP 用尽快的重传换取低延迟。

本模块把「选哪条通道」封装起来，``server`` / ``client`` 只调用 ``send_important`` /
``send_unimportant``，不直接碰 TCP socket 或 KCP 端点。

KCP 端点是纯 Python 实现（``net.kcp.KCP``），不引入编译依赖。
"""

import socket
import threading

from sync_demo.net import kcp as K
from sync_demo.net import protocol as P


# 走 KCP（非重要）的消息类型
UNIMPORTANT_TYPES = {P.MSG_INPUT, P.MSG_FRAME}


def classify(msg):
    """判断一条消息是否「非重要」（走 KCP）。"""
    return msg.get("type") in UNIMPORTANT_TYPES


class UDPEndpoint:
    """一个 KCP over UDP 端点（服务器按 (addr, conv) 区分多个客户端；客户端单一端点）。"""

    def __init__(self, sock, addr, conv):
        self.addr = addr
        self.conv = conv
        self.kcp = K.KCP(conv, send_callback=self._send_udp)
        self.sock = sock          # 共享的 UDP socket（服务器复用；客户端独占）

    def _send_udp(self, data):
        # 服务器端在尚未学到客户端 UDP 源地址前 addr 为 None，暂不发送
        if self.addr is None:
            return
        self.sock.sendto(data, self.addr)

    def feed(self, data):
        """把收到的 UDP 数据送入 KCP 端点。"""
        self.kcp.input(data)

    def send(self, payload):
        """:param payload: 业务消息的字节。"""
        self.kcp.send(payload)
        self.kcp.flush()

    def update(self):
        self.kcp.update()

    def recv(self, max_bytes=65536):
        return self.kcp.recv(max_bytes)

    def pending(self):
        return len(self.kcp.recv_queue) + len(self.kcp.recv_buf)


class Transport:
    """双通道传输抽象。

    服务端用法：一个 ``Transport`` 持有 TCP listener + UDP socket + 到各客户端的
    ``ClientLink``（TCP socket + UDPEndpoint）。
    客户端用法：一个 ``Transport`` 持有单个 TCP socket + 单个 UDPEndpoint。
    """

    def __init__(self):
        self.tcp_sock = None
        self.udp_sock = None
        self.lock = threading.Lock()

    # ---- 客户端侧 ----

    @classmethod
    def connect_client(cls, host, tcp_port, udp_port, conv):
        t = cls()
        tcp = socket.create_connection((host, tcp_port), timeout=5)
        tcp.setblocking(False)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setblocking(False)
        t.tcp_sock = tcp
        t.udp_sock = udp
        t.udp_endpoint = UDPEndpoint(udp, (host, udp_port), conv)
        return t

    def send_important(self, msg):
        self.tcp_sock.send(P.encode(msg))

    def send_unimportant(self, msg):
        self.udp_endpoint.send(P.encode(msg))

    def poll_important(self):
        """非阻塞读 TCP，返回消息列表。"""
        messages = []
        try:
            data = self.tcp_sock.recv(65536)
        except BlockingIOError:
            return messages
        except OSError:
            return messages
        if not data:
            return messages
        # 逐条按行解出（协议是行分隔 JSON）
        decoded, _ = P.decode_all(data)
        messages.extend(decoded)
        return messages

    def poll_unimportant(self):
        """非阻塞读 UDP → KCP，返回已按序交付的消息列表。"""
        messages = []
        try:
            data, _ = self.udp_sock.recvfrom(65536)
        except BlockingIOError:
            data = None
        except OSError:
            data = None
        if data:
            self.udp_endpoint.feed(data)
        self.udp_endpoint.update()
        chunk = self.udp_endpoint.recv(65536)
        while chunk:
            decoded, _ = P.decode_all(chunk)
            messages.extend(decoded)
            chunk = self.udp_endpoint.recv(65536)
        return messages


class ClientLink:
    """服务器侧：到某个客户端的 TCP socket + UDP 端点。"""

    def __init__(self, tcp_sock, addr, udp_endpoint):
        self.tcp_sock = tcp_sock
        self.addr = addr
        self.udp_endpoint = udp_endpoint
        self.tcp_inbuf = b""

    def send_important(self, msg):
        self.tcp_sock.send(P.encode(msg))

    def send_unimportant(self, msg):
        self.udp_endpoint.send(P.encode(msg))
