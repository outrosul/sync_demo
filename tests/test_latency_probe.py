"""延迟埋点测试：各段耗时统计。"""

from sync_demo.net import latency_probe as LP


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_disabled_returns_no_probe():
    clock = FakeClock()
    probe = LP.LatencyProbe(clock=clock)
    assert probe.enabled is False
    assert probe.new_probe() is None


def test_enabled_tracks_stages():
    clock = FakeClock()
    probe = LP.LatencyProbe(clock=clock)
    probe.enabled = True
    pid = probe.new_probe()          # t=0 input_created
    clock.t = 0.01
    probe.mark(pid, "client_sent")   # 10ms
    clock.t = 0.06
    probe.mark(pid, "server_recv")   # 60ms
    clock.t = 0.07
    probe.mark(pid, "server_sent")   # 70ms
    clock.t = 0.12
    probe.mark(pid, "client_recv")   # 120ms
    report = probe.report()
    # 各段耗时
    assert abs(report["input_created->client_sent"] - 0.01) < 1e-6
    assert abs(report["client_sent->server_recv"] - 0.05) < 1e-6
    assert abs(report["server_recv->server_sent"] - 0.01) < 1e-6
    assert abs(report["server_sent->client_recv"] - 0.05) < 1e-6


def test_report_empty_when_no_samples():
    probe = LP.LatencyProbe(clock=FakeClock())
    assert probe.report() == {}


def test_total_probes_counts():
    clock = FakeClock()
    probe = LP.LatencyProbe(clock=clock)
    probe.enabled = True
    p1 = probe.new_probe()
    p2 = probe.new_probe()
    assert probe.total_probes() == 2
