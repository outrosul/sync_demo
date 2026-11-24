"""确定性模拟：把输入推进世界一个 tick。

这是帧同步与状态同步共用的核心。规则只依赖：
- 当前世界状态（WorldState + 各 PlayerState + 飞行物）。
- 本 tick 的输入（``InputState``）。
- 显式的随机源（``random.Random``）。

不读系统时间、不读进程全局随机，保证同一输入序列在两侧得到相同结果。
"""

from sync_demo.core import ability as AB
from sync_demo.core import game as G

# 下面的常量都是「定点数」（Fixed），而不是 Python 的 float。
# 为什么：帧同步要求两端对同一输入得到「逐位一致」的结果，而 float 的
# 舍入方式在 CPU / 平台 / 编译器之间可能不同（同样 0.1+0.2 在不同平台
# 可能差最后一位）。只有把逻辑运算全部换成定点整数，才能保证任何机器
# 每一步的中间结果都完全相同。
# 这里是「常量在导入时统一换算一次」，之后全程只碰定点值；
# 换算用 from_float 也是一次性的，用它换取的是之后所有比较/加减的确定性。
# 定点版地图边界（Fixed，与 game.py 的像素常量对应）
_GROUND_Y = G.Fixed.from_float(G.GROUND_Y)
_MAP_WIDTH = G.Fixed.from_float(G.MAP_WIDTH)
_MAP_HEIGHT = G.Fixed.from_float(G.MAP_HEIGHT)
# 近战 / 飞行物判定距离的定点值（近战命中已由 MeleeAttack Ability 结算，这里的
# _ATTACK_RANGE 移除，飞行物命中用 _HIT_DX/_HIT_DY）
_HIT_DX = G.Fixed.from_float(20)
_HIT_DY = G.Fixed.from_float(30)
_ZERO = G.Fixed(0)


def clamp(value, low, high):
    """对 Fixed 做钳制：把 value 限制在 [low, high] 内。

    为什么不直接用内置的 min/max 或写成一行三元表达式：
    这里的比较必须走 Fixed 定义的整数比较语义（<、> 已被 Fixed 重载为
    定点比较），才能保证两端得到同一个边界值，而不是 float 的近似结果。
    全部落到三个朴素分支里，也让「value 究竟取了哪一个」一眼可读、
    不存在任何隐式舍入的空间。
    """
    if value < low:
        return low
    if value > high:
        return high
    return value


class InputState:
    """一个玩家在一个 tick 的输入。字段全是布尔或 0/1，便于帧同步压缩。

    这是「帧同步的最小可传输单元」：每 tick 双方要互发的不是完整世界快照，
    而只是这一 tick 的输入向量。正因为输入可以压成几个 bit，帧同步才有
    带宽优势；而「输入确定性地驱动世界」的前提，是输入本身必须是布尔/
    小整数，不能夹带浮点小数或平台相关的值。
    构造器里用 bool() 把任何传入值归一化成严格布尔，也是同样的动机：
    让状态里永远只存在两种可精确比较的值，杜绝「0.0/0/False 混用」这类
    会造成两端分歧的隐患。

    - ``left`` / ``right``：左右移动
    - ``jump``：跳跃
    - ``attack``：普通攻击
    - ``dash``：冲刺（可躲冰冻）
    - ``freeze``：冰冻（冻住对手 1 秒，但对手冲刺瞬移时冻不住）
    """

    __slots__ = ("left", "right", "jump", "attack", "dash", "freeze")

    def __init__(self, left=False, right=False, jump=False, attack=False, dash=False, freeze=False):
        # 用 bool() 归一化：传进来的是任意值，进到状态里必须只剩 True/False。
        # 下层的逻辑靠这些字段做精确比较与序列化，混入 0/1/None 会让
        # 两侧对「同一个输入」得出不同的判断，破坏确定性。
        self.left = bool(left)
        self.right = bool(right)
        self.jump = bool(jump)
        self.attack = bool(attack)
        self.dash = bool(dash)
        self.freeze = bool(freeze)

    def as_tuple(self):
        # 固定字段顺序导出为元组：这是「帧同步压缩/传输」的约定格式。
        # 顺序一旦定了就不能动——两端靠同样的位置含义对号入座。
        return (self.left, self.right, self.jump, self.attack, self.dash, self.freeze)

    @classmethod
    def empty(cls):
        # 全空输入：某玩家这一 tick 没发来输入时的兜底值（见 tick 里 inputs.get）。
        # 用类方法构造保证每次拿到的都是「全新的、状态确定的」空对象，
        # 而不是某个可能被意外改动的共享实例。
        return cls(False, False, False, False, False, False)


def apply_input(world, player, inp):
    """应用单个玩家的输入到其玩家状态（不涉及碰撞）。

    职责划分（明白「为什么只有这一件事」才能理解 tick 的四步）：
    本函数只把「输入」翻译成「意图对自身状态的修改」——设速度、设计时器、
    设朝向，绝不读别的玩家、也不改别的玩家。玩家之间的互动（打中谁、
    冻住谁）一律推迟到后面的 resolve_melee / step_projectiles 阶段。
    这么做的好处是：每一 tick 里，先让所有人都基于「上一帧结束时的世界」
    独立地表达完意图，再统一做交互结算，结算顺序只由固定的遍历顺序决定，
    不会因为某个玩家「先改了别人」而导致两人先后不同、结果不同。
    """
    if not player.alive:
        return

    # 被冰冻：不能动、不能攻击，但冲刺/冰冻输入也无效（除了解冻计时在 step_physics 推进）
    # 之所以在这里整段 return：冰冻是「输入被拦截」，即在输入层就掐断，
    # 而不是在物理层再「抵消位移」。这样「能不能动」的判断只在一处决定，
    # 两端行为一致，也避免物理推进时出现「动一下又被拉回来」这类二次修正。
    if player.frozen_timer > 0:
        player.vx = _ZERO
        return

    # 冲刺中：冲刺位移由 Dash Ability 单独推进，这里把普通 vx 清零，
    # 避免残留上一帧的移动速度在 step_physics 里又叠加一次位移。
    if player.dash_timer > 0:
        player.vx = _ZERO
        return

    # 冲刺（Dash）：朝朝向方向突进，期间免疫冰冻
    # 只在冷却为 0 时触发：冷却是「逐 tick 递减的整数」，用它而不是系统时间，
    # 才能保证「什么时候能再冲刺」在两台机器上分毫不差。
    if inp.dash and player.dash_cooldown == 0:
        player.dash_cooldown = G.DASH_COOLDOWN
        # UE GAS 风格：冲刺是独立 Ability 对象，构造→activate→挂到 active_abilities
        ab = AB.Dash(player.player_id)
        ab.activated_at = world.tick
        if ab.activate(world, player):
            world.active_abilities.append(ab)
        return

    # 水平输入：move ∈ {-1, 0, 1}，× 定点速度
    # 先把左右编成一个整数方向（左-右相互抵消），再乘定点速度：
    # 这样速度只有 -V/0/+V 三档可精确比较的值，绝不会出现 0.1+0.1 的浮点漂移。
    move = 0
    if inp.left:
        move -= 1
    if inp.right:
        move += 1
    player.vx = G.MOVE_SPEED * move
    if move != 0:
        player.facing = 1 if move > 0 else -1

    # 跳跃：只有站在地上才允许，且立即离地
    # 「离地」这一帧就把 on_ground 置 False，是让下文的 step_physics 走
    # 重力分支的关键；若这里不改，本帧就会多承受一次普通位移。
    if inp.jump and player.on_ground:
        player.vy = G.JUMP_SPEED
        player.on_ground = False

    # 普通攻击：同时设「生效计时」与「冷却计时」，并锁定出手朝向
    # 用两个独立计数器而不是系统时间：attack_timer 决定这一击的有效窗口，
    # attack_cooldown 决定下一次出手的间隔。二者都以 tick 为最小单位递减，
    # 于是「攻击是否命中」和「能否再出手」在两端逐 tick 完全同步。
    if inp.attack and player.attack_cooldown == 0 and player.attack_timer == 0:
        player.attack_cooldown = G.ATTACK_COOLDOWN
        # UE GAS 风格：攻击是独立 Ability，激活即检测命中结算伤害
        ab = AB.MeleeAttack(player.player_id)
        ab.activated_at = world.tick
        if ab.activate(world, player):
            world.active_abilities.append(ab)

    # 冰冻（Freeze）：命中前方对手，对手若非冲刺中则被冻住
    if inp.freeze and player.freeze_cooldown == 0:
        player.freeze_cooldown = G.FREEZE_COOLDOWN
        # UE GAS 风格：冰冻是独立 Ability，激活即检测并冻结前方目标
        ab = AB.Freeze(player.player_id)
        ab.activated_at = world.tick
        if ab.activate(world, player):
            world.active_abilities.append(ab)


def step_physics(world, player):
    """推进一个玩家的物理（位移 / 重力 / 地板 / 边界），全部定点运算。

    按 tick 的固定顺序，本函数运行时「所有玩家的输入都已应用完毕」，因此
    这里推进的是「意图表达完之后」的整体移动，并且一次只改一个玩家自己、
    绝不读/改别的玩家。把位移、重力、边界钳制、各计时器递减都集中在同一
    函数里，是为了让「一个玩家一帧之内的全部自身演化」成为一个原子步骤，
    保证它不因与别人交错而出现顺序分歧。
    """
    if not player.alive:
        return

    # 位移：普通移动（vx/vy）。冲刺位移由 Dash Ability 在 tick 阶段单独推进，
    # 不在这里重复（避免「一次冲刺移两格」）。冲刺期间 vx 已在 apply_input
    # 被拦截为 0，这里只走普通位移。
    player.x = player.x + player.vx
    player.y = player.y + player.vy

    # 重力（只有离地才累积）
    # 先位移、后加重力、再落地检测：这个顺序让「落地那一帧」的 vy 先被
    # 重力加大、再被落地清零，落地瞬间不会残留一个向下的速度；顺序若颠倒，
    # 结果会差一帧，两端就容易在极边界的场合不一致。
    if not player.on_ground:
        player.vy = player.vy + G.GRAVITY

    # 落地（定点比较）
    # 用定点 >= 而不是 == 判断落地：即便被重力一步跨过了地面线也不会漏判，
    # 同时把位置钳回地面线，保证「站上地面」这个状态值是精确的、可复现的。
    if player.y >= _GROUND_Y:
        player.y = _GROUND_Y
        player.vy = _ZERO
        player.on_ground = True

    # 地图左右边界（定点）
    # 边界钳制单独抽成 clamp，语义是「越界即贴边」，而不是把越界值丢弃；
    # 这样两端对「贴边」之后的精确坐标完全一致，后续判定都用同一数值。
    player.x = clamp(player.x, _ZERO, _MAP_WIDTH)

    # 攻击冷却递减（攻击命中/动作窗口已由 MeleeAttack Ability 激活时一次性结算，
    # 这里只保留「能否再次出手」的冷却计时）
    if player.attack_cooldown > 0:
        player.attack_cooldown -= 1

    # 攻击动作表现计时
    if player.attack_anim > 0:
        player.attack_anim -= 1

    # 受击动作表现计时
    if player.hit_anim > 0:
        player.hit_anim -= 1

    # 冲刺冷却
    if player.dash_cooldown > 0:
        player.dash_cooldown -= 1

    # 冰冻计时（被冻期间不能动已经由 apply_input 处理）
    if player.frozen_timer > 0:
        player.frozen_timer -= 1

    # 冰冻冷却
    if player.freeze_cooldown > 0:
        player.freeze_cooldown -= 1


def step_projectiles(world):
    """推进飞行物并结算命中。

    飞行物命中是独立的实体交互：其命中结算基于「这一帧所有玩家移动后的最终
    位置」。近战命中已改由 MeleeAttack Ability 在激活时一次性结算，故此步只
    处理飞行物，任何一端都按同一顺序跑出同一结果。
    """
    for proj in list(world.projectiles.values()):
        # 飞行物按自己的速度推进；速度也是定点，保证弹道逐位一致。
        proj.x = proj.x + proj.vx
        proj.y = proj.y + proj.vy
        # 命中任何存活的其他玩家
        # 遍历里 break 出第一个命中的目标：一旦命中就停止对该飞行物的后续
        # 判定，避免一颗弹在一次结算里打到多个人而产生顺序依赖。
        hit = None
        for victim in list(world.players.values()):
            if victim.player_id == proj.owner_id or not victim.alive:
                continue
            if abs(victim.x - proj.x) <= _HIT_DX and abs(victim.y - proj.y) <= _HIT_DY:
                hit = victim
                break
        if hit is not None:
            # 命中后扣血并移除飞行物；HP 仍是整数，损伤确定。
            hit.hp = max(0, hit.hp - proj.damage)
            if hit.hp == 0:
                hit.alive = False
            world.projectiles.pop(proj.proj_id, None)
            continue
        # 出界移除（定点）
        # 用链式定点区间判断是否仍在世界内；越界即移除，保证飞行物集合
        # 在所有端上同一 tick 收敛到同一集合。
        if not (_ZERO <= proj.x <= _MAP_WIDTH and _ZERO <= proj.y <= _MAP_HEIGHT):
            world.projectiles.pop(proj.proj_id, None)


def tick(world, inputs):
    """推进世界一个 tick。

    ``inputs`` 是 ``{player_id: InputState}`` 的字典，缺失的玩家视为空输入。
    步骤顺序固定（这是确定性的关键）：应用输入 -> 物理 -> 近战 -> 飞行物。

    为什么顺序本身就是「确定性」的核心：
    帧同步的保障不是「每个人都算出合理结果」，而是「每个人都算出
    **完全一样**的中间值」。只要每一步的依赖都被收进一个明确的阶段边界，
    且阶段顺序写死，那么给定同一份输入序列，任何一端都会逐 bit 走同一条
    路径。反过来，哪怕把任意两步交换，都可能让「先动后判」变成「先判后动」，
    在边界情况（恰好落地、恰好进入射程）下产生不同的 HP 或位移。
    """
    # 阶段一：应用输入。先让每个玩家「表达意图」（设速度/计时器/朝向），
    # 且各自独立、互不影响；缺输入的补空输入，保证本轮人人都有确定状态。
    for pid, player in world.players.items():
        inp = inputs.get(pid, InputState.empty())
        apply_input(world, player, inp)

    # 阶段二：物理。所有意图都表达完，再统一推进每个人的位置与自身计时器。
    for player in world.players.values():
        step_physics(world, player)

    # 阶段三：推进技能（Ability）。冲刺位移等持续效果在普通位移之后推进，
    # 保证「位移定稿」后再进入下一步的命中结算。结束的技能从列表移除。
    for ab in list(world.active_abilities):
        ab.tick(world)
    world.active_abilities = [ab for ab in world.active_abilities if ab.is_active]

    # 阶段四：交互结算（飞行物命中）。近战命中已由 MeleeAttack Ability 在激活时
    # 一次性结算（不再走 hit_active 的延迟判定），故这里只保留飞行物推进。
    step_projectiles(world)

    # 帧号递增放在最后：它以纯整数累计，是本 tick 结束时世界的「版本号」，
    # 也是两端对齐进度的依据；放在所有结算之后，代表它指向一个已完成的世界。
    world.tick += 1
