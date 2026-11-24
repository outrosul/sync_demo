"""技能状态快照往返测试：攻击/冲刺/冰冻的 timer 状态经 as_dict 序列化后不丢失。

固化一个真实 bug：PlayerState.as_dict() 曾经漏掉 attack_timer / attack_cooldown /
hit_facing / hit_active 四个字段，导致状态同步快照里攻击状态丢失（攻击卡死、无命中表现）。
"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim


def test_attack_state_survives_dict_roundtrip():
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    p = w.players[1]
    p.facing = 1
    # 触发攻击（UE GAS 风格：MeleeAttack Ability 激活，设 attack_anim 表现 + cooldown）
    sim.tick(w, {1: sim.InputState(attack=True)})
    assert p.attack_anim > 0          # 攻击动作表现
    assert p.attack_cooldown > 0      # 攻击冷却
    # 活跃的 MeleeAttack Ability 应挂到 world.active_abilities
    assert any(ab.ability_id == "melee" for ab in w.active_abilities)

    d = p.as_dict()
    # 关键字段必须都在快照里（表现 + 冷却 + 命中状态 + 颜色）
    assert "attack_anim" in d
    assert "attack_cooldown" in d
    assert "hit_facing" in d
    assert "hit_active" in d


def test_dash_freeze_state_survives_dict_roundtrip():
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    p = w.players[1]
    sim.tick(w, {1: sim.InputState(dash=True)})
    assert p.dash_timer > 0
    d = p.as_dict()
    assert d["dash_timer"] > 0
    assert d["dash_dir"] in (1, -1)


def test_full_state_reconstruction_matches():
    """经 as_dict 快照重建的玩家，技能状态与原文完全一致。"""
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    p = w.players[1]
    p.facing = 1
    for _ in range(3):
        sim.tick(w, {1: sim.InputState(attack=True)})
    p.dash_timer = 5
    p.frozen_timer = 30

    d = p.as_dict()
    # 重建
    p2 = G.PlayerState(d["player_id"], d["name"], G.Fixed(d["x"]), G.Fixed(d["y"]))
    for k in ("vx", "vy"):
        setattr(p2, k, G.Fixed(d[k]))
    for k in ("facing", "on_ground", "hp", "alive",
              "attack_timer", "attack_cooldown", "hit_facing", "hit_active",
              "attack_anim", "hit_anim", "dash_timer", "dash_dir", "dash_cooldown",
              "frozen_timer", "freeze_cooldown"):
        setattr(p2, k, d[k])
    # 关键技能状态应完全一致
    assert p2.attack_timer == p.attack_timer
    assert p2.attack_cooldown == p.attack_cooldown
    assert p2.hit_active == p.hit_active
    assert p2.dash_timer == p.dash_timer
    assert p2.frozen_timer == p.frozen_timer
