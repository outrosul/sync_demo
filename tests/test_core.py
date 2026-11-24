"""测试：逻辑层确定性、玩法规则、协议编解码、代码风格守卫。"""

import random

from sync_demo.core import game as G
from sync_demo.core import sim
from sync_demo.net import protocol as P


def _new_world(seed=1):
    rng = random.Random(seed)
    w = G.World(rng)
    w.add_player(1, "p1", 100, G.GROUND_Y)
    w.add_player(2, "p2", 300, G.GROUND_Y)
    return w


def test_move_right():
    w = _new_world()
    p1 = w.players[1]
    x0 = p1.x
    for _ in range(10):
        sim.tick(w, {1: sim.InputState(right=True)})
    assert p1.x > x0
    assert p1.facing == 1


def test_move_left_changes_facing():
    w = _new_world()
    p1 = w.players[1]
    p1.x = G.Fixed.from_float(400)
    sim.tick(w, {1: sim.InputState(left=True)})
    assert p1.facing == -1
    assert p1.x < G.Fixed.from_float(400)


def test_jump_lands():
    w = _new_world()
    p1 = w.players[1]
    sim.tick(w, {1: sim.InputState(jump=True)})
    assert p1.on_ground is False
    # 推进足够久后应回到地面
    for _ in range(200):
        sim.tick(w, {1: sim.InputState()})
    assert p1.on_ground is True
    assert p1.y == G.Fixed.from_float(G.GROUND_Y)


def test_attack_damages_enemy_in_range():
    w = _new_world()
    p1 = w.players[1]
    p2 = w.players[2]
    p1.x = G.Fixed.from_float(300)
    p2.x = G.Fixed.from_float(320)
    p1.facing = 1
    hp0 = p2.hp
    sim.tick(w, {1: sim.InputState(attack=True)})
    # 攻击打在生效期内（近战结算在 apply 之后同 tick 进行）
    assert p2.hp < hp0


def test_attack_misses_wrong_direction():
    w = _new_world()
    p1 = w.players[1]
    p2 = w.players[2]
    p1.x = G.Fixed.from_float(300)
    p2.x = G.Fixed.from_float(320)
    p1.facing = -1          # 背对敌人
    hp0 = p2.hp
    sim.tick(w, {1: sim.InputState(attack=True)})
    assert p2.hp == hp0


def test_determinism_same_input_same_state():
    w1 = _new_world(seed=7)
    w2 = _new_world(seed=7)
    seq = [
        {1: sim.InputState(right=True), 2: sim.InputState(left=True)},
        {1: sim.InputState(jump=True), 2: sim.InputState(attack=True)},
        {1: sim.InputState(attack=True), 2: sim.InputState(right=True)},
    ]
    for inputs in seq:
        sim.tick(w1, inputs)
        sim.tick(w2, inputs)
    for pid in (1, 2):
        a = w1.players[pid]
        b = w2.players[pid]
        assert (a.x, a.y, a.hp, a.alive, a.facing) == (b.x, b.y, b.hp, b.alive, b.facing)


def test_protocol_roundtrip():
    msg = {"type": P.MSG_INPUT, "left": True, "right": False}
    data = P.encode(msg)
    msgs, rest = P.decode(data)
    assert rest == b""
    assert msgs == [msg]


def test_protocol_partial_buffer():
    data1 = P.encode({"type": "a"})[:5]
    msgs, rest = P.decode(data1)
    assert msgs == []
    assert rest == data1


def test_code_style_no_lambda_no_try():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "sync_demo"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for line_no, line in enumerate(lines, 1):
            assert "lambda" not in line, "{}:{} 禁用 lambda".format(path, line_no)
        # 禁止「用 try/except 包业务逻辑」：允许的只有 io/控制流/输入解析边界的防御类异常。
        allowed = ("OSError", "BlockingIOError", "EOFError", "KeyboardInterrupt",
                   "ConnectionError", "ValueError", "KeyError")
        for line_no, line in enumerate(lines, 1):
            if line.strip() == "try:":
                except_line = _matching_except(lines, line_no).strip()
                assert any(a in except_line for a in allowed), \
                    "{}:{} 只允许 io/控制流防御类 except，禁用 try/except 包业务逻辑".format(path, line_no)


def _matching_except(lines, try_index):
    """从 try_index 那行往下找第一个缩进与 try 相同的 except 行。"""
    indent = len(lines[try_index - 1]) - len(lines[try_index - 1].lstrip())
    for line in lines[try_index:]:
        if line.strip().startswith("except") and (len(line) - len(line.lstrip())) == indent:
            return line
    return ""
