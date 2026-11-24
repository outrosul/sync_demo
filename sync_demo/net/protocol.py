"""协议：消息的编解码。

**设计（二进制 + Property/RPC 分离）**：

不再用 JSON 文本，改用**二进制 TLV 编码**，并把消息分为两类语义：

- **Property（属性同步）**：持续同步的状态（快照/位置/血量等），丢失靠下一次补齐，
  不要求每个都送达 —— 例如 ``snapshot``。
- **RPC（调用同步）**：一次性事件（攻击/命中/切模式/争夺标记等），需可靠送达。

分类好处：Property 类可走「小体积、容忍丢失」的语义，RPC 类走「可靠、保序」，从协议
层面就区分了「数据」与「事件」，避免用同一套 JSON 把所有东西混在一起。

**二进制格式**：

```
[type(1字节)] [长度(2字节，大端)] [payload...]
```

- ``type``：1 字节枚举（不再是字符串），既省体积又天然区分消息类别。
- payload：TLV 编码的字段 —— 每个字段 ``[字段类型(1字节)][字段长度(2字节)][字节值]``。
  支持 int / float / bool / str / bytes / list / dict 递归编码。

调用方仍用 **dict 语义**（``{"type": "...", ...}``），``encode``/``decode`` 内部完成
dict ↔ 二进制的转换，因此 server / client / transport 的现有代码基本不用改。
"""

import struct

# ---- 消息类型常量（字符串，供调用方使用）----
MSG_HELLO = "hello"
MSG_WELCOME = "welcome"
MSG_INPUT = "input"
MSG_FRAME = "frame"
MSG_SNAPSHOT = "snapshot"
MSG_MODE = "mode"
MSG_MODE_REQ = "mode_req"
MSG_HASH = "hash"
MSG_FAULT = "fault"
MSG_ATTACK = "attack_req"
MSG_ATTACK_RESULT = "attack_result"
MSG_DASH = "dash_req"        # 客户端 -> 服务器：冲刺技能事件（离散边沿，RPC 通道）
MSG_FREEZE = "freeze_req"    # 客户端 -> 服务器：冰冻技能事件（离散边沿，RPC 通道）
MSG_HIT_REPORT = "hit_report"
MSG_CLAIM = "claim"
MSG_OWNER = "owner"
MSG_BYE = "bye"

# ---- 消息类型 -> 1 字节枚举 ID ----
TYPE_TO_ID = {
    MSG_HELLO: 1,
    MSG_WELCOME: 2,
    MSG_INPUT: 3,
    MSG_FRAME: 4,
    MSG_SNAPSHOT: 5,
    MSG_MODE: 6,
    MSG_MODE_REQ: 7,
    MSG_HASH: 8,
    MSG_FAULT: 9,
    MSG_ATTACK: 10,
    MSG_ATTACK_RESULT: 11,
    MSG_HIT_REPORT: 12,
    MSG_CLAIM: 13,
    MSG_OWNER: 14,
    MSG_BYE: 15,
    MSG_DASH: 16,
    MSG_FREEZE: 17,
}
ID_TO_TYPE = {v: k for k, v in TYPE_TO_ID.items()}

# ---- Property / RPC 分类 ----
# Property：状态持续同步（丢一次靠下次补）；其余为 RPC（一次性事件，需可靠送达）。
PROPERTY_TYPES = {
    MSG_SNAPSHOT,
}


def is_property(msg):
    """判断一条消息是否属于 Property（属性同步）。"""
    return msg.get("type") in PROPERTY_TYPES


def is_rpc(msg):
    """判断一条消息是否属于 RPC（调用同步）。"""
    return msg.get("type") not in PROPERTY_TYPES


# TLV 字段类型标签
_T_INT = 1
_T_FLOAT = 2
_T_BOOL = 3
_T_STR = 4
_T_BYTES = 5
_T_LIST = 6
_T_DICT = 7
_T_NONE = 8


def _encode_value(v):
    """把单个值编码成 TLV 字节。"""
    if v is None:
        return bytes([_T_NONE])
    if isinstance(v, bool):
        return bytes([_T_BOOL]) + struct.pack(">H", 1) + bytes([1 if v else 0])
    if isinstance(v, int):
        # 有符号 64 位范围内用 >q；超出（如 FNV hash 可能 >2^63）转字符串存，无损
        if -2**63 <= v < 2**63:
            data = struct.pack(">q", v)
            return bytes([_T_INT]) + struct.pack(">H", len(data)) + data
        return _encode_value(str(v))
    if isinstance(v, float):
        data = struct.pack(">d", v)
        return bytes([_T_FLOAT]) + struct.pack(">H", len(data)) + data
    if isinstance(v, str):
        data = v.encode("utf-8")
        return bytes([_T_STR]) + struct.pack(">H", len(data)) + data
    if isinstance(v, (bytes, bytearray)):
        data = bytes(v)
        return bytes([_T_BYTES]) + struct.pack(">H", len(data)) + data
    if isinstance(v, list) or isinstance(v, tuple):
        body = b"".join(_encode_value(x) for x in v)
        return bytes([_T_LIST]) + struct.pack(">H", len(body)) + body
    if isinstance(v, dict):
        body = b""
        for k in sorted(v.keys(), key=str):
            body += _encode_value(str(k))
            body += _encode_value(v[k])
        return bytes([_T_DICT]) + struct.pack(">H", len(body)) + body
    # 其它类型（如 tuple）转 str 兜底
    return _encode_value(str(v))


def _decode_value(buf, offset):
    """解码单个 TLV 值，返回 (value, 新 offset)。"""
    tag = buf[offset]
    offset += 1
    if tag == _T_NONE:
        return None, offset
    length = struct.unpack_from(">H", buf, offset)[0]
    offset += 2
    data = buf[offset:offset + length]
    offset += length
    if tag == _T_BOOL:
        return bool(data[0]), offset
    if tag == _T_INT:
        return struct.unpack(">q", data)[0], offset
    if tag == _T_FLOAT:
        return struct.unpack(">d", data)[0], offset
    if tag == _T_STR:
        return data.decode("utf-8"), offset
    if tag == _T_BYTES:
        return bytes(data), offset
    if tag == _T_LIST:
        items = []
        i = 0
        while i < len(data):
            v, i = _decode_value(data, i)
            items.append(v)
        return items, offset
    if tag == _T_DICT:
        d = {}
        i = 0
        while i < len(data):
            k, i = _decode_value(data, i)
            v, i = _decode_value(data, i)
            d[k] = v
        return d, offset
    return None, offset


def encode(msg):
    """把 dict 编码成二进制消息（type 1 字节 + 长度 2 字节 + TLV payload）。"""
    mtype = msg.get("type")
    type_id = TYPE_TO_ID.get(mtype, 0)
    # payload 不含 type 字段（type 已由首字节标识），仅编码其余字段
    payload = {}
    for k, v in msg.items():
        if k == "type":
            continue
        payload[k] = v
    body = _encode_value(payload)
    return bytes([type_id]) + struct.pack(">H", len(body)) + body


def decode(buf):
    """从字节缓冲里尽量解出完整消息。

    返回 ``(messages, remaining_bytes)``：``messages`` 是本轮解出的 dict 列表（含
    还原的 ``type`` 字符串），``remaining_bytes`` 是尚未凑成整段消息的字节。
    """
    messages = []
    offset = 0
    n = len(buf)
    while offset + 3 <= n:
        type_id = buf[offset]
        length = struct.unpack_from(">H", buf, offset + 1)[0]
        total = 3 + length
        if offset + total > n:
            break   # 不完整，留作剩余
        body = buf[offset + 3:offset + total]
        payload, _ = _decode_value(body, 0)
        mtype = ID_TO_TYPE.get(type_id, "unknown")
        msg = {"type": mtype}
        if isinstance(payload, dict):
            msg.update(payload)
        messages.append(msg)
        offset += total
    return messages, buf[offset:]


def decode_all(buf):
    """一次解析缓冲里所有完整消息（丢弃残段）。"""
    messages, _rest = decode(buf)
    return messages
