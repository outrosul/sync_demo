"""固定步长逻辑帧调度器测试：等帧 / 追帧 / 补帧 / 上限保护。"""

from sync_demo.net import logic_clock as LC


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_empty_queue_waits():
    """队列空 → 等帧（返回空列表，逻辑不动）。"""
    clock = FakeClock()
    sched = LC.FixedStepScheduler(fixed_step=0.05, clock=clock)
    clock.t = 1.0
    assert sched.step() == []


def test_normal_step_consumes_one():
    """队列有一帧 → 正常消费 1 帧。"""
    clock = FakeClock()
    sched = LC.FixedStepScheduler(fixed_step=0.05, clock=clock)
    sched.push("f1")
    clock.t = 0.05
    consumed = sched.step()
    assert len(consumed) == 1
    assert consumed == ["f1"]
    assert sched.queue_size() == 0


def test_backlog_catches_up():
    """积压多帧 → 追帧（一次消费多帧，但不超过上限）。"""
    clock = FakeClock()
    sched = LC.FixedStepScheduler(fixed_step=0.05, max_catch_up=8, clock=clock)
    for i in range(10):
        sched.push("f{}".format(i))
    clock.t = 0.05
    # 积压 10 帧，受 max_catch_up=8 上限，一次最多追 8 帧
    consumed = sched.step()
    assert len(consumed) == 8
    assert sched.queue_size() == 2


def test_missed_frames_follows_wall_clock():
    """补帧原则：逻辑时间落后自然时间多少，就需补跑多少帧。"""
    clock = FakeClock()
    sched = LC.FixedStepScheduler(fixed_step=0.05, clock=clock)
    # 先把 wall_ref 建好（第一次 step 初始化基准）
    sched.step()
    # 自然时间走 1 秒（模拟 20 个渲染拍，每拍 0.05s），但队列始终空 → 逻辑一帧没跑
    for _ in range(20):
        clock.t += 0.05
        sched.step()
    # 落后约 1 秒 ÷ 0.05 = 20 帧
    assert sched.missed_frames() >= 19
    assert sched.missed_frames() <= 20


def test_catch_up_ratio_zero_when_kept_up():
    """从容消费时追平，落后比例接近 0。"""
    clock = FakeClock()
    sched = LC.FixedStepScheduler(fixed_step=0.05, clock=clock)
    for tick in range(1, 21):
        clock.t += 0.05
        sched.push("f{}".format(tick))
        sched.step()
    # 每拍推 0.05s 且消费 1 帧（0.05s），逻辑时间基本追平自然时间（允许首拍边界误差）
    assert abs(sched.catch_up_ratio()) < 0.1
