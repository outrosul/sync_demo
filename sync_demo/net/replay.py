"""回放系统（操作序列记录 + 重放，可开关）。

**为什么需要**：帧同步的一个巨大优势是「回放文件小」——因为同步的是操作（输入），不是状态。
只要记录下「每一帧所有玩家按了什么」，回放时把这些输入喂给同一份确定性 ``sim`` 推演，就能
**逐位复现同一局**，而回放文件只含操作、体积极小。这也直接服务「战局回放 / 举报 / 断线重连追帧」。

参照《从零开始设计同步框架》的回放系统：对局中操作 command 汇成 Step，一边送服务器、一边记录；
回放时把记录的 Step 喂回 loop 路径复现。

**本模块职责**（纯数据可测，不碰 socket/渲染）：
- ``Recorder``：逐帧记录输入（可开关，``enabled=False`` 时零开销跳过）。
- ``Replay``：把已记录的输入序列喂给 ``sim.tick`` 重放，得到与原始完全一致的世界状态。
- 序列化：输入序列可 ``dump()`` / ``load()``（用协议 InputState 的 as_tuple）。

可开关设计：``Recorder.enabled`` 默认关（记录有内存开销）。
"""

from sync_demo.core import sim


class Recorder:
    """记录每一帧的输入序列（可开关）。"""

    def __init__(self):
        self.enabled = False              # 录制开关（默认关）
        self.frames = []                  # 每帧的 {player_id: InputState} 列表

    def record(self, inputs):
        """记录一帧的输入（enabled 才记录）。inputs = {player_id: InputState}。"""
        if not self.enabled:
            return
        self.frames.append({pid: inp for pid, inp in inputs.items()})

    def dump(self):
        """序列化成可保存的纯数据（每帧是 {pid_str: tuple}）。"""
        return [
            {str(pid): inp.as_tuple() for pid, inp in frame.items()}
            for frame in self.frames
        ]

    def load(self, data):
        """从 dump() 的纯数据还原输入帧列表。"""
        self.frames = []
        for frame in data:
            parsed = {}
            for pid_str, tup in frame.items():
                parsed[int(pid_str)] = sim.InputState(*tup)
            self.frames.append(parsed)

    def frame_count(self):
        return len(self.frames)


def replay(world, frames):
    """把记录好的输入帧序列喂给世界重放，逐帧 sim.tick。

    返回同一个 world（就地推进），供调用方对比「重放前 vs 重放后」的 hash。
    """
    for inputs in frames:
        sim.tick(world, inputs)
    return world
