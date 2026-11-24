"""二进制协议测试：往返、Property/RPC 分类、嵌套/tuple/hash 编码。"""

from sync_demo.net import protocol as P


def test_binary_roundtrip_basic():
    msg = {"type": P.MSG_INPUT, "left": True, "right": False}
    data = P.encode(msg)
    msgs, rest = P.decode(data)
    assert rest == b""
    assert msgs == [msg]


def test_binary_roundtrip_nested():
    msg = {"type": P.MSG_SNAPSHOT, "tick": 100,
           "players": [{"player_id": 1, "x": 25600, "hp": 100}]}
    data = P.encode(msg)
    msgs, _ = P.decode(data)
    assert msgs == [msg]


def test_tuple_encoded_as_list():
    msg = {"type": P.MSG_FRAME, "inputs": {str(1): (True, False, True, False, False, False)}}
    data = P.encode(msg)
    msgs, _ = P.decode(data)
    # tuple 解出后是 list（二进制协议无损往返为 list）
    decoded = msgs[0]["inputs"]["1"]
    assert list(decoded) == [True, False, True, False, False, False]


def test_large_hash_preserved():
    big = 2**64 - 1   # 超过有符号 64 位
    msg = {"type": P.MSG_HASH, "hash": big, "tick": 5}
    data = P.encode(msg)
    msgs, _ = P.decode(data)
    # 超范围 int 编码成 str，server 侧再 int() 还原；这里验证无损
    assert int(msgs[0]["hash"]) == big


def test_property_rpc_classification():
    assert P.is_property({"type": P.MSG_SNAPSHOT}) is True
    assert P.is_rpc({"type": P.MSG_SNAPSHOT}) is False
    assert P.is_property({"type": P.MSG_INPUT}) is False
    assert P.is_rpc({"type": P.MSG_ATTACK}) is True


def test_binary_smaller_than_old_json_typical():
    # 二进制编码应比 JSON 更紧凑（体积更小）
    msg = {"type": P.MSG_INPUT, "left": True, "right": False,
           "jump": False, "attack": False, "dash": False, "freeze": False}
    import json
    json_size = len(json.dumps(msg).encode("utf-8"))
    bin_size = len(P.encode(msg))
    assert bin_size < json_size


def test_partial_buffer_left_as_remaining():
    msg = {"type": P.MSG_HELLO, "name": "a" * 100}
    data = P.encode(msg)
    # 切掉尾巴，剩余应被保留
    msgs, rest = P.decode(data[:10])
    assert msgs == []
    assert rest == data[:10]
