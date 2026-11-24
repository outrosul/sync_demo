"""desync 演示：展示「帧同步在故障注入下如何分叉，观测机制如何定位」。

不用真实网络，用两个独立的 ``World``（模拟两个客户端）+ 一个权威世界（模拟服务器），
在「帧同步」语义下各自用同一份 ``sim`` 推演；为其中一方注入输入丢失，观察：

1. 两侧 hash 从哪一帧开始不一致（desync 帧号）。
2. ``diff_players`` 定位到具体哪个玩家、哪个字段、差值多少。

这是「不同步观测」的最小可运行例子，也是后续接进真实 server/client 的模板。
"""

import os
import random
import sys

# 让脚本能从任意工作目录运行：把仓库根加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.core import sync_diff as SD
from sync_demo.net import fault as F


def _make_world():
    rng = random.Random(12345)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 400, G.GROUND_Y)
    return w


def _make_inputs():
    """一段确定性的输入序列（每帧两个玩家的输入）。"""
    seq = []
    for i in range(120):
        seq.append({
            1: sim.InputState(right=True if i < 40 else False,
                              jump=(i in (20, 60)),
                              attack=(i == 45)),
            2: sim.InputState(left=True if i < 30 else False,
                              attack=(i == 50)),
        })
    return seq


def main():
    # 三个世界：权威（server）、客户端 A（正常）、客户端 B（有输入丢失故障）
    server = _make_world()
    client_a = _make_world()
    client_b = _make_world()

    # 对 client_b 的上行输入注入 20% 丢失
    fault_b = F.FaultInjector(random.Random(777))
    fault_b.set_drop_input(0.2)

    inputs = _make_inputs()
    desync_frame = None

    for frame_idx, frame_inputs in enumerate(inputs):
        # 服务器按权威输入推演
        sim.tick(server, frame_inputs)

        # 客户端 A 正常
        sim.tick(client_a, frame_inputs)

        # 客户端 B 上行走丢失注入（只丢 p2 自己的输入，模拟“某人输入没到服务器”）
        b_inputs = {}
        for pid, inp in frame_inputs.items():
            delivered = fault_b.send_input(inp)
            if delivered:
                b_inputs[pid] = delivered[0]     # 取第一份（重复份不影响 tick 结果，这里忽略）
        # 帧同步语义：客户端 B 只按「它收到的」输入推演 → 与权威分叉
        sim.tick(client_b, b_inputs)

        # 每帧对比 hash
        ha = SD.world_hash(client_a)
        hb = SD.world_hash(client_b)
        hs = SD.world_hash(server)
        if desync_frame is None and (ha != hs or hb != hs):
            desync_frame = frame_idx
            print("== desync detected at frame {} ==".format(frame_idx))
            print("  server  hash = {}".format(hs))
            print("  clientA hash = {}  (match={})".format(ha, ha == hs))
            print("  clientB hash = {}  (match={})".format(hb, hb == hs))

            # 定位 client_b 与权威的字段差异
            for pid in sorted(server.players):
                diffs = SD.diff_players(server.players[pid], client_b.players[pid])
                if diffs:
                    print("  player {} diverged:".format(pid))
                    for name, va, vb in diffs:
                        print("    {} : server={}  clientB={}".format(name, va, vb))

    if desync_frame is None:
        print("no desync within {} frames".format(len(inputs)))
    else:
        print("first desync at frame {}".format(desync_frame))


if __name__ == "__main__":
    main()
