"""定点数与纯数据定义。

**为什么要定点**：浮点运算是平台相关的 —— 同一段 ``0.1 + 0.2`` 在不同 CPU（x87 vs SSE）、
不同舍入模式下结果可能不同，导致帧同步里两侧模拟结果**逐位不相等**，最终 desync。
定点数是把「小数」表示成「整数」，**固定小数位数**（本仓库固定 8 位，缩放 2^8=256），
例如 ``0.5`` 存成 ``128``。这样所有小数运算都变成整数运算，**任何平台算出来逐位相等**。

用法：

- ``Fixed.from_float(0.5)`` 或 ``Fixed(128)`` 构造（``Fixed(raw)`` 以原始定点整数构造）。
- 四则运算 ``+ - * /`` 返回 ``Fixed``；比较 ``== != < <= > >=``。
- ``to_float()`` / ``to_int()`` 转回普通数字（仅用于表现层 / 协议边界，不进逻辑运算）。

**纪律**：逻辑层内**禁止**使用 Python ``float`` 参与位置 / 速度 / 重力运算；
所有物理量都是 ``Fixed``。表现层 / 协议编解码处才允许 ``to_float()`` 一次。
"""

# 定点缩放：2^8，固定 8 位小数
FIXED_SCALE = 256
FIXED_SHIFT = 8


class Fixed:
    """8 位小数的定点数（内部为 ``int``，缩放 256）。"""

    __slots__ = ("raw",)

    def __init__(self, raw=0):
        # 只存一个整数 raw；任何入参都 int() 收敛，避免外部误传 float 把小数带进逻辑层。
        # 定点数本质就这一个整数，所以用 __slots__ 关闭 dict，省内存且字段访问更快。
        self.raw = int(raw)

    @classmethod
    def from_float(cls, value):
        """从浮点构造（仅限配置 / 常量定义处使用，逻辑循环内不应反复调用）。"""
        # value * 256 得到「放大 256 倍的整数」，round 消除浮点边界误差（如 0.1*256=25.600000...），
        # 再 int 截断成逐位确定的整数。这一「浮点→定点」换算只在定义常量时做一次，
        # 此后所有运算走整数路径，因此不会把浮点的不确定性泄漏进模拟。
        return cls(int(round(value * FIXED_SCALE)))

    def to_float(self):
        """转回浮点（仅表现层 / 协议边界用）。"""
        # 除以 256 还原真实值，但结果是有限精度的 float：只能用于显示/调试，
        # 不能再回逻辑运算里 —— 再拿它做加减，等于把浮点不确定性重新引入，前功尽弃。
        return self.raw / FIXED_SCALE

    def to_int(self):
        """向下取整到整数（像素等）。"""
        # raw >> 8 等价于 raw // 256：只留整数部分、丢弃低 8 位小数。
        # 用算术右移是纯整数运算，负数符号位得以保留（仍向下取整），且比除法更直白地表达「截掉小数位」。
        return self.raw >> FIXED_SHIFT

    # ---- 算数 ----

    def __add__(self, other):
        # 两边都是定点的整数表示，相加后仍是定点：小数位对齐天然成立，无需任何浮点。
        # _raw 负责把 int/float 也收敛到「同一尺度」，保证 self.raw 与 other 同单位。
        return Fixed(self.raw + _raw(other))

    def __sub__(self, other):
        # 同加法：定点整数直接相减，尺度一致，结果仍是定点整数。
        return Fixed(self.raw - _raw(other))

    def __mul__(self, other):
        # 定点 × 定点：raw 相乘再 >>8；定点 × 整数：直接乘
        # 两个定点相乘时，raw1*raw2 相当于「放大 256 倍 × 放大 256 倍 = 放大 65536 倍」，
        # 因此必须 >>8 折回 256 倍，否则数值被放大 256 倍、物理量彻底错乱。
        if isinstance(other, int):
            # 与纯整数相乘则尺度不变，直接乘即可（相当于乘一个没有小数位的量）。
            return Fixed(self.raw * other)
        return Fixed((self.raw * _raw(other)) >> FIXED_SHIFT)

    def __radd__(self, other):
        return Fixed(_raw(other) + self.raw)

    def __rsub__(self, other):
        return Fixed(_raw(other) - self.raw)

    def __neg__(self):
        return Fixed(-self.raw)

    def __abs__(self):
        return Fixed(abs(self.raw))

    def __truediv__(self, other):
        # 定点 ÷ 整数：直接除；÷ 定点：(raw<<8)/raw
        if isinstance(other, int):
            # 除以整数尺度不变：raw 除以整数的结果仍是「放大 256 倍」的定点，正确。
            return Fixed(self.raw // other)
        # 除以定点：raw1/raw2 的真实比值 ≈ (raw1/256)/(raw2/256)=raw1/raw2，
        # 但结果还要再放大 256 倍才能当作定点存，故分子先 <<8 再整除分母，
        # 相当于 (raw1*256)/raw2，既保留精度又维持统一尺度。
        return Fixed((self.raw << FIXED_SHIFT) // _raw(other))

    # ---- 比较 ----

    def __eq__(self, other):
        return self.raw == _raw(other)

    def __lt__(self, other):
        return self.raw < _raw(other)

    def __le__(self, other):
        return self.raw <= _raw(other)

    def __gt__(self, other):
        return self.raw > _raw(other)

    def __ge__(self, other):
        return self.raw >= _raw(other)

    def __hash__(self):
        return hash(self.raw)

    def __repr__(self):
        return "Fixed({} ≈ {})".format(self.raw, self.to_float())


def _raw(value):
    """取出定点原始整数：Fixed 取 raw，int 直接当定点、float 保守转定点。"""
    # 三种输入统一收敛到「同一尺度的整数」，是四则比较运算的公共前处理：
    # 不在这收敛，每个运算符都要各写一遍类型判断，且字段值还容易被误传成 float 造成尺度错乱。
    if isinstance(value, Fixed):
        # Fixed 直接取其内部整数，零成本、零误差。
        return value.raw
    if isinstance(value, int):
        # int 本身就是「放大 256 倍」的定点原始值，原样返回即可。
        return value
    if isinstance(value, float):
        # 浮点仅在边界处才允许出现：这里保守地用 round 折回定点，避免静默下溢。
        # 一旦走这里说明逻辑层漏传了 float，宁可让它进入也保证数值仍被量化到 8 位定点尺度。
        return int(round(value * FIXED_SCALE))
    # 不认识的类型直接抛错：比悄悄按 0/错误值继续演算更能尽早暴露逻辑层漏网的类型。
    raise TypeError("unsupported operand type: {}".format(type(value)))


# 地图尺寸（像素整数）
MAP_WIDTH = 800
MAP_HEIGHT = 480
# 地板高度：角色脚底所在的 y（世界坐标，y 向下增长）
GROUND_Y = 440

# 玩家基础参数（定点，固定 8 位小数）—— 用 from_float 定义一次，逻辑内全用 Fixed 运算
# 这些常量只在模块加载时经 from_float 换算一次：模块内只有这一处出现 float 字面量，
# 之后所有物理推演都拿它们的整数 raw 做纯整数运算，从而保证帧同步两侧算出的速度/位移逐位一致。
MOVE_SPEED = Fixed.from_float(4.0)        # 每 tick 水平位移（定点）
JUMP_SPEED = Fixed.from_float(-14.0)      # 起跳竖直速度（负 = 向上，定点）
GRAVITY = Fixed.from_float(3.0)           # 每 tick 竖直加速度（定点）

MAX_HP = 100

# 攻击相关（距离用像素整数，便于判定；伤害/冷却用整数）
ATTACK_RANGE = 60
ATTACK_DAMAGE = 10
ATTACK_COOLDOWN = 20
ATTACK_ACTIVE = 6          # 一次攻击「有效命中」持续 tick 数
ATTACK_ANIM = 12           # 攻击动作总时长 tick（含抬手+命中+收招，用于表现）

# 冲刺（Dash）相关
# 冲刺位移也必须是定点：它和 MOVE_SPEED 一样参与角色每 tick 的位置积分，
# 若这里用 float，速度变换时就会把浮点不确定性带进 x 坐标，破坏逐位一致。
DASH_SPEED = Fixed.from_float(24.0)   # 冲刺每 tick 位移（定点）
DASH_TICKS = 8                        # 冲刺持续 tick 数
DASH_COOLDOWN = 40                    # 冲刺冷却 tick 数

# 冰冻（Freeze）相关
FREEZE_TICKS = 60                     # 冰冻持续时间（按 60Hz = 1 秒）
FREEZE_RANGE = 80                     # 冰冻判定距离（像素）
FREEZE_COOLDOWN = 60                  # 冰冻冷却 tick 数


class Vec2:
    """二维定点向量（全 Fixed）。"""

    __slots__ = ("x", "y")

    def __init__(self, x, y):
        # x/y 接受 Fixed 或普通数字：已是 Fixed 直接收下，否则走 from_float 量化。
        # 关键是不能把 float 原样存进 x/y —— 那样向量一旦参与位置积分就会重新引入浮点误差。
        self.x = x if isinstance(x, Fixed) else Fixed.from_float(x)
        self.y = y if isinstance(y, Fixed) else Fixed.from_float(y)

    def __eq__(self, other):
        return isinstance(other, Vec2) and other.x == self.x and other.y == self.y

    def __repr__(self):
        return "Vec2({}, {})".format(self.x, self.y)


class PlayerState:
    """一个玩家的权威状态（可序列化、可比较）。

    位置 / 速度均为 ``Fixed`` 定点（固定 8 位小数）；``as_dict`` 输出定点原始整数
    供协议无损传输，表现层再用 ``to_int()`` 换算像素。
    """

    __slots__ = (
        "player_id", "name",
        "x", "y", "vx", "vy",        # Fixed 定点
        "facing", "on_ground",
        "hp", "alive",
        "attack_timer", "attack_cooldown",
        "hit_facing", "hit_active",
        # 动作表现状态
        "attack_anim",               # >0 表示攻击动作剩余 tick（表现用）
        "hit_anim",                  # >0 表示受击动作剩余 tick（表现用）
        # 冲刺 Dash
        "dash_timer",                # >0 表示冲刺中（期间免疫冰冻）
        "dash_dir",                  # 冲刺方向（-1/1）
        "dash_cooldown",
        # 冰冻 Freeze
        "frozen_timer",              # >0 表示被冰冻，不能动
        "freeze_cooldown",
        # 颜色（需要同步的属性：服务器分配一次，随状态同步给所有客户端）
        "color_index",               # 颜色索引（0~7），客户端映射到调色板 RGB
    )

    def __init__(self, player_id, name, x, y):
        """:param x: Fixed 或 int(定点原始值)；:param y: 同上。"""
        self.player_id = player_id
        self.name = name
        # 这里与 Vec2 不同：x/y 已可能是定点原始整数（来自协议还原），
        # 所以用 Fixed(x) 按「原始定点整数」解释，而不是 from_float —— 二者语义正好相反：
        # 传进来 128 应理解为 0.5，若误用 from_float 会把它当成 128.0 再放大 256 倍。语义不能混。
        self.x = x if isinstance(x, Fixed) else Fixed(x)
        self.y = y if isinstance(y, Fixed) else Fixed(y)
        self.vx = Fixed(0)
        self.vy = Fixed(0)
        self.facing = 1               # 1 朝右，-1 朝左
        self.on_ground = True
        self.hp = MAX_HP
        self.alive = True
        self.attack_timer = 0
        self.attack_cooldown = 0
        self.hit_facing = 1
        self.hit_active = False
        # 动作表现状态（默认 0）
        self.attack_anim = 0
        self.hit_anim = 0
        # 冲刺
        self.dash_timer = 0
        self.dash_dir = 1
        self.dash_cooldown = 0
        # 冰冻
        self.frozen_timer = 0
        self.freeze_cooldown = 0
        # 颜色：默认 0（客户端首次收到快照前占位），服务器分配后随快照同步
        self.color_index = 0

    def as_dict(self):
        """序列化为纯 dict（协议/快照用）。

        坐标与速度都传**定点原始整数**（``self.x.raw``），快照不失精度；
        表现层再用 ``Fixed(raw).to_int()`` 换算像素。动作/技能状态一并序列化。
        """
        return {
            "player_id": self.player_id,
            "name": self.name,
            # 位置/速度输出 self.x.raw 而非 float：raw 是确定的整数，JSON/网络传输逐位无损；
            # 若输出 to_float()，float 转字符串、再跨端解析会引入舍入与平台差异，快照就对不齐了。
            "x": self.x.raw, "y": self.y.raw,
            "vx": self.vx.raw, "vy": self.vy.raw,
            "facing": self.facing, "on_ground": self.on_ground,
            "hp": self.hp, "alive": self.alive,
            # 攻击状态：timer 决定有效窗口、cooldown 决定出手间隔、hit_* 决定命中判定
            "attack_timer": self.attack_timer,
            "attack_cooldown": self.attack_cooldown,
            "hit_facing": self.hit_facing,
            "hit_active": self.hit_active,
            "attack_anim": self.attack_anim,
            "hit_anim": self.hit_anim,
            "dash_timer": self.dash_timer, "dash_dir": self.dash_dir,
            "dash_cooldown": self.dash_cooldown,
            "frozen_timer": self.frozen_timer,
            "freeze_cooldown": self.freeze_cooldown,
            "color_index": self.color_index,
        }


class Projectile:
    """远程攻击的飞行物。位置/速度用 Fixed 定点。"""

    __slots__ = ("proj_id", "owner_id", "x", "y", "vx", "vy", "damage")

    def __init__(self, proj_id, owner_id, x, y, vx, vy, damage):
        self.proj_id = proj_id
        self.owner_id = owner_id
        self.x = x if isinstance(x, Fixed) else Fixed(x)
        self.y = y if isinstance(y, Fixed) else Fixed(y)
        self.vx = vx if isinstance(vx, Fixed) else Fixed(vx)
        self.vy = vy if isinstance(vy, Fixed) else Fixed(vy)
        self.damage = int(damage)


class World:
    """世界状态：玩家集合 + 飞行物集合 + 随机源。

    纯数据容器，推演逻辑在 ``sim.tick``（它只持有状态与随机源）。
    """

    def __init__(self, rng):
        self.rng = rng                 # random.Random 实例（显式传入）
        self.players = {}              # player_id -> PlayerState
        self.projectiles = {}          # proj_id -> Projectile
        self.active_abilities = []     # 正在生效的技能（Ability 对象，UE GAS 风格）
        self.next_proj_id = 1
        self.tick = 0

    def add_player(self, player_id, name, x_px, y_px):
        """:param x_px: 像素 x；:param y_px: 像素 y。内部转定点存储。"""
        # 入参是像素（真实浮点坐标），因此用 from_float 做浮点→定点量化后落进 PlayerState，
        # 而非 Fixed(x_px)：这里 x_px 是「真实坐标」，不是「定点原始整数」，二者尺度不同。
        ps = PlayerState(player_id, name, Fixed.from_float(x_px), Fixed.from_float(y_px))
        self.players[player_id] = ps
        return ps

    def remove_player(self, player_id):
        self.players.pop(player_id, None)

    def spawn_projectile(self, owner, vx, vy, damage):
        pid = self.next_proj_id
        self.next_proj_id += 1
        p = Projectile(pid, owner.player_id, owner.x, owner.y, vx, vy, damage)
        self.projectiles[pid] = p
        return p
