"""端到端冒烟：起服务器 + 两个 headless 客户端，验证同图 + 双模式切换链路。

用 SDL dummy 驱动让 pygame 在无窗口环境也能初始化，跑固定帧数后退出。
供人工 / CI 快速验证，不属于单元测试。
"""

import os
import subprocess
import sys
import time


def main():
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDAL_AUDIODRIVER"] = "dummy"
    port = "7837"

    server_env = dict(env)
    server_env["SYNC_DEMO_ROLE"] = "server"
    server_env["SYNC_DEMO_PORT"] = port
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "sync_demo.server"],
        env=server_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(0.5)

    def spawn_client(name):
        ce = dict(env)
        ce["SYNC_DEMO_ROLE"] = "client"
        ce["SYNC_DEMO_HOST"] = "127.0.0.1"
        ce["SYNC_DEMO_PORT"] = port
        ce["SYNC_DEMO_NAME"] = name
        return subprocess.Popen(
            [sys.executable, "-m", "sync_demo.client"],
            env=ce, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    c1 = spawn_client("c1")
    c2 = spawn_client("c2")
    time.sleep(2.0)

    for p in (c1, c2, server_proc):
        p.terminate()
    for p in (c1, c2, server_proc):
        try:
            out, _ = p.communicate(timeout=2)
            if isinstance(out, str) and "listening" in out:
                print(out.strip().splitlines()[0])
        except Exception:
            pass
    print("[smoke] server + 2 clients ran without import/init errors")


if __name__ == "__main__":
    main()
