"""固定步长逻辑帧调度器（等帧 / 追帧 / 补帧）。

**为什么需要**：帧同步下，客户端逻辑帧以服务端下发的「帧」为节奏推进。但网络波动会导致：

- **没收到帧（等帧）**：本拍没有可消费的帧 → 逻辑帧停下来等待，不能用「上一帧」瞎推演
  （否则客户端环境不一致）。表现为卡顿，但换来一致。
- **收到多帧积压（追帧）**：网络恢复后多帧一起到 → 逻辑帧加速消费，追赶服务端进度。
- **补帧原则**：逻辑帧步长固定（如 33ms），但真实 wall clock 会波动。要求任意时间段内
  「累计逻辑时间 == 累计自然时间」：`Sum(delta_time) == Sum(wall_time)`。卡顿期间少跑的
  逻辑帧，要在追帧阶段补回来，让逻辑时间最终追平自然时间。

**本模块职责**（纯数据，可脱离网络/渲染单测）：
- 维护一个「待消费帧队列」，``push(frame)`` 入队。
- 维护「逻辑时间」与「自然时间」两个累计值，``step(now)`` 按固定步长从队列取帧消费，
  并返回「本拍应执行几帧」（0 = 等帧，>1 = 追帧，1 = 正常）。
- ``catch_up_ratio()`` 报告逻辑时间落后自然时间的比例，供观测「追帧压力」。

时钟可注入（与 KCP/DR 同思路），保证可测。
"""


class FixedStepScheduler:
    """固定步长逻辑帧调度器：管理帧队列 + 等帧/追帧 + 补帧。

    :param fixed_step: 逻辑帧固定步长（秒），如 0.033（33ms）。
    :param max_catch_up: 单拍最多追几帧，防止追帧把主循环卡死（上限保护）。
    :param clock: 可注入时钟（秒），默认 time.monotonic。
    """

    def __init__(self, fixed_step=0.033, max_catch_up=8, clock=None):
        self.fixed_step = fixed_step
        self.max_catch_up = max_catch_up
        self.clock = clock if clock is not None else _monotonic
        self.frame_queue = []      # 待消费的帧（先进先出）
        self.logic_time = 0.0      # 累计逻辑时间（帧数 × 步长）
        self.wall_ref = None       # 自然时间的累计基准（上次 step 的 now）
        self.wall_elapsed = 0.0    # 累计自然时间

    def push(self, frame):
        """把一个服务端帧加入待消费队列。"""
        self.frame_queue.append(frame)

    def queue_size(self):
        """当前积压的待消费帧数（观测追帧压力）。"""
        return len(self.frame_queue)

    def step(self):
        """推进一步：根据「队列积压 + 逻辑/自然时间差」决定本拍执行几帧。

        返回本拍应消费的**帧列表**（从队列中弹出，供调用方逐个推演）：
        - 空列表：队列空 → 等帧（逻辑停下，不动）。
        - 1 帧：正常步进。
        - 多帧：积压追帧（但不超过 max_catch_up）。

        补帧原则：若「自然时间已大大领先逻辑时间」（之前卡顿少跑了帧），即使队列
        空了也允许 `catch_up_missing()` 补跑（返回待补帧数），但补跑不消费真实帧，
        由调用方自行按空帧推演。为保持简单，这里只管理「队列驱动」的等帧/追帧；
        「补帧追平自然时间」通过 ``missed_frames()`` 暴露给调用方。
        """
        now = self.clock()
        if self.wall_ref is None:
            self.wall_ref = now
        self.wall_elapsed += now - self.wall_ref
        self.wall_ref = now

        if not self.frame_queue:
            return []
        # 追帧：积压多少就尽量消费多少（但受 max_catch_up 上限保护）
        consume = min(len(self.frame_queue), self.max_catch_up)
        consumed = []
        for _ in range(consume):
            consumed.append(self.frame_queue.pop(0))
        self.logic_time += consume * self.fixed_step
        return consumed

    def missed_frames(self):
        """按补帧原则算出「还需补跑多少逻辑帧」追平自然时间。

        ``逻辑时间 + 补帧×步长 ≈ 自然时间`` → 补帧数 = (自然时间 - 逻辑时间) / 步长。
        结果向下取整；为负（逻辑超前）则返回 0。供调用方在队列空但时间落后时补跑空帧。
        """
        lag = self.wall_elapsed - self.logic_time
        if lag <= 0:
            return 0
        return int(lag / self.fixed_step)

    def catch_up_ratio(self):
        """逻辑时间落后自然时间的比例（0 = 完全追平，越大越落后）。"""
        if self.wall_elapsed <= 0:
            return 0.0
        return 1.0 - self.logic_time / self.wall_elapsed


def _monotonic():
    import time
    return time.monotonic()
