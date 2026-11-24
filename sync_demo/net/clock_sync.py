"""时钟同步（客户端-服务端对时，NTP 风格）。

**为什么需要**：客户端预测、延迟补偿、插值都依赖「客户端/服务器时间戳基本一致」。但两端
时钟天然有偏移（且各自独立漂移），需要周期性对时算出「客户端时钟偏移」，把本地时间换算成
服务器时间。

参照 ARPG 篇的时钟同步流程：
1. 客户端把本地时间 t0 附在时间请求包发服务器；
2. 服务器附上服务器时间戳 t1 回包；
3. 客户端收到后：延迟 = (now - t0) / 2；时差 = now - t1；正确时钟偏移 = 时差 + 半个延迟；
4. 第一条立刻用；
5. 重复多次采样，排序取中位数；
6. 丢弃与中位数偏差过大（超 1 个标准差或中位数 1.5 倍，用于过滤 TCP 重传）的样例，剩余取算术平均。

**本模块职责**（纯数据可测，不碰 socket/渲染）：
- ``ClockSync``：收集每次「往返对时」的样本（延迟、服务器时间），按上述算法算出稳定偏移。
- ``offset()``：当前估测的「本地时钟 - 服务器时钟」偏移（秒）。
- ``to_server_time(local_now)``：把本地时刻换算成服务器时刻。

时钟可注入（可测）。
"""


class ClockSync:
    """客户端-服务端对时器。"""

    def __init__(self, clock=None):
        self.clock = clock if clock is not None else _monotonic
        self.samples = []          # [(offset_秒, rtt_秒)]，offset = 本地 - 服务器
        self._offset = None        # 当前估测偏移

    def add_sample(self, send_local, server_time, recv_local):
        """记录一次往返对时样本。

        :param send_local: 发送请求时的本地时钟。
        :param server_time: 服务器回的时间戳。
        :param recv_local: 收到回包时的本地时钟。
        """
        rtt = recv_local - send_local
        # offset = 本地时钟 - 服务器时钟 = (recv_local - server_time) - rtt/2
        # 即：用「收到时本地时刻」减「服务器时刻」再减去半个 RTT，把服务器时间对齐到「发送中点」
        offset = (recv_local - server_time) - rtt / 2.0
        self.samples.append((offset, rtt))
        # 第一条立刻用于更新
        if self._offset is None:
            self._offset = offset

    def _recompute(self):
        """按「排序取中位数 → 丢弃偏离过大的样例 → 取算术平均」重算偏移。"""
        if not self.samples:
            return
        offsets = sorted(o for o, _r in self.samples)
        n = len(offsets)
        median = offsets[n // 2]
        # 丢弃与中位数偏差超过 1.5 倍中位数的样例（过滤 TCP 重传等异常）
        keep = [o for o in offsets if abs(o - median) <= median * 1.5 + 1e-9]
        if keep:
            self._offset = sum(keep) / len(keep)
        else:
            self._offset = median

    def offset(self, recompute=False):
        """返回估测的「本地 - 服务器」时钟偏移（秒）。recompute=True 时重算。"""
        if recompute:
            self._recompute()
        return self._offset if self._offset is not None else 0.0

    def offset_ms(self, recompute=False):
        return self.offset(recompute) * 1000.0

    def to_server_time(self, local_now):
        """把本地时刻换算成服务器时刻。"""
        return local_now - self.offset()

    def sample_count(self):
        return len(self.samples)


def _monotonic():
    import time
    return time.monotonic()
