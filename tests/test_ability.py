"""UE GAS 风格技能（Ability）系统测试：激活→命中→伤害→结束。"""

import random

from sync_demo.core import ability as AB
from sync_demo.core import game as G
from sync_demo.core import sim


def _two_players(distance=40):
    """两个相邻玩家：p1 在 200，p2 在 200+distance，p1 朝右。"""
    rng = random.Random(1)
    w = G.World(rng)
    w.add_player(1, "p1", 200, G.GROUND_Y)
    w.add_player(2, "p2", 200 + distance, G.GROUND_Y)
    w.players[1].facing = 1
    return w


def test_melee_attack_deals_damage():
    w = _two_players(distance=40)
    hp0 = w.players[2].hp
    sim.tick(w, {1: sim.InputState(attack=True)})
    # 攻击命中：伤害 + 受击表现
    assert w.players[2].hp < hp0
    assert w.players[2].hit_anim > 0
    # 攻击者动作表现 + 一个活跃的 melee ability
    assert w.players[1].attack_anim > 0
    assert any(ab.ability_id == "melee" for ab in w.active_abilities)


def test_melee_misses_when_facing_away():
    w = _two_players(distance=40)
    w.players[1].facing = -1   # 背对 p2
    hp0 = w.players[2].hp
    sim.tick(w, {1: sim.InputState(attack=True)})
    assert w.players[2].hp == hp0   # 没打中


def test_dash_moves_player():
    w = _two_players(distance=200)
    x0 = w.players[1].x.raw
    sim.tick(w, {1: sim.InputState(dash=True)})
    # 冲刺：x 增大（朝右），dash_timer 激活，Dash ability 活跃
    assert w.players[1].x.raw > x0
    assert w.players[1].dash_timer > 0
    assert any(ab.ability_id == "dash" for ab in w.active_abilities)


def test_dash_dir_synced_for_render():
    """冲刺方向必须写进 PlayerState.dash_dir，否则渲染拖尾永远画在同一侧。"""
    # 朝右冲刺
    w = _two_players(distance=200)
    sim.tick(w, {1: sim.InputState(dash=True)})
    assert w.players[1].dash_dir == 1

    # 朝左冲刺
    w2 = _two_players(distance=200)
    w2.players[1].facing = -1
    x0 = w2.players[1].x.raw
    sim.tick(w2, {1: sim.InputState(dash=True)})
    assert w2.players[1].dash_dir == -1      # 方向正确同步
    assert w2.players[1].x.raw < x0          # 向左位移


def test_dash_ends_after_duration():
    w = _two_players(distance=200)
    sim.tick(w, {1: sim.InputState(dash=True)})
    # 推进足够多 tick，dash ability 结束、dash_timer 归零
    for _ in range(G.DASH_TICKS + 2):
        sim.tick(w, {})
    assert w.players[1].dash_timer == 0
    assert not any(ab.ability_id == "dash" for ab in w.active_abilities)


def test_freeze_freezes_target():
    w = _two_players(distance=40)
    sim.tick(w, {1: sim.InputState(freeze=True)})
    assert w.players[2].frozen_timer > 0


def test_freeze_does_not_freeze_dashing_target():
    # 目标冲刺中（dash_timer > 0），冻不住
    w = _two_players(distance=40)
    w.players[2].dash_timer = 5
    sim.tick(w, {1: sim.InputState(freeze=True)})
    assert w.players[2].frozen_timer == 0


def test_ability_serializes():
    w = _two_players(distance=40)
    sim.tick(w, {1: sim.InputState(attack=True)})
    ab = [a for a in w.active_abilities if a.ability_id == "melee"][0]
    d = ab.as_dict()
    assert d["ability_id"] == "melee"
    assert d["owner_id"] == 1
    assert "hit_targets" in d
