"""纯 Python 最小 KCP 协议。

**为什么要在 UDP 上再做一层可靠**：UDP 本身只是「发了就忘」的无连接传输——
它会丢包、乱序、甚至重复，但代价极低、不握手、不等待确认，因此延迟和开销都小。
游戏对网络延迟敏感（快照/操作要尽快送达），又必须保证不乱不丢（一帧的输入丢了
整局就会错位），所以 KCP 的做法是：保留 UDP 的「快」，在其上补一套「可靠 + 快速
重传」的机制。可靠性和速度通常此消彼长，KCP 的取舍是牺牲少量带宽（重传）来换
更低的重传延迟，而不是像 TCP 那样等待较长的超时。

在不可靠 UDP 之上实现可靠的、面向连接的传输，提供类似 TCP 的 ``send`` / ``recv``
字节流接口，但针对低延迟（快速重传、更激进的重传策略）。

**设计（最小可用，教学可读）**：

- 每个 UDP 数据报是一个 ``Segment``：含 32 位序号 ``seq``、确认号 ``ack`` 等。
- 发送端维护一个发送窗口；接收端对收到的段回 ``ack``（捎带确认）。
- **ARQ**：发送段后启动超时定时器，超时未确认则重传。
- **快速重传**：收到连续 ``dup_ack`` 次重复 ACK（跳过序号）时立即重传，不必等超时。
- **滑动窗口**：接收端按 ``seq`` 顺序交付，乱序的段先缓存，凑齐后按序上交上层。

只实现教学所需的最小字段，不追求与 skywind3000/kcp 逐位对齐。
"""

import struct
import time


# ---- 段格式 ----
# 头部：conv(4) type(1) seq(4) len(4) ack(4)
# 类型：0=数据 1=ACK 2=SYN 3=FIN
# ``!`` 表示网络字节序（大端）：跨平台收发时双方按同一字节序读字段，才不会把
# 多字节整数（如 seq）解释错；``I`` 是 4 字节无符号 int，``i`` 是 4 字节有符号 int。
SEG_HEADER = struct.Struct("!IBIIi")
SEG_TYPE_DATA = 0
SEG_TYPE_ACK = 1
SEG_TYPE_SYN = 2
SEG_TYPE_FIN = 3

# 默认参数
DEFAULT_MTU = 1400
DEFAULT_WINDOW = 32          # 发送窗口大小（未确认段上限）
DEFAULT_TIMEOUT = 0.2        # 单段重传超时（秒）
DEFAULT_FASTACK = 3          # 触发快速重传的重复 ACK 次数
MAX_RETRANSMIT = 64          # 单段最大重传次数


# 时钟抽成函数：生产环境用 time.monotonic()（单调递增，不受系统改时间影响，适合
# 计算「过了多久」）。单独包一层是为了让 __init__ 默认参数能引用它，也让测试可以
# 换成假时钟；见下方 KCP.__init__ 的 clock 参数。
def _monotonic():
    return time.monotonic()


class Segment:
    """一个待发送 / 待确认的数据段，即一次 UDP 传输的载荷与它的协议元数据。

    发送端给每个数据段一个递增的 ``seq``；接收端回 ``ack``（表示「小于这个 ack 的
    序号我都按序收到了」）。``__slots__`` 固定了字段集合，省内存且避免拼写错误。
    """

    __slots__ = ("conv", "type", "seq", "data", "ack", "sent_time", "retransmit")

    def __init__(self, conv, seg_type, seq, data=b"", ack=0):
        self.conv = conv
        self.type = seg_type
        self.seq = seq
        self.data = data
        self.ack = ack
        # sent_time 记录「最近一次把这段发到线上的时刻」，超时判定靠它和 clock 的差。
        self.sent_time = 0.0
        # retransmit 累计这段被重传了几次，超过上限就放弃，避免对端彻底失联时空转。
        self.retransmit = 0

    def encode(self):
        # 头部写死 seq/len/ack 等，再拼上数据体；decode 按同一结构反解，保证收发对称。
        return SEG_HEADER.pack(self.conv, self.type, self.seq, len(self.data), self.ack) + self.data

    @classmethod
    def decode(cls, raw):
        conv, seg_type, seq, length, ack = SEG_HEADER.unpack_from(raw)
        # length 是头里声明的数据长度：只取这一段的数据，避免把同一 UDP 报文里可能的
        # 后续字节（本实现不会拼多个段，但按长度切是自洽且安全的）误当成数据。
        data = raw[SEG_HEADER.size:SEG_HEADER.size + length]
        seg = cls(conv, seg_type, seq, data, ack)
        return seg


def conv_of(raw):
    """只读 KCP 段头里的 conv 字段，供服务器按 conv 路由 UDP 数据报到对应端点。"""
    # 只看前 4 字节（不用完整 decode），又快又省：路由阶段只关心这段该发给哪个会话，
    # 不关心 payload 内容，因此刻意不解包整段。
    return SEG_HEADER.unpack_from(raw)[0]


class KCP:
    """端点状态机（客户端与服务器共用）。"""

    def __init__(self, conv, send_callback, mtu=DEFAULT_MTU, window=DEFAULT_WINDOW, clock=_monotonic):
        self.conv = conv
        # 回调把组装好的 UDP 字节真正发到网络上；KCP 只负责协议，不管底层 socket，
        # 这样同一个 KCP 既能跑在测试里的假 socket 上，也能跑在真实 UDP socket 上。
        self.send_callback = send_callback   # 回调：把 UDP 字节发到对端，签名 func(bytes)
        self.mtu = mtu
        self.window = window
        # 可注入时钟：测试里传一个手动推进的假时钟，就能「快进」超时/重传，不必真等 0.2 秒。
        # 这是把「时间」变成显式依赖，让确定性测试成为可能——真实 sleep 会拖慢且不稳定。
        self.clock = clock                   # 可注入时钟（测试用假时钟加速）

        # 发送侧
        self.send_buf = bytearray()          # 上层待发送的流数据
        self.snd_next = 0                    # 下一个可用的 seq
        # unacked：已发出去但还没被对端确认的段。窗口大小 win == len(unacked) 上限，
        # 用来做流控：对端来不及确认时，这里不许无脑狂发，防止压垮接收端。
        self.unacked = {}                    # seq -> Segment（已发未确认）
        self.timeout = DEFAULT_TIMEOUT
        self.fastack = DEFAULT_FASTACK

        # 接收侧
        self.rcv_next = 0                    # 期望的下一个 seq
        self.rcv_buf = {}                    # seq -> Segment（乱序缓存）
        self.recv_queue = bytearray()        # 已按序、待上层读取的流数据
        self.last_ack = -1                   # 上一条 ACK 里的 ack（判重，用于快速重传计数）

    # ---- 上层接口 ----

    def send(self, data):
        """把流数据送入发送缓冲，等待切成段发出。"""
        # 只入队不立即发：真正切段+发放在 flush/update 里。这样上层可以攒一批再发，
        # 也避免在 send 内部牵扯窗口/重传逻辑，职责单一。
        self.send_buf += data

    def flush(self):
        """把发送缓冲切段发出（不超过窗口）。"""
        # 窗口未满才继续发：len(unacked) 是「在途未确认段」数，达到 window 就停，
        # 等待 update 里的超时重传或对端 ACK 腾出窗口。这是发送端流控的核心条件。
        while self.send_buf and len(self.unacked) < self.window:
            chunk = bytes(self.send_buf[:self.mtu])
            del self.send_buf[:self.mtu]
            self._transmit_data(chunk)

    def recv(self, max_bytes=65536):
        """读取按序到达的流数据（可能返回空）。"""
        # 只从已按序排好的 recv_queue 取，返回空意味着「目前没有按序数据」，
        # 而不是「连接坏了」——上层据此轮询，等到数据齐了自然能取到。
        n = min(max_bytes, len(self.recv_queue))
        if n == 0:
            return b""
        data = bytes(self.recv_queue[:n])
        del self.recv_queue[:n]
        return data

    # ---- 输入处理（收到一个 UDP 段） ----

    def input(self, raw):
        """处理对端发来的一个段。"""
        # 按段类型分派：数据段推进可靠交付，ACK 推进发送侧窗口，SYN/FIN 只是信号。
        # 只回 ACK 而不做连接状态切换，是「最小可用」的有意简化。
        seg = Segment.decode(raw)
        if seg.type == SEG_TYPE_DATA:
            self._on_data(seg)
        elif seg.type == SEG_TYPE_ACK:
            self._on_ack(seg)
        elif seg.type == SEG_TYPE_SYN:
            self._send_ack()
        elif seg.type == SEG_TYPE_FIN:
            self._send_ack()

    def _on_data(self, seg):
        # rcv_next 是「期望的下一个字节的序号」。seq 恰好等于它 => 这段接续得上，可直接交付。
        if seg.seq == self.rcv_next:
            # 按序：直接交付
            self.recv_queue += seg.data
            self.rcv_next = seg.seq + len(seg.data)
            # 这段接上之后，缓存的乱序段里可能有紧跟着的下一段也能交付了，一并补上。
            self._drain_rcv_buf()
        elif seg.seq > self.rcv_next:
            # 乱序到达：缓存等待。塞进按 seq 索引的 dict，等前面的段到了再按序抽出来。
            self.rcv_buf[seg.seq] = seg
        # seq < rcv_next 说明是重复/旧数据，忽略（但会 ack 以帮助对端）
        # 无论哪种情况都要回 ACK：ACK 是对端清理发送窗口、推进重传判断的唯一依据，
        # 漏了 ACK 会让对端误以为丢包并白白重传。
        self._send_ack()

    def _drain_rcv_buf(self):
        # 用 changed 反复重扫：每交付一段，rcv_next 又前进，可能让缓存里又有一段「接续得上」，
        # 所以要循环直到一轮都没有进展为止，才能把连续缓存的段一次性交清。
        changed = True
        while changed:
            changed = False
            for seq in sorted(self.rcv_buf):
                if seq == self.rcv_next:
                    # 恰好接续：弹出并交付，rcv_next 前进到这段之后。
                    seg = self.rcv_buf.pop(seq)
                    self.recv_queue += seg.data
                    self.rcv_next = seq + len(seg.data)
                    changed = True
                elif seq <= self.rcv_next:
                    # 已被覆盖/重复的旧段（其数据已在 rcv_next 之前），直接丢弃避免重复交付。
                    self.rcv_buf.pop(seq)
                    changed = True

    def _on_ack(self, seg):
        # 确认 ack 对应的段
        # 累积确认：ack = N 表示「N 之前的所有字节我都收到了」。所以只要某个段
        # 「末尾字节序号 <= ack」，它整体就被确认了，可以安全移出 unacked 并释放窗口。
        # 用 list(...) 快照再删，是因为一边遍历一边改 dict 会让迭代器失效。
        for seq, s in list(self.unacked.items()):
            # ack 表示序号小于 ack 的都已收到（累积确认）
            if seq + len(s.data) <= seg.ack:
                self.unacked.pop(seq, None)
        # 快速重传判定：重复的 ack 说明中间的段丢了
        # 连续收到一模一样的 ack，说明对端一直在等某一个缺失的段（后面的段都到了、
        # 但它无法按序交付，只能一遍遍报同一个 ack）。连续达到 fastack 次就判定
        # 该段真丢了，不等到超时就立刻重传，省下整整一个超时周期。
        if seg.ack == self.last_ack:
            self._dup_ack_count = getattr(self, "_dup_ack_count", 0) + 1
            if self._dup_ack_count >= self.fastack:
                self._fast_retransmit(seg.ack)
                self._dup_ack_count = 0
        else:
            # ack 变化了 => 说明有新进度，不是「卡在同一处」的重复确认，计数归零重来。
            self.last_ack = seg.ack
            self._dup_ack_count = 0

    def _fast_retransmit(self, ack):
        # 重发所有「seq >= ack」的在途段：因为累积确认到 ack，说明 ack 及其之后的那段
        # 是缺失的（对端首个没收到/没按序收到的），从它往后一并重发最稳妥也最简单。
        for seq, seg in list(self.unacked.items()):
            if seq >= ack:
                self._send_raw(seg)
                seg.sent_time = self.clock()

    # ---- 驱动（定时重传 / 心跳，宿主周期调用） ----

    def update(self):
        """周期调用：超时重传 + 刷新发送缓冲。

        宿主（事件循环）每个 tick 都要调一次，超时重传才能被「驱动」：KCP 没有自己的
        线程/定时器，时间推进全靠外部反复调用 update 并传入 clock 来判断是否超时。
        """
        now = self.clock()
        # 遍历在途段，看谁超时了：sent_time 是上次发出的时刻，超过 timeout 仍未被确认，
        # 说明这一段（或其 ACK）在某个方向丢了，重发一次。
        for seq, seg in list(self.unacked.items()):
            if now - seg.sent_time >= self.timeout:
                # 超过 MAX_RETRANSMIT 次仍无果，判定对端失联：放弃这段，
                # 避免无限重发占用资源；continue 让它离开本次循环（已从 unacked 移除）。
                if seg.retransmit >= MAX_RETRANSMIT:
                    self.unacked.pop(seq, None)
                    continue
                self._send_raw(seg)
                # 重发后重置计时基准、累计重传次数；下一次超时判定以这个新 sent_time 为起点。
                seg.sent_time = now
                seg.retransmit += 1
        # 重传可能释放了窗口（部分段被放弃），这里再补发还囤在 send_buf 里的数据。
        self.flush()

    # ---- 内部 ----

    def _transmit_data(self, data):
        # 给这段数据分配 seq = 当前 snd_next，然后 snd_next 前移 len(data) 个字节，
        # 保证每个数据段占一段互不重叠的序号区间，接收端才能据此按序重组。
        seg = Segment(self.conv, SEG_TYPE_DATA, self.snd_next, data)
        self.snd_next += len(data)
        # 发出前先记入 unacked（进入发送窗口），等 ACK 来确认；确认前这段一直在窗口里。
        self.unacked[seg.seq] = seg
        self._send_raw(seg)

    def _send_ack(self):
        # ACK 段携带 ack = rcv_next，即「我期望的下一个字节」，等价于宣告「这之前都收齐了」。
        # 数据用的 seq 字段这里不参与排序，故随便给（见 snd_ack_seq）。
        seg = Segment(self.conv, SEG_TYPE_ACK, self.snd_ack_seq(), ack=self.rcv_next)
        self.send_callback(seg.encode())

    def snd_ack_seq(self):
        # ACK 段本身不占数据序号，用 0 即可；用独立 seq 会与数据序号冲突，这里简化为 0
        # ACK 段不进发送窗口、不重传、不参与累积确认，所以它的 seq 没有排序意义；
        # 若给它一个会与数据序号混淆的值，接收端可能误判。固定 0 最安全。
        return 0

    def _send_raw(self, seg):
        # 任何「真正上线路」的出口都走这里：统一记录 sent_time 再回调发出。
        # sent_time 是超时重传和快速重传共同依赖的时间锚点，集中在此维护可避免漏记。
        seg.sent_time = self.clock()
        self.send_callback(seg.encode())

    def connect(self):
        """发起 SYN（可选；简化实现直接可用）。"""
        # SYN 段 seq 固定 0：连接握手在这个最小实现里只是「发个信号让对方知道我要连」，
        # 不承担建立双向序号协商，所以序号写死即可。
        seg = Segment(self.conv, SEG_TYPE_SYN, 0)
        self.send_callback(seg.encode())

    def close(self):
        # FIN 同样 seq 固定 0：仅作「我要关闭」的通知，对端 input 收到后回 ACK 即结束。
        seg = Segment(self.conv, SEG_TYPE_FIN, 0)
        self.send_callback(seg.encode())
