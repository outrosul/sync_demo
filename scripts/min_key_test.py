"""真实窗口键盘诊断（改进版）：显示按键、焦点、事件计数，点窗口也不会丢按键。

用法：
    python scripts/min_key_test.py

窗口里会显示：
- 焦点状态（SDL 认为窗口是否有键盘焦点）
- 每个关注键的 ON/off 状态
- 最近收到的 KEYDOWN（名字 + 累计次数）
- 收到的所有事件类型统计（看 KEYDOWN 有没有到达）

按 A D W J K L 方向键 空格 Shift，观察显示是否变化。
"""

import os
import sys

# 真实窗口：不设 dummy
os.environ.pop("SDL_VIDEODRIVER", None)


def main():
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((700, 420))
    pygame.display.set_caption("键盘诊断 - 按 A D W J K L 方向键")
    # 关闭 SDL 文本输入（IME），否则字母键会被输入法接管
    pygame.key.stop_text_input()
    font = pygame.font.Font(None, 24)
    try:
        font = pygame.font.Font("C:/Windows/Fonts/msyh.ttc", 20)
    except OSError:
        pass
    clock = pygame.time.Clock()

    held = {}
    last_key = (None, 0)
    event_counts = {}
    running = True

    print("窗口已打开。按 A D W J K L 方向键 空格 Shift，看窗口里的提示。")
    print("关闭窗口退出。")

    while running:
        # 先 pump 一次让 SDL 处理系统消息（键盘事件靠它从系统队列拉进 pygame 队列）。
        # 这里在主循环最前面调，不在事件处理中间调，是安全用法。
        pygame.event.pump()
        for event in pygame.event.get():
            event_counts[event.type] = event_counts.get(event.type, 0) + 1
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                held[event.key] = True
                name = pygame.key.name(event.key)
                last_key = (name, last_key[1] + 1)
                print("[KEYDOWN] {} (keycode={})".format(name, event.key))
            elif event.type == pygame.KEYUP:
                held[event.key] = False

        screen.fill((25, 25, 35))
        focused = pygame.key.get_focused()

        info_lines = [
            "焦点: {}".format("有" if focused else "无（点一下窗口）"),
            "最近按键: {}".format(("{} #{}".format(*last_key)) if last_key[0] else "未收到"),
            "KEYDOWN 事件数: {}".format(event_counts.get(pygame.KEYDOWN, 0)),
            "窗口事件数: {}".format(event_counts.get(pygame.WINDOWFOCUSGAINED, 0)
                                + event_counts.get(pygame.WINDOWFOCUSLOST, 0)),
        ]
        y = 10
        for line in info_lines:
            surf = font.render(line, True, (200, 220, 255))
            screen.blit(surf, (15, y))
            y += 26

        # 关注键状态
        watch = [
            (pygame.K_a, "A"), (pygame.K_d, "D"), (pygame.K_w, "W"),
            (pygame.K_j, "J"), (pygame.K_k, "K"), (pygame.K_l, "L"),
            (pygame.K_LEFT, "<-"), (pygame.K_RIGHT, "->"),
            (pygame.K_UP, "UP"), (pygame.K_SPACE, "SPACE"),
            (pygame.K_LSHIFT, "LSHIFT"),
        ]
        y = 130
        for key, label in watch:
            on = held.get(key, False)
            color = (120, 255, 120) if on else (110, 110, 120)
            surf = font.render("{}: {}".format(label, "ON" if on else "off"), True, color)
            screen.blit(surf, (15, y))
            y += 26

        pygame.display.flip()
        clock.tick(30)
    pygame.quit()


if __name__ == "__main__":
    main()
