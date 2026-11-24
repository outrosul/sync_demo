"""延迟补偿副作用演示（高延迟玩家获利）。

倒带法（Lag Compensation）的核心坑：一击必杀对射时——
- 低延迟玩家 A（10ms）先开枪，服务器很快处理 → B 判死亡。
- 高延迟玩家 B（200ms）后开枪，但 B 的请求 200ms 后才到，服务器「倒带 0.2s」到
  A、B 都未死亡的历史时刻 → B 的攻击也判有效 → A 也死。

即：**延迟补偿让高延迟玩家反而获得有利判定，拖累低延迟玩家体验**。

本脚本用纯数据（不碰真实网络）演示这个反直觉结论。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_demo.core import game as G
from sync_demo.net import timewarp as TW


def _make_world():
    rng = __import__("random").Random(1)
    w = G.World(rng)
    w.add_player(1, "A", 100, G.GROUND_Y)   # 低延迟玩家 A
    w.add_player(2, "B", 120, G.GROUND_Y)   # 高延迟玩家 B
    w.players[1].facing = 1                 # A 朝右瞄准 B
    w.players[2].facing = -1                # B 朝左瞄准 A
    return w


def main():
    print("== 场景：A(10ms) 和 B(200ms) 一击必杀对射 ==")
    w = _make_world()
    history = TW.WorldHistoryBuffer(capacity=64)
    # 先归档「两人都活着」的历史世界（倒带目标）
    history.record(0, w)

    # 步骤1：A 先开枪（低延迟，很快处理），按当前世界判定 → B 死
    print()
    print("1) A 先开枪（10ms 后即被服务器处理）")
    if TW.rewind_hit_test(w, 1, 2, max_range=G.ATTACK_RANGE * 20, max_dy=60):
        w.players[2].alive = False
        print("   A 命中 B，B 被判死亡")
    print("   当前世界：A 存活={}，B 存活={}".format(w.players[1].alive, w.players[2].alive))

    # 步骤2：B 后开枪（高延迟，200ms 后才到），服务器倒带 0.2s 到「两人都活着」的历史
    print()
    print("2) B 后开枪（200ms 后请求才到），服务器倒带到 0.2s 前")
    history_world = history.get(0)   # 倒带目标：两人都活着
    if TW.rewind_hit_test(history_world, 2, 1, max_range=G.ATTACK_RANGE * 20, max_dy=60):
        w.players[1].alive = False
        print("   B 倒带后也命中 A，A 也被判死亡！")
    print("   最终世界：A 存活={}，B 存活={}".format(w.players[1].alive, w.players[2].alive))

    print()
    print("== 结论 ==")
    print("  不补偿时：A 先杀 B，B 无法攻击，A 存活。")
    print("  补偿（倒带）后：B 的迟到攻击也生效 → A 也死。")
    print("  即：延迟补偿让高延迟玩家 B 反而获利，拖累低延迟玩家 A 的体验。")


if __name__ == "__main__":
    main()
