"""故障注入：可开关地制造网络异常，用于研究「不同步」。

支持的故障（各有独立开关 + 参数）：

- ``latency``：固定延迟（毫秒），消息延后 ``delay_ms`` 才发出。
- ``loss``：丢包率（0~1），随机丢弃消息。
- ``reorder``：乱序，把相邻消息以一定概率交换。
- ``duplicate``：输入重复，以一定概率重复发送一条输入。
- ``drop_input``：输入丢失，以一定概率直接丢弃输入帧。
- ``jitter``：延迟抖动，在延迟基础上加随机抖动（毫秒）。
- ``throttle``：带宽上限（kbps），模拟网络限速。
- ``preset``：网络分级预设（极好/良/一般/差一档切换）。

这些故障**只在网络层生效**，不改变逻辑层规则；这样能干净地观察「同样确定的逻辑
在不同网络条件下如何 desync」。

实现：一个 ``FaultInjector`` 挂在发送/接收路径上，``send(msg)`` 返回是否放行，
``recv(msg)`` 返回是否/如何投递。用显式 ``random.Random`` 保证可复现。
"""

import random

# 网络分级预设（参照弱网自适配文档的环境分级）：(延迟ms, 丢包率, 抖动ms, 带宽kbps, 乱序率)
NETWORK_PRESETS = {
    "perfect": {"latency_ms": 7, "loss_rate": 0.0, "jitter_ms": 0, "throttle_kbps": 50000, "reorder_rate": 0.0},
    "good":    {"latency_ms": 50, "loss_rate": 0.01, "jitter_ms": 5, "throttle_kbps": 5000, "reorder_rate": 0.0},
    "general": {"latency_ms": 150, "loss_rate": 0.02, "jitter_ms": 20, "throttle_kbps": 2000, "reorder_rate": 0.01},
    "bad":     {"latency_ms": 1500, "loss_rate": 0.10, "jitter_ms": 200, "throttle_kbps": 150, "reorder_rate": 0.05},
}


class FaultInjector:
    """延迟 / 丢包 / 乱序 / 复制 / 丢输入 / 抖动的可开关注入器。"""

    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random.Random(0)
        # 开关与参数（默认全关 = 正常网络）
        self.latency_ms = 0        # 固定延迟（0 = 关）
        self.loss_rate = 0.0       # 丢包率
        self.reorder_rate = 0.0    # 乱序率
        self.duplicate_rate = 0.0  # 输入重复率
        self.drop_input_rate = 0.0 # 输入丢失率
        self.jitter_ms = 0         # 延迟抖动幅度
        self.throttle_kbps = 0     # 带宽上限 kbps（0 = 不限速）
        self.cap_buffer_ms = 500   # 带宽限速缓冲（ms），模拟「限速队列积压」

        # 延迟队列：[(投递时间, 消息)]
        self._delay_queue = []
        self._now_ms = 0

    def set_latency(self, ms):
        self.latency_ms = max(0, int(ms))

    def set_loss(self, rate):
        self.loss_rate = max(0.0, min(1.0, rate))

    def set_reorder(self, rate):
        self.reorder_rate = max(0.0, min(1.0, rate))

    def set_duplicate(self, rate):
        self.duplicate_rate = max(0.0, min(1.0, rate))

    def set_drop_input(self, rate):
        self.drop_input_rate = max(0.0, min(1.0, rate))

    def set_jitter(self, ms):
        self.jitter_ms = max(0, int(ms))

    def set_throttle(self, kbps):
        self.throttle_kbps = max(0, int(kbps))

    def apply_preset(self, name):
        """一键套用网络分级预设（极好 / 良 / 一般 / 差）。"""
        presets = NETWORK_PRESETS.get(name)
        if presets is None:
            return False
        self.latency_ms = presets["latency_ms"]
        self.loss_rate = presets["loss_rate"]
        self.jitter_ms = presets["jitter_ms"]
        self.throttle_kbps = presets["throttle_kbps"]
        self.reorder_rate = presets.get("reorder_rate", 0.0)
        return True

    def tick(self, dt_ms):
        """推进注入器的虚拟时钟。"""
        self._now_ms += dt_ms

    def _total_delay(self):
        """本消息的总延迟 = 固定延迟 + 抖动。"""
        if self.jitter_ms > 0:
            jitter = self.rng.randint(-self.jitter_ms, self.jitter_ms)
        else:
            jitter = 0
        return max(0, self.latency_ms + jitter)

    def send(self, msg):
        """发送路径：返回 (是否放行, 是否额外复制一份)。

        丢包 → 丢弃；延迟 → 进延迟队列稍后投递。
        """
        # 丢包
        if self.rng.random() < self.loss_rate:
            return False, False
        return True, False

    def send_input(self, msg):
        """输入路径：可能重复 / 丢失。返回投递的消息列表（0、1 或 2 份）。"""
        out = []
        if self.rng.random() < self.drop_input_rate:
            return out              # 输入丢失
        out.append(msg)
        if self.rng.random() < self.duplicate_rate:
            out.append(msg)         # 输入重复
        # 延迟：这里简化——输入延迟通过全局 delay 队列处理，由宿主在 tick 里排空
        return out

    def pending_deliveries(self):
        """取到该投递的消息（按延迟时间到达的）。"""
        due = []
        remaining = []
        for at, msg in self._delay_queue:
            if at <= self._now_ms:
                due.append(msg)
            else:
                remaining.append((at, msg))
        self._delay_queue = remaining
        return due

    def delay(self, msg):
        """把一个消息加入延迟队列（在 send 路径被延迟时调用）。"""
        at = self._now_ms + self._total_delay()
        self._delay_queue.append((at, msg))

    def reorder_pair(self, msgs):
        """对收到的连续消息做乱序（简单：以概率交换最后两条）。"""
        if len(msgs) < 2:
            return msgs
        if self.rng.random() < self.reorder_rate:
            msgs[-1], msgs[-2] = msgs[-2], msgs[-1]
        return msgs

    def describe(self):
        """返回当前注入配置的一行摘要。"""
        return ("latency={}ms loss={:.0%} reorder={:.0%} dup={:.0%} "
                "drop_input={:.0%} jitter={}ms throttle={}kbps").format(
            self.latency_ms, self.loss_rate, self.reorder_rate,
            self.duplicate_rate, self.drop_input_rate, self.jitter_ms,
            self.throttle_kbps)
