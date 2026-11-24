"""时钟同步测试：偏移估算、往返对时、异常过滤。"""

from sync_demo.net import clock_sync as CS


def test_first_sample_sets_offset():
    sync = CS.ClockSync()
    # 服务器比本地快 0.5s：send_local=0, server_time=0.5, recv_local=0.1（RTT 0.1）
    sync.add_sample(send_local=0.0, server_time=0.5, recv_local=0.1)
    # offset = (0.1 - 0.5) - 0.1/2 = -0.4 - 0.05 = -0.45 = 本地比服务器慢 0.45s
    assert abs(sync.offset() - (-0.45)) < 1e-9


def test_zero_rtt_offset_matches_simple_diff():
    sync = CS.ClockSync()
    # 无延迟：rtt=0，offset = 本地 - 服务器
    sync.add_sample(send_local=10.0, server_time=9.0, recv_local=10.0)
    assert abs(sync.offset() - 1.0) < 1e-9


def test_to_server_time_converts():
    sync = CS.ClockSync()
    sync.add_sample(send_local=0.0, server_time=0.0, recv_local=0.0)  # 无偏移
    assert abs(sync.to_server_time(5.0) - 5.0) < 1e-9


def test_outlier_filtered_by_median():
    sync = CS.ClockSync()
    # 多数样本偏移 ~1.0，一条异常偏移 100（模拟 TCP 重传）
    for _ in range(5):
        sync.add_sample(send_local=0.0, server_time=-1.0, recv_local=0.0)  # offset ≈ 1.0
    sync.add_sample(send_local=0.0, server_time=-100.0, recv_local=0.0)   # 异常 offset ≈ 100
    sync.offset(recompute=True)
    # 异常样本被过滤，偏移应接近 1.0 而非被异常拉偏
    assert abs(sync.offset() - 1.0) < 1e-6


def test_sample_count():
    sync = CS.ClockSync()
    for _ in range(3):
        sync.add_sample(0.0, 0.0, 0.0)
    assert sync.sample_count() == 3
