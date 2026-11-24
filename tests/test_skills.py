"""技能测试：普通攻击动作、冲刺（可躲冰冻）、冰冻（冻不住冲刺）。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim


def _two_players():
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 200, G.GROUND_Y)
    w.add_player(2, "p2", 240, G.GROUND_Y)
    p1 = w.players[1]
    p2 = w.players[2]
    p1.facing = 1          # p1 朝右，面向 p2
    return w, p1, p2


def test_attack_triggers_attack_anim():
    w, p1, p2 = _two_players()
    sim.tick(w, {1: sim.InputState(attack=True)})
    assert p1.attack_anim > 0          # 攻击动作开始


def test_attack_hit_triggers_hit_anim():
    w, p1, p2 = _two_players()
    p2.hp = G.MAX_HP
    hp0 = p2.hp
    sim.tick(w, {1: sim.InputState(attack=True)})
    assert p2.hp < hp0
    assert p2.hit_anim > 0             # 受击动作触发


def test_dash_moves_forward_and_grants_immunity():
    w, p1, p2 = _two_players()
    x0 = p1.x
    sim.tick(w, {1: sim.InputState(dash=True)})
    assert p1.dash_timer > 0
    assert p1.x > x0                   # 冲刺向前突进


def test_freeze_blocks_target_for_one_second():
    w, p1, p2 = _two_players()
    # p1 施放冰冻，p2 被冻住
    sim.tick(w, {1: sim.InputState(freeze=True)})
    assert p2.frozen_timer > 0         # 被冻住
    assert p2.frozen_timer >= G.FREEZE_TICKS - 1

    # 被冻期间不能动
    x_before = p2.x
    sim.tick(w, {2: sim.InputState(right=True)})
    assert p2.x == x_before            # 冻住时无法移动


def test_freeze_misses_dashing_target():
    w, p1, p2 = _two_players()
    # p2 先冲刺（瞬移），再 p1 施放冰冻——冻不住
    sim.tick(w, {2: sim.InputState(dash=True)})
    assert p2.dash_timer > 0
    sim.tick(w, {1: sim.InputState(freeze=True)})
    assert p2.frozen_timer == 0        # 冲刺中冻不住
