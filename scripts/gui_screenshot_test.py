"""截图式端到端测试：真实窗口 + 按键 + 每步截图，用于人工看画面验证技能表现。

用法：
    python scripts/gui_screenshot_test.py [--show]

运行后：
1. 起真实服务器 + 2 个客户端 GUI 窗口
2. 依次注入 攻击(J) / 冲刺(K) / 冰冻(L) 按键
3. 每步保存截图到 screenshots/ 目录
4. 打印服务器世界状态（hp / attack_anim / dash_timer / frozen_timer）

截图文件：
    screenshots/01_init.png        初始画面
    screenshots/02_attack.png      按攻击后
    screenshots/03_dash.png        按冲刺后
    screenshots/04_freeze.png      按冰冻后
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "screenshots")


def main():
    show = "--show" in sys.argv
    if not show:
        os.environ["SDL_VIDEODRIVER"] = "dummy"

    import pygame
    from sync_demo.net.server import Server
    from sync_demo.net.client import Client
    from sync_demo.core import game as G

    os.makedirs(SHOT_DIR, exist_ok=True)

    server = Server("127.0.0.1", 7808)
    server.mode = "state"
    server.start()
    time.sleep(0.2)

    c1 = Client("127.0.0.1", 7808, "me")
    c2 = Client("127.0.0.1", 7808, "enemy")
    c1.connect()
    c2.connect()
    dl = time.monotonic() + 2.0
    while time.monotonic() < dl and (c1.player_id is None or c2.player_id is None):
        time.sleep(0.02)

    # 摆近（让攻击能命中）
    server.world.players[1].x = G.Fixed.from_float(180.0)
    server.world.players[2].x = G.Fixed.from_float(220.0)
    server.world.players[1].facing = 1
    server.world.players[2].facing = -1

    import threading
    t1 = threading.Thread(target=c1.run_gui, daemon=True)
    t1.start()
    time.sleep(0.5)

    def snapshot(name):
        path = os.path.join(SHOT_DIR, name)
        # 从服务器世界快照渲染一帧到当前 c1 screen（拿到画面）
        pygame.image.save(c1.screen, path)
        print("[shot] saved", os.path.abspath(path))


    def press(key, name, duration=0.35):
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))
        time.sleep(duration)
        pygame.event.post(pygame.event.Event(pygame.KEYUP, key=key))
        time.sleep(0.35)
        snapshot(name)
        p1 = server.world.players[1]
        p2 = server.world.players[2]
        print("[state] hp1={} hp2={} a1_anim={} a2_anim={} dash1={} frz2={}".format(
            p1.hp, p2.hp, p1.attack_anim, p2.attack_anim, p1.dash_timer, p2.frozen_timer))

    snapshot("01_init.png")
    print("=== 初始 ===")

    press(pygame.K_j, "02_attack.png")
    print("=== 攻击 J ===")

    press(pygame.K_k, "03_dash.png")
    print("=== 冲刺 K ===")

    press(pygame.K_l, "04_freeze.png")
    print("=== 冰冻 L ===")

    c1.close()
    c2.close()
    server.stop()
    time.sleep(0.2)


if __name__ == "__main__":
    main()
