"""客户端入口：``python -m sync_demo.client``。

角色由 ``SYNC_DEMO_ROLE`` 决定；本模块只在 client 角色下运行。
"""

import os
import sys


def main():
    role = os.environ.get("SYNC_DEMO_ROLE", "client")
    if role != "client":
        print("[sync_demo] not a client role (SYNC_DEMO_ROLE != client), exiting.")
        sys.exit(0)
    from sync_demo.net.client import run
    run()


if __name__ == "__main__":
    main()
