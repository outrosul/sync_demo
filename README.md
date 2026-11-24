# sync_demo —— 帧同步 / 状态同步研究框架

用**纯 Python（无引擎，仅 pygame 做画面）**构建的联机同步研究框架，用于**制造并观测
「不同步」**，进而研究**预测、回滚（回拉）、平滑移动**等同步难题。

客户端与服务端共用一份代码，靠 `SYNC_DEMO_ROLE` 环境变量区分；演示**帧同步（lockstep）**
与**状态同步（state sync）**两种联网架构，服务器 console 或客户端 HUD 可运行时切换。

## 为什么是「研究框架」而不是「游戏」

核心不是做一个玩法，而是回答三个问题：

1. **什么情况下会不同步？** → 内置故障注入（延迟 / 丢包 / 乱序 / 重复 / 输入丢失 / 抖动 / 带宽限速）。
2. **怎么发现不同步？** → 每帧世界状态 hash（FNV-1a）对比 + 逐字段差异定位。
3. **怎么处理不同步？** → 预测、回滚（回拉）、预测不回滚、平滑移动（插值 / 虚影 / DR 外推），分层可研究。

**凡是效果不确定或有正反权衡的机制，都做成可开关**，由使用者自己体会差异。

## 特性

### 确定性基础
- **全定点数**（`Fixed` 8 位小数，×256）：无浮点漂移，帧同步两侧逐位一致。
- **显式随机源**：`random.Random(seed)` 传入，不读系统时间/全局随机。
- **固定 tick 阶段顺序**：应用输入 → 物理 → 技能推进 → 飞行物结算。

### 同步双模式
- **帧同步 lockstep**：服务器只收集/广播输入，各端本地 `sim.tick` 推演；客户端「本地预推演 + frame 回滚」。
- **状态同步 state**：服务器权威推演，广播快照；客户端插值/预测贴近权威。
- 运行时热切换（console `mode` 或客户端 HUD 点击），切换即改语义。

### 观测与诊断
- `core/sync_diff.py`：FNV-1a hash + 逐字段差异定位。
- `net/latency_probe.py`：延迟埋点（输入→发送→转发→接收各段耗时，可开关）。
- HUD 调试面板：帧率 / 模式 / tick / 玩家数 / 网速档位 / 回拉容差 / 当前按键 / 键盘焦点 / 最近按键。

### 网络层
- **双通道**：TCP（重要：握手/快照/事件）+ KCP over UDP（高频：输入/帧）。
- **纯 Python 最小 KCP**：滑动窗口 + ARQ 超时重传 + 快速重传 + 乱序重排，零编译依赖。
- **故障注入** `net/fault.py`：延迟/丢包/乱序/重复/输入丢失/抖动/带宽限速 + **四档网络预设**。
- **冗余发包** `net/redundancy.py`：DUP 冗余（1 原始 + N 份），接收端去重，可开关。
- **二进制协议** `net/protocol.py`：TLV 二进制编码（非 JSON），并区分 **Property（状态）/ RPC（事件）**。

### 预测 / 回滚 / 平滑（可开关）
| 机制 | 模块 | 说明 |
| --- | --- | --- |
| 客户端预测 + 回滚 | `net/prediction.py` + client | 本地预推演，frame 到达后回滚重放 |
| 预测回滚开关 | client `predict_rollback_enabled` | on=跳回权威 / off=多帧插值平滑纠正 |
| 纠错帧数可调 | `net/correction.py` | 1 帧跳变 vs N 帧平滑 |
| 回拉容差 | client `snap_threshold` | 偏差小于容差不回拉 |
| 内插 | `net/smoothing.py` | 远端玩家平滑跟随 |
| DR 外推 | `net/smoothing.DeadReckoner` | 收不到包时按速度惯性外推 |
| 内插↔外插切换 | client `interp_extrap_mode` | auto（按收包间隔）/ interp / extrap |
| 抖动缓冲 | `net/jitter_buffer.py` | 帧缓存 + 动态调插值速度 |
| 移动预测 | `net/movement_predict.py` | 半 RTT 先行（摇杆方向累加） |
| 等帧 / 追帧 | `net/logic_clock.py` | 队列空=等，积压=加速追 |
| 渲染跳帧 | client `render_skip` | 逻辑照推、渲染隔帧 |

### 权威与仲裁
- **延迟补偿** `net/timewarp.py`：历史世界 buffer + 倒带回退命中判定（`scripts/lagcomp_side_effect_demo.py` 演示副作用）。
- **时钟同步** `net/clock_sync.py`：NTP 式往返对时 + 中位数过滤异常样本。
- **断线重连**：`session_token` + 服务器 10 秒宽限（期内重连恢复身份与状态，超时真移除）。
- **分布式授权**：`MSG_CLAIM` 争夺「授权标记」，谁持有谁主控（解决 p2p 无法验证）。
- **客户端碰撞上报 + 服务端校验**：`MSG_HIT_REPORT`，服务器回退历史世界校验后才结算。

### 技能系统（UE GAS 风格）
- `core/ability.py`：技能是**独立 Ability 对象**（`MeleeAttack` / `Dash` / `Freeze`），
  激活 → 检测命中 → 结算伤害 → 结束 的状态机；`World.active_abilities` 纳入快照/hash 同步。
- 技能输入走**按键上升沿 RPC**（`MSG_ATTACK`/`MSG_DASH`/`MSG_FREEZE`，TCP 可靠通道），
  避免瞬时事件被每帧输入覆盖吞掉。

### 回放
- `net/replay.py`：记录每帧操作序列，重放复现同一局（帧同步天然优势，文件极小）。

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest tests/ -q          # 全部测试（含代码风格守卫）
```

### 服务器（可部署在 Linux）

```bash
SYNC_DEMO_ROLE=server python -m sync_demo.server
# console 命令：
#   mode lockstep|state          切换同步方式
#   fault <param> <value>        注入故障（latency/loss/reorder/duplicate/drop_input/jitter/throttle）
#   preset perfect|good|general|bad   网络分档一键切
#   lagcomp on|off               延迟补偿开关
#   setlag <pid> <ms>            设置某玩家估测延迟（供延迟补偿回退）
#   hash                         打印当前权威世界 hash
#   quit                         退出
```

### 客户端（可开多个，进入同一张地图）

```bash
SYNC_DEMO_ROLE=client SYNC_DEMO_HOST=127.0.0.1 python -m sync_demo.client
```

**操作**：`A/← D/→` 移动 · `W/↑/空格` 跳跃 · `J/Z` 攻击 · `K/LShift` 冲刺 · `L/X` 冰冻

**客户端 HUD（左上角面板，全中文）**
- `帧同步` / `状态同步`：切换同步模式（单选，当前模式高亮）
- `网速：极好/好/一般/差`：四档网络一键切换
- 开关组（按当前模式自动置灰不可用的）：`等帧追帧` `预测回滚` `内插外插` `渲染跳帧` `争夺标记`
- `容差 -/+`：回拉容差（像素）
- **鼠标悬停任意控件显示中文说明**

**右上角调试信息**：帧率 / 模式 / tick / 玩家数 / 网速 / 容差 / 按键 / 键盘焦点 / 最近按键

> 小提示：若按键无反应，先点击窗口让它获得焦点（HUD 会显示「键盘 无焦点(点窗口)」）。

## 研究演示 / 工具脚本

```bash
# 不同步观测（无需真实网络）
python scripts/desync_demo.py             # 输入丢失导致 desync + hash 定位
python scripts/desync_socket_demo.py      # 真实 socket：客户端多推演被服务器检测

# 预测与延迟补偿
python scripts/prediction_demo.py         # 客户端预测 + 回滚
python scripts/lagcomp_side_effect_demo.py # 延迟补偿副作用（高延迟玩家获利）

# 批量跑测：遍历弱网 × 冗余数，输出 desync 率矩阵
python scripts/benchmark_sync.py

# GUI 测试（真实按键 → 技能验证）
python scripts/gui_e2e_test.py            # 自动验证攻击/冲刺/冰冻
python scripts/gui_screenshot_test.py --show   # 真实窗口 + 每步截图
python scripts/min_key_test.py            # 键盘诊断（按键是否到达）

# 端到端冒烟
python scripts/smoke_e2e.py
```

## 目录

| 目录 | 内容 |
| --- | --- |
| `sync_demo/core/` | 逻辑层：`game`（Fixed/状态）· `sim`（确定性推演）· `sync_diff`（hash/定位）· `ability`（技能） |
| `sync_demo/net/` | 网络层：`protocol` · `server` · `client` · `fault` · `kcp` · `transport` · `prediction` · `smoothing` · `timewarp` · `logic_clock` · `jitter_buffer` · `movement_predict` · `correction` · `latency_probe` · `redundancy` · `replay` · `clock_sync` |
| `scripts/` | 研究演示与测试工具 |
| `docs/` | 规范与台账（`208_sync-references.md` 是同步技术参考文献整理） |
| `tests/` | 135 项测试 + 代码风格守卫 |

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SYNC_DEMO_ROLE` | `client` | `server` / `client` |
| `SYNC_DEMO_HOST` | `127.0.0.1` | 服务器地址 |
| `SYNC_DEMO_PORT` | `7808` | TCP 端口（UDP 自动 +1） |
| `SYNC_DEMO_NAME` | `client` | 玩家名 |

详见 `AGENTS.md`（入口路由）与 `docs/` 下各文档。
