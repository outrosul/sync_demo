"""冗余发包（DUP 冗余编码，可开关）：对抗丢包，牺牲带宽换可靠性。

**为什么需要**：UDP 丢包时，若只发一份，丢一个就影响实时体验。冗余发包把同一份数据
发多份（1 原始 + N 冗余），接收端**任收到一份即还原**，其余重复副本去重丢弃。丢包率越高，
冗余越多越能保证「至少一份到达」。代价是**流量陡增**（冗余数 +1 倍），是典型的「牺牲带
宽换延迟/可靠性」。

参照《多人弹球》与《弱网移动优化》的冗余发包思路（那里的冗余数量还按丢包率自适应，
本 demo 简化为**手动开关 + 固定冗余数**，便于学习「有冗余 vs 无冗余」的差异）。

**本模块职责**（纯数据可测，不碰 socket）：
- ``RedundantSender``：发送端把「(seq, data)」分包，按冗余数复制成多份，加序号与
  「该序号总份数」头。
- ``RedundantReceiver``：接收端按 seq 去重；序号「收齐」至少一份即交付；重复丢弃。
"""

import struct

_HEADER = struct.Struct("!II")   # seq(4) + total(4)：序号 + 该序号的总份数


class RedundantSender:
    """发送端：把数据按冗余数复制成多份，附 seq/total 头。

    :param redundancy: 冗余份数（0 = 只发 1 份；N = 1 原始 + N 冗余，共 N+1 份）。
    """

    def __init__(self, redundancy=0):
        self.redundancy = max(0, int(redundancy))
        self._seq = 0

    def encode(self, data):
        """把 bytes 编码成「冗余数份」带序号的包。返回 list[bytes]。"""
        self._seq += 1
        seq = self._seq
        total = self.redundancy + 1
        packets = []
        for _ in range(total):
            packets.append(_HEADER.pack(seq, total) + data)
        return packets


class RedundantReceiver:
    """接收端：按 seq 去重，任收到一份即交付该 seq（重复副本丢弃）。"""

    def __init__(self):
        self._seen_seqs = set()        # 已交付的 seq（去重）

    def decode(self, packet):
        """解码一个冗余包。返回 (seq, data, is_new)；重复包返回 (seq, None, False)。"""
        seq, total = _HEADER.unpack_from(packet)
        data = packet[_HEADER.size:]
        if seq in self._seen_seqs:
            return seq, None, False
        self._seen_seqs.add(seq)
        return seq, data, True
