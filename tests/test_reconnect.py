"""断线重连测试：宽限期内重连恢复身份+状态，超时真移除。"""

import time

from sync_demo.core import game as G
from sync_demo.net.server import Server
from sync_demo.net.client import Client


def test_reconnect_within_grace_restores_identity():
    server = Server("127.0.0.1", 7950)
    server.mode = "state"
    server.disconnect_grace = 10.0
    server.start()
    time.sleep(0.15)

    c1 = Client("127.0.0.1", 7950, "c1")
    c1.connect()
    time.sleep(0.3)
    end = time.monotonic() + 2.0
    while time.monotonic() < end and c1.player_id is None:
        time.sleep(0.02)
    assert c1.player_id is not None
    orig_pid = c1.player_id

    # 模拟断线：直接关闭 socket（服务器会将其挂起进 disconnected，不立即移除）
    c1.close()
    time.sleep(0.3)

    # 宽限期内世界里的玩家仍在（未移除）
    assert orig_pid in server.world.players

    # 用同一个 session_token 重连（新 Client 对象，但 token 复用）
    c2 = Client("127.0.0.1", 7950, "c1")
    c2.session_token = c1.session_token   # 复用身份 token
    c2.connect()
    time.sleep(0.3)
    while time.monotonic() < end and c2.player_id is None:
        time.sleep(0.02)

    # 重连后应恢复原 player_id
    assert c2.player_id == orig_pid
    server.stop()
    c2.close()


def test_grace_expiry_removes_player():
    server = Server("127.0.0.1", 7951)
    server.mode = "state"
    server.disconnect_grace = 0.2   # 缩短宽限期，便于测试「超时真移除」
    server.start()
    time.sleep(0.15)

    c1 = Client("127.0.0.1", 7951, "c1")
    c1.connect()
    time.sleep(0.3)
    end = time.monotonic() + 2.0
    while time.monotonic() < end and c1.player_id is None:
        time.sleep(0.02)
    orig_pid = c1.player_id

    c1.close()   # 断线
    # 等待超过宽限期，服务器应真正移除该玩家
    time.sleep(0.6)

    assert orig_pid not in server.world.players
    server.stop()
