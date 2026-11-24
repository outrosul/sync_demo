"""自动化批量跑测：遍历弱网组合 × 冗余方案，统计 desync 率与延迟。

参照《弱网自适配》的「自动化跑测 + 反推最优方案」思路，但用纯数据（不碰真实网络）：

- 环境维度：遍历 `fault.NETWORK_PRESETS`（perfect/good/general/bad）。
- 方案维度：遍历冗余数（0/1/2，对应 `redundancy.py` DUP）。
- 每组合跑 N 轮：两个「无网络」的确定性世界，一侧注入丢包（用 fault），对比最终 hash。

输出一张矩阵：行 = 网络分级，列 = 冗余数，格 = desync 率（越低越好）。

说明：这是「离线近似」——真实同步的 desync 还受预测回滚、时钟等因素影响，这里只量化
「纯输入丢失下的确定性破坏」这一维，作为「冗余数是否有用」的对照参考。
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD
from sync_demo.net import fault as F
from sync_demo.net import redundancy as RD


def _two_player_world(seed=1):
    rng = random.Random(seed)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 300, G.GROUND_Y)
    return w


def _input_sequence(n=60):
    seq = []
    for i in range(n):
        seq.append({
            1: sim.InputState(right=True if i % 3 else False,
                              jump=(i == 10),
                              attack=(i == 30)),
            2: sim.InputState(left=True if i % 2 else False,
                              attack=(i == 20)),
        })
    return seq


def _run_one(preset_name, redundancy_n, rounds=10):
    """跑一个组合 N 轮，返回 desync 率（0~1）。"""
    desyncs = 0
    inputs = _input_sequence()
    for r in range(rounds):
        # 权威世界：全输入（无丢包）
        auth = _two_player_world(seed=r)
        for frame in inputs:
            sim.tick(auth, frame)

        # 被测世界：输入经「冗余编码 → 每份独立按丢包率丢 → 任一份到达即还原」，
        # 冗余数越高，输入越不容易因丢包而整体丢失。
        inj = F.FaultInjector(random.Random(1000 + r))
        inj.apply_preset(preset_name)
        test = _two_player_world(seed=r)
        for frame in inputs:
            received = {}
            for pid, inp in frame.items():
                sender = RD.RedundantSender(redundancy=redundancy_n)
                packets = sender.encode(_encode_input(inp))
                survived = False
                for pkt in packets:
                    # 每个冗余副本独立经受丢包；任一到达即视为收到
                    ok, _dup = inj.send(pkt)
                    if ok:
                        survived = True
                        break
                if survived:
                    received[pid] = inp
            sim.tick(test, received)

        if SD.world_hash(test) != SD.world_hash(auth):
            desyncs += 1
    return desyncs / rounds


def _encode_input(inp):
    """把 InputState 编码成 bytes（redundancy 需要 bytes）。"""
    import struct
    left, right, jump, attack, dash, freeze = inp.as_tuple()
    return bytes([int(left), int(right), int(jump), int(attack), int(dash), int(freeze)])


def main():
    presets = ["perfect", "good", "general", "bad"]
    redundancies = [0, 1, 2]   # 冗余数：0=无冗余，1=1份冗余，2=2份冗余

    print("desync 率矩阵（越低越同步；行=网络, 列=冗余数）")
    print("{:<10}".format("network"), end="")
    for r in redundancies:
        print("{:>12}".format("redun={}".format(r)), end="")
    print()
    for preset in presets:
        print("{:<10}".format(preset), end="")
        for r in redundancies:
            rate = _run_one(preset, r, rounds=10)
            print("{:>12.1%}".format(rate), end="")
        print()

    print()
    print("说明：弱网丢包下「冗余数」越高，输入越不容易因丢包而丢失，desync 率应越低")


if __name__ == "__main__":
    main()
