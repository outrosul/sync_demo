"""预测 + 回滚 + 平滑 的可运行演示（本地，无真实网络）。

展示状态同步下三个核心研究点：

1. **预测**：客户端本地立即响应自己的输入（预测位置先行动）。
2. **回滚（回拉）**：收到服务器权威快照后，回到权威状态、重放未确认输入。
3. **平滑 / 虚影**：权威位置落后 + 插值平滑，与本地预测位置形成「虚实差距」。

用纯数据跑一遍，打印每一步的位置，让研究者直观理解三者关系。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net import prediction as PR
from sync_demo.net import smoothing as SM


def main():
    pc = PR.PredictedClient(__import__("random").Random(42))
    pc.world = G.World(__import__("random").Random(42))
    pc.world.add_player(1, "me", 100, G.GROUND_Y)

    print("== 阶段 1：预测（客户端本地立即响应）==")
    for i in range(6):
        pc.predict(1, sim.InputState(right=True))
        print("  tick {}  本地预测 x = {:.1f}".format(
            pc.world.tick, pc.world.players[1].x.to_float()))

    predicted_x = pc.world.players[1].x.to_float()
    print("  预测终点 x = {:.1f}".format(predicted_x))

    print()
    print("== 阶段 2：回滚（服务器权威快照 tick=2 到达，回滚 + 重放）==")
    def rebuild(world):
        # 服务器权威：tick=2 时玩家只右移了 2 步
        world.players = {}
        world.add_player(1, "me", 100 + 2 * G.MOVE_SPEED.to_float(), G.GROUND_Y)
    rollback, unacked = pc.on_snapshot(2, rebuild)
    print("  回滚发生 = {}，重放未确认输入 {} 条".format(rollback, unacked))
    print("  回滚+重放后 x = {:.1f}（应等于预测终点 {:.1f}，因为输入全被重放）".format(
        pc.world.players[1].x.to_float(), predicted_x))

    print()
    print("== 阶段 3：平滑 / 虚影（权威位置落后 + 插值）==")
    ghost = SM.GhostTracker()
    # 模拟服务器权威快照序列（滞后、离散）
    snaps = [100, 104, 108, 112, 116, 120]
    prev = None
    for px in snaps:
        ghost.push_snapshot([{"player_id": 1, "x": G.Fixed.from_float(px).raw,
                              "y": G.Fixed.from_float(0).raw}])
        # 每次推入后，采样上一个快照到当前快照之间的 0.5 插值（落后一帧）
        for alpha in (0.25, 0.5, 0.75):
            gx, _ = ghost.sample(1, alpha)
            # 打印一次代表值即可，这里只打印 mid
    gx, _ = ghost.sample(1, 0.5)
    print("  虚影插值中点（最后两个快照之间）x = {:.1f}".format(gx.to_float()))
    print("  说明：虚影是『服务器认为我在哪』的平滑轨迹，与本地预测轨迹的差距")
    print("        就是研究『预测 vs 回拉』要观察的核心。")


if __name__ == "__main__":
    main()
