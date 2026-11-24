"""技能（Ability）系统 —— 参考 UE GameplayAbilitySystem（GAS）风格。

**设计目标**：技能不再是散落在 PlayerState 上的一堆 timer 字段，而是**独立的 Ability
对象**，有明确的「激活 → 检测命中 → 结算伤害 → 结束」生命周期。伤害与表现都由这套
状态机可靠驱动，且 Ability 状态作为世界的一部分，随快照/回滚完整同步。

**与 UE GAS 的对应**：

- ``Ability`` ≈ UE 的 ``UGameplayAbility``：独立技能对象，有 owner、激活、tick、结束。
- ``activate`` ≈ ``ActivateAbility``：施放技能那一刻，做「检测命中、施加效果」。
- ``tick`` ≈ Ability Task 的每帧推进：冲刺位移、冰冻计时等持续的自身演化。
- ``is_active`` / ``ended`` ≈ Ability 的激活/结束状态。
- 伤害结算 ≈ ``GameplayEffect`` 的效果应用（扣血、冰冻、位移）。

**关键设计**：``World.active_abilities`` 挂着一批「正在生效的技能」对象。这样：
1. 技能的激活状态、剩余时长、命中了谁，都是**世界状态的一部分**，纳入快照/回滚/hash，
   帧同步下两侧一致、状态同步下服务器权威下发给客户端。
2. 渲染层读这些 Ability 对象，画出攻击动作 / 受击变白 / 冰冻冰层 / 冲刺拖尾等表现。
"""

from sync_demo.core import game as G


class Ability:
    """技能基类（对应 UE 的 UGameplayAbility）。"""

    ability_id = "ability"       # 子类覆盖：技能名
    duration = 1                 # 子类覆盖：持续时间 tick 数

    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.activated_at = 0
        self.ticks = 0
        self.ended = False

    def activate(self, world, caster):
        """激活技能（对应 UE 的 ActivateAbility），子类实现。"""
        raise NotImplementedError

    def tick(self, world):
        """每帧推进（对应 Ability Task 的 tick），子类实现。"""
        self.ticks += 1
        if self.ticks >= self.duration:
            self.ended = True

    @property
    def is_active(self):
        return not self.ended

    def as_dict(self):
        return {
            "ability_id": self.ability_id,
            "owner_id": self.owner_id,
            "activated_at": self.activated_at,
            "ticks": self.ticks,
            "ended": self.ended,
        }


def build_ability(ability_id, owner_id):
    """按技能名构造对应 Ability 实例。"""
    if ability_id == "melee":
        return MeleeAttack(owner_id)
    if ability_id == "dash":
        return Dash(owner_id)
    if ability_id == "freeze":
        return Freeze(owner_id)
    return None


def _in_melee_range(caster, victim):
    """近战命中判定：朝向一致 + 水平/竖直在射程内（定点整数比较）。"""
    dx = victim.x - caster.x
    dy = victim.y - caster.y
    same_dir = (dx.raw * caster.facing) > 0
    return same_dir and abs(dx) <= _ATTACK_RANGE and abs(dy) <= _ATTACK_RANGE


class MeleeAttack(Ability):
    """普通攻击：激活瞬间检测前方近战命中，立即造成伤害 + 受击表现。"""

    ability_id = "melee"
    duration = G.ATTACK_ANIM

    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.hit_targets = []

    def activate(self, world, caster):
        for victim in world.players.values():
            if victim.player_id == self.owner_id:
                continue
            if not victim.alive:
                continue
            if victim.player_id in self.hit_targets:
                continue
            if _in_melee_range(caster, victim):
                victim.hp = max(0, victim.hp - G.ATTACK_DAMAGE)
                victim.hit_anim = self.duration
                self.hit_targets.append(victim.player_id)
                if victim.hp == 0:
                    victim.alive = False
        caster.attack_anim = self.duration
        return True

    def as_dict(self):
        d = super().as_dict()
        d["hit_targets"] = list(self.hit_targets)
        return d


class Dash(Ability):
    """冲刺：持续 N tick 朝朝向突进，期间免疫冰冻。"""

    ability_id = "dash"
    duration = G.DASH_TICKS

    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.direction = 1

    def activate(self, world, caster):
        self.direction = caster.facing
        caster.dash_timer = self.duration
        # 同步写到 PlayerState 的 dash_dir：渲染层（拖尾方向）读的是这个字段，
        # 不写的话渲染永远用默认值 1，导致「往左冲刺也画在左边」。
        caster.dash_dir = self.direction
        return True

    def tick(self, world):
        caster = world.players.get(self.owner_id)
        if caster is not None and caster.alive:
            caster.x = caster.x + G.DASH_SPEED * self.direction
            caster.facing = self.direction
            if caster.x.raw < 0:
                caster.x = G.Fixed(0)
            if caster.x.raw > G.Fixed.from_float(G.MAP_WIDTH).raw:
                caster.x = G.Fixed.from_float(G.MAP_WIDTH)
        super().tick(world)
        if caster is not None:
            caster.dash_timer = max(0, self.duration - self.ticks)

    def as_dict(self):
        d = super().as_dict()
        d["direction"] = self.direction
        return d


class Freeze(Ability):
    """冰冻：激活瞬间检测前方目标，使其冻结 1 秒（冲刺中目标冻不住）。"""

    ability_id = "freeze"
    duration = 1

    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.frozen_target_id = None

    def activate(self, world, caster):
        for target in world.players.values():
            if target.player_id == self.owner_id:
                continue
            if not target.alive:
                continue
            dx = target.x.raw - caster.x.raw
            if dx * caster.facing <= 0:
                continue
            if target.dash_timer > 0:
                continue
            if abs(target.x - caster.x) <= G.Fixed.from_float(G.FREEZE_RANGE):
                target.frozen_timer = G.FREEZE_TICKS
                self.frozen_target_id = target.player_id
                break
        caster.freeze_cooldown = G.FREEZE_COOLDOWN
        return True

    def as_dict(self):
        d = super().as_dict()
        d["frozen_target_id"] = self.frozen_target_id
        return d


# ---- 常量（模块级一次定义，与 core.game 对齐）----
_ATTACK_RANGE = G.Fixed.from_float(G.ATTACK_RANGE)
