"""延迟埋点：测量「输入产生 → 发送 → 服务器接收 → 转发 → 客户端接收&消耗」各段耗时。

**为什么需要**：优化延迟前先要「看到」延迟在哪。参照弱网自适配文档的埋点方案，把一次
输入在网络链路各节点的墙上时间戳记录下来，比赛/会话结束后统计各段耗时。

**本模块职责**（纯数据可测，不碰真实网络）：
- ``LatencyProbe``：一个可开关的埋点收集器。开启后，消息体里可附加 ``probe_id`` + ``t_*``
  时间戳，各节点用 ``mark(probe_id, stage)`` 记录到达时间。
- 结束时 ``report()`` 汇总各段耗时（stage 间的时间差）。

各段 stage：``input_created``（输入产生）→ ``client_sent``（客户端发出）→ ``server_recv``
（服务器收到）→ ``server_sent``（服务器转发）→ ``client_recv``（客户端收到）。

**开关设计**：``enabled`` 默认关（埋点有性能开销）；打开后每次输入带 probe_id，记录墙钟，
`report()` 打印各段统计。时钟可注入（可测）。
"""


class LatencyProbe:
    """延迟埋点收集器（可开关）。

    :param clock: 可注入时钟（秒），默认 time.monotonic。
    """

    STAGES = ("input_created", "client_sent", "server_recv", "server_sent", "client_recv")

    def __init__(self, clock=None):
        self.clock = clock if clock is not None else _monotonic
        self.enabled = False            # 埋点开关（默认关，避免性能开销）
        self.samples = []               # [(probe_id, stage, wall_clock_sec)]
        self._next_probe_id = 1

    def new_probe(self):
        """开启埋点时，为一次输入申请一个 probe_id（并记录 input_created）。"""
        if not self.enabled:
            return None
        pid = self._next_probe_id
        self._next_probe_id += 1
        self.samples.append((pid, "input_created", self.clock()))
        return pid

    def mark(self, probe_id, stage):
        """在某个网络节点记录该 probe 的到达时间。"""
        if not self.enabled or probe_id is None:
            return
        if stage not in self.STAGES:
            return
        self.samples.append((probe_id, stage, self.clock()))

    def report(self):
        """汇总各段耗时（按 probe_id 分组，相邻 stage 的时间差）。返回 dict。

        key = "stageA->stageB"，value = 所有 probe 该段耗时的平均值（秒）。
        """
        if not self.samples:
            return {}
        # 按 probe_id 分组，统计相邻 stage 差
        per_probe = {}
        for pid, stage, t in self.samples:
            per_probe.setdefault(pid, {})[stage] = t
        stage_deltas = {}
        n = len(self.STAGES)
        for i in range(n - 1):
            a = self.STAGES[i]
            b = self.STAGES[i + 1]
            deltas = []
            for pid, stages in per_probe.items():
                if a in stages and b in stages:
                    deltas.append(stages[b] - stages[a])
            if deltas:
                stage_deltas["{}->{}".format(a, b)] = sum(deltas) / len(deltas)
        return stage_deltas

    def total_probes(self):
        """已记录的 probe 数量（观测用）。"""
        ids = set(pid for pid, _s, _t in self.samples)
        return len(ids)


def _monotonic():
    import time
    return time.monotonic()
