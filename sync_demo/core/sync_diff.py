"""同步状态观测：hash 对比 + 差异字段定位。

这是「不同步观测」的核心。帧同步的关键难题是**两侧模拟结果必须逐位一致**，
一旦某帧分叉（desync），就需要立刻知道：**在第几帧、哪个玩家、哪个字段、差多少**。

本模块提供两件事：

1. ``world_hash(world)``：对世界状态计算确定性 hash（FNV-1a 64 位），
   让服务器能对比「各客户端上报的状态」是否与「权威状态」一致。
2. ``diff_snapshots(a, b)``：对比两套玩家状态，逐字段定位差异，返回差异清单。

两者都只依赖 ``core.game`` / ``core.sim``，不碰网络与渲染。
"""

from sync_demo.core import game as G
from sync_demo.core import sim

# FNV-1a 64 位参数。
# 为什么不直接用 Python 内置 hash()：内置 hash 对 str 加了随机盐（防止 DoS），
# 同一条数据两次运行得到的结果不同，且只有「同一进程内」才稳定——这对跨机对比毫无意义。
# FNV-1a 是纯整数乘法+异或，不依赖哈希种子，任何语言/平台实现一致，正是同步校验需要的「确定性」。
_FNV_OFFSET = 0xcbf29ce484222325
# 2**40 + 2**8 + 0xb3，一个保证良好雪崩效应的素数。
_FNV_PRIME = 0x100000001b3


def fnv1a64(data_bytes):
    """FNV-1a 64 位 hash（确定性、跨平台一致）。

    注意这里是 FNV-1a（不是 FNV-1）：先异或、后乘。
    这一顺序差异会显著影响雪崩效果——FNV-1a 里最后一个字节的差别也能充分扩散到高位，
    任一字节变化都会让最终 hash 面目全非，因此能灵敏地暴露「只差一个字段」的不同步。
    """
    h = _FNV_OFFSET
    for byte in data_bytes:
        h ^= byte
        h = (h * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def _player_fields(player):
    """把某玩家状态摊平成确定顺序的字段列表（用于 hash 与差异对比）。

    为什么必须「摊平」：玩家的字段很多是对象（如 x 是带 .raw 的定点数/坐标，
    facing、alive 是枚举或布尔）。如果直接 hash 整个 player 对象，会把这些也牵进来——
    而对象的 hash/内存表示在不同进程里可能不一致，等于埋下假的不同步。
    这里显式选取「真正参与同步判定」的字段并转成裸整数，杜绝一切非确定内容混入。
    另一个作用是统一顺序：hash 对输入顺序敏感，同一份状态字段顺序一旦两边不同，
    hash 就必然不同。固定这份 tuple 的顺序，等于给两侧锁死了「编码序列」。
    """
    return (
        player.player_id,
        player.x.raw, player.y.raw,
        player.vx.raw, player.vy.raw,
        player.facing, 1 if player.on_ground else 0,
        player.hp, 1 if player.alive else 0,
        player.attack_timer, player.attack_cooldown,
        player.hit_facing, 1 if player.hit_active else 0,
        player.attack_anim, player.hit_anim,
        player.dash_timer, player.dash_dir, player.dash_cooldown,
        player.frozen_timer, player.freeze_cooldown,
    )


def world_hash(world):
    """对世界状态算 hash。

    只纳入逻辑状态（玩家字段 + 飞行物），不纳入进程内 python 对象 id 等非确定内容。
    player_id 排序后逐字段编码，保证两侧遍历顺序一致（字典顺序可能不同）。
    """
    parts = []
    # 为什么必须按 player_id 排序：world.players 是 dict，它的遍历顺序是「插入顺序」，
    # 与玩家加入的先后有关，跟逻辑状态无关。服务器与客户端、或不同客户端的插入顺序
    # 可能不同，若直接 for 遍历，即使两边状态完全一致，拼接出来的字节流顺序也会不同，
    # 导致 hash 必然不同——把「顺序不同」误判成「状态不同」。排序后只看 id 的大小关系，
    # 与插入历史彻底脱钩，hash 才真正只反映状态。
    for pid in sorted(world.players):
        player = world.players[pid]
        for field in _player_fields(player):
            # 每个字段编码为 8 字节大端，保证跨字节序一致
            parts.append(_pack_i64(field))
    # 飞行物同样按 proj_id 排序，理由与玩家一致。
    for proj_id in sorted(world.projectiles):
        proj = world.projectiles[proj_id]
        parts.append(_pack_i64(proj.proj_id))
        parts.append(_pack_i64(proj.x.raw))
        parts.append(_pack_i64(proj.y.raw))
    # 全部字段拼成一段字节串后一次性 hash，避免对每个字段单独 hash 再组合
    # （那样组合方式本身也容易引入歧义，例如 [hash(a),hash(b)] 与 [hash(b),hash(a)] 无法区分边界）。
    return fnv1a64(b"".join(parts))


def _pack_i64(value):
    """把整数打包成 8 字节大端（有符号，用补码）。

    为什么必须统一成「8 字节大端」：同一数值在不同机器上的字节序（大端/小端）不同，
    若直接取内存表示，同一份状态在 x86（小端）与 ARM（可能大端）上编码出的字节不一样，
    hash 就会「明明状态相同却不一致」。
    这里显式用 .to_bytes(8, "big") 固定为大端、固定 8 字节，让字节流与 CPU 字节序无关。
    先做 & 0xFFFFFFFFFFFFFFFF 是为了把负数转成 64 位补码表示（去掉 Python 无限精度整数的符号位干扰），
    保证同一个有符号值无论正负都映射到同一串 8 字节，另一端解码时也走同一补码约定。
    """
    value = int(value) & 0xFFFFFFFFFFFFFFFF
    return value.to_bytes(8, "big")


# 参与差异对比的字段名（与 _player_fields 顺序一致，human-readable）。
# 为什么单独维护这份名字表：_player_fields 返回的是「裸数值」，只靠数值没法告诉人
# 「第 3 个字段是 vx 还是 vy」。这份 tuple 与它按下标一一对应，diff 时把数值映射回
# 人类可读的字段名，学同步的人才能一目了然地看出「哪个字段岔开了」。
FIELD_NAMES = (
    "player_id", "x", "y", "vx", "vy", "facing", "on_ground", "hp", "alive",
    "attack_timer", "attack_cooldown", "hit_facing", "hit_active",
    "attack_anim", "hit_anim", "dash_timer", "dash_dir", "dash_cooldown",
    "frozen_timer", "freeze_cooldown",
)


def diff_players(a, b):
    """对比两个玩家的「字段值 tuple」，返回差异字段名列表（按顺序）。

    为什么用 zip 而不是逐个字段手写 if：FIELD_NAMES 与 _player_fields 是「同一份顺序语义」
    的两个表达（一个可读名、一个数值），zip 让它们天然对齐；只要俩 tuple 一方改了顺序
    而另一方没跟着改，zip 就会把名字张冠李戴到错的数值上——这就是为什么注释强调
    「顺序一致」必须成立，它是这份 diff 可靠性的前提。
    """
    diffs = []
    # 逐字段比较：只有不相等才记录，返回 (字段名, a 的值, b 的值) 三元组，
    # 这样调用方能同时拿到「哪个玩家（由外层给定）」与「哪个字段、差多少」。
    for name, va, vb in zip(FIELD_NAMES, _player_fields(a), _player_fields(b)):
        if va != vb:
            diffs.append((name, va, vb))
    return diffs


def diff_snapshots(snap_a, snap_b):
    """对比两份快照 dict（``{player_id: {...}}``），返回差异清单。

    返回 ``[(player_id, field, value_a, value_b), ...]``；含「仅一侧存在」的玩家。

    它是 world_hash 的「定位版」：hash 只能告诉你「这一帧两侧不一致」（是/否），
    但没法告诉你在哪分叉。diff_snapshots 补上这后半步——真发生不同步时，靠这条
    清单直接回答「哪位玩家(player_id)、哪个字段(field)、一边是多少(value_a)、
    另一边是多少(value_b)」，把排查从「对着整帧盲找」缩到「看一个字段」。
    这也是教学上 hash 与 diff 分工的意义：hash 负责快速发现、diff 负责精确定位。
    """
    diffs = []
    # 并集 + 排序：既要找出「两边都有但值不同」的玩家，也要找出「只有一边存在」的玩家
    # （掉线、新加入、或一方没能同步到某个玩家）。排序同样是为了结果顺序稳定，
    # 让两次 diff 的输出可复现、便于对账。
    ids = sorted(set(snap_a) | set(snap_b))
    for pid in ids:
        # 快照 b 里根本没有这个玩家：标记为 <missing-b>，value_b 记为 None，
        # 调用方能据此区分「值真的就是 None」与「这个字段/玩家在那一侧压根不存在」。
        if pid not in snap_a:
            diffs.append((pid, "<missing-a>", None, snap_b[pid]))
            continue
        if pid not in snap_b:
            diffs.append((pid, "<missing-b>", snap_a[pid], None))
            continue
        a = snap_a[pid]
        b = snap_b[pid]
        # 字段名也是并集 + 排序：两侧字段集合可能不同（例如某侧新增了字段），
        # 只取交集会漏掉「一边有、一边没有」的字段差异，取并集才能完整报告。
        for key in sorted(set(a) | set(b)):
            # 用 .get 而非下标：缺失字段返回 None，避免 KeyError；
            # 这样「字段缺失」会表现为 va=None 而 vb=某个值，自然落入 va != vb 分支被报告出来。
            va = a.get(key)
            vb = b.get(key)
            if va != vb:
                diffs.append((pid, key, va, vb))
    return diffs
