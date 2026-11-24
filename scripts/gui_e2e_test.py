"""真实 GUI e2e 测试：post KEYDOWN 事件驱动 run_gui 的事件按键，验证技能生效。"""

import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame
from sync_demo.net.server import Server
from sync_demo.net.client import Client
from sync_demo.core import game as G


server = Server("127.0.0.1", 7808)
server.mode = "state"
server.start()
time.sleep(0.2)

c1 = Client("127.0.0.1", 7808, "me")
c2 = Client("127.0.0.1", 7808, "enemy")
c1.connect(); c2.connect()
dl = time.monotonic() + 2.0
while time.monotonic() < dl and (c1.player_id is None or c2.player_id is None):
    time.sleep(0.02)

server.world.players[1].x = G.Fixed.from_float(180)
server.world.players[2].x = G.Fixed.from_float(220)
server.world.players[1].facing = 1
server.world.players[2].facing = -1

import threading
t = threading.Thread(target=c1.run_gui, daemon=True)
t.start()
time.sleep(0.5)


def press(key, duration=0.3):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))
    time.sleep(duration)
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=key))
    time.sleep(0.3)


# 攻击 J
press(pygame.K_j)
print("[TEST] attack: c2 hp =", server.world.players[2].hp,
      "c1 attack_anim =", server.world.players[1].attack_anim)

# 冲刺 K
x0 = server.world.players[1].x.raw
press(pygame.K_k)
print("[TEST] dash: x", x0, "->", server.world.players[1].x.raw,
      "dash_timer =", server.world.players[1].dash_timer)

# 冰冻 L（先重置位置/朝向/冷却，排除前面技能的状态干扰）
server.world.players[1].x = G.Fixed.from_float(180)
server.world.players[2].x = G.Fixed.from_float(220)
server.world.players[1].facing = 1
server.world.players[1].freeze_cooldown = 0
server.world.players[2].frozen_timer = 0
server.world.players[2].dash_timer = 0
press(pygame.K_l)
print("[TEST] freeze: c2 frozen_timer =", server.world.players[2].frozen_timer)

c1.close(); c2.close(); server.stop()
