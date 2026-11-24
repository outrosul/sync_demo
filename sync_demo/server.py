"""服务器入口：``python -m sync_demo.server``。

角色由 ``SYNC_DEMO_ROLE`` 决定；本模块只在 server 角色下运行，否则打印提示退出。
"""

import os
import sys


def main():
    role = os.environ.get("SYNC_DEMO_ROLE", "client")
    if role != "server":
        print("[sync_demo] not a server role (SYNC_DEMO_ROLE != server), exiting.")
        sys.exit(0)
    from sync_demo.net.server import run
    run()


if __name__ == "__main__":
    main()
