# 进度与待做

## 已完成

- 通用 agent 说明抽取落地（`AGENTS.md` + `docs/`，三段编号对齐）。
- 逻辑层 `core/`：`game`（Fixed 定点数据）· `sim`（确定性推演：移动/跳跃/攻击/冲刺/冰冻）。
- **定点数**：`Fixed` 类（8 位小数），物理量全定点，测试验证「浮点不相等、定点相等」。
- **同步观测**：`core/sync_diff.py` —— 世界状态 FNV-1a hash 对比 + 逐字段差异定位。
- **故障注入**：`net/fault.py` —— 延迟/丢包/乱序/输入重复/输入丢失/抖动，可开关。
- **desync 观测接入真实网络**：客户端帧同步下每 N 帧上报 `world_hash`，服务器比对权威 hash，
  不一致即打印 DESYNC + 权威字段摘要（`MSG_HASH` / `MSG_FAULT` 协议）。
- **故障注入命令**：服务器 console `fault <param> <value>` 广播注入参数给所有客户端。
- 纯 Python 手写最小 KCP（`net/kcp.py`，滑动窗口+ARQ+快速重传，假时钟测试）。
- **预测 + 回滚**：`net/prediction.py`（`PredictedClient`：本地预测、权威回滚、重放未确认输入、历史 buffer）。
- **平滑移动**：`net/smoothing.py`（`InterpolatedTracker` 落后一帧插值 · `GhostTracker` 服务器位置虚影）。
- **双通道 TCP+KCP 接入**：重要消息（握手/欢迎/模式/故障/快照）走 TCP，非重要（输入/帧）走 KCP over UDP；
  服务器按 conv 分配 + UDP 源地址学习区分客户端。
- **虚影渲染**：客户端对远端玩家画「服务器权威的滞后插值虚影」。
- **客户端调试 HUD**：右上角实时 FPS/模式/tick/玩家数/6 项故障参数；左上角按钮面板
  （模式切换 lockstep/state + 6 种故障参数 +/- 可点击调整）；底部一行技能按键提示。
- **下行故障注入**：客户端收包也走 fault（丢包/延迟），上行+下行 6 种都可手动调。
- **模式切换请求**：客户端按钮发 `MSG_MODE_REQ`，服务器 `set_mode` 后广播 `MSG_MODE` 生效。
- **帧同步修复**：客户端锁步「本地预推演 + frame 回滚重放」，步长与服务器一致（修复
  此前客户端比服务器少 2 tick 的错位）；新玩家加入/离开时服务器向已有客户端补发快照
  补齐玩家集合（修复老客户端缺新玩家实体导致的永久不同步）。验证：真实 GUI 循环下
  双客户端持续 hash 一致、tick 对齐。
- **帧同步预测/回拉**：客户端发出输入后立即从确认基线重放未确认输入得到预测世界（即时
  响应），收到 frame 回滚重放对齐——正是「预测、回拉」在锁步下的落地。
- **按钮文案**：HUD 模式按钮与调试信息把 `lockstep` 显示为 `frame`，与 `state` 并列便于区分。
- **回拉容差（snap threshold）**：帧同步「软回滚」——预测与权威偏差 ≤ 容差就不回拉（保持预测、
  画面平滑），超阈值才回拉；容差在 HUD 可 +/- 调（步进 2px），右上角实时显示。测试 `test_snap_threshold.py`。
- **服务器异常断连修复**：客户端强制 RST 不再崩服务器线程（recv/send/accept 补 OSError 防御）。
- **同步参考文献**：`docs/208_sync-references.md`，收录 7 篇资料（帧/状态同步分享、同步概述、
  ARPG 战斗同步、网络同步、G67 技能同步、从零同步探索）+ 合并「可模拟问题」总清单。
  ⚠️ 全文已按要求「去链接、去网易化」（无 URL、无 KM/item 内部 ID）。
- **延迟补偿 Lag Compensation**：`net/timewarp.py`（`WorldHistoryBuffer` 历史世界 buffer +
  `rewind_hit_test` 回退命中判定）；server 每 tick 归档历史、`MSG_ATTACK` 攻击请求走
  `_on_attack_request` 回退裁判；console `lagcomp on/off` / `setlag <pid> <ms>`。测试 `test_lagcomp.py`。
- **帧同步「等帧/追帧」**：`net/logic_clock.py`（`FixedStepScheduler` 固定步长调度器：队列空等帧、
  积压追帧、补帧原则 `Sum(dt)==Sum(wall_time)`）；客户端加 `use_frame_scheduler` 开关（默认关），
  打开后 frame 入队由主循环按调度器消费。测试 `test_logic_clock.py`。
- **修复 confirmed_world 未随快照更新**：锁步下新玩家加入的补发快照会更新 world，但
  `confirmed_world` 未同步 → 下次 frame 回滚把新玩家又冲掉（老客户端永远缺新玩家）。已修复：
  `_apply_snapshot` 末尾同步 `confirmed_world`。
- **Jitter Buffer（抖动平滑）**：`net/jitter_buffer.py`（帧缓存 + 动态插值速度：水位高加速、
  水位低减速、耗尽降级交 DR 预测）。测试 `test_jitter_buffer.py`。
- **移动预测（半 RTT 先行）**：`net/movement_predict.py`（摇杆历史 buffer + 半个 RTT 累加）。
  测试 `test_movement_predict.py`。
- **纠错帧数可调**：`net/correction.py`（`Corrector` 多帧线性插值逼近权威：1帧跳变 vs N帧平滑）。
  测试 `test_correction.py`。
- **延迟补偿副作用演示**：`scripts/lagcomp_side_effect_demo.py`（高延迟玩家获利的反直觉结论）。
- **预测回滚 vs 预测不回滚开关**：`net/correction.py`（`Corrector` 平滑纠偏）+ 客户端 `predict_rollback_enabled`
  开关 + HUD「rollback:on/off」按钮。回滚=跳变回权威；不回滚=多帧插值渐进纠偏（《帧同步手感优化》两种方案）。
- **参考文献**：`docs/208_sync-references.md` 收录 12 篇，全文已去链接、去网易化、去人名化。
- **网络分级预设**：`fault.py` 加 `NETWORK_PRESETS`（极好/良/一般/差）+ console `preset <name>` 命令。
- **Throttle/Cap 带宽限速**：`fault.py` 加 `throttle_kbps` 参数（0=不限速）。
- **延迟埋点**：`net/latency_probe.py`（各段耗时统计，可开关，默认关）。
- **冗余发包 DUP**：`net/redundancy.py`（1 原始 + N 冗余，接收去重，可开关）。
- **时钟同步**：`net/clock_sync.py`（NTP 式对时：RTT/2 算偏移，中位数过滤异常）。
- **自动化批量跑测**：`scripts/benchmark_sync.py`（遍历弱网×冗余统计 desync 率矩阵）。
- **客户端碰撞上报 + 服务端校验**：`MSG_HIT_REPORT` + server `_on_hit_report`（回退历史世界校验宣称命中）。
- **渲染帧跳帧**：客户端 `render_skip` 参数（渲染跳帧，逻辑帧照推，逻辑/渲染分离）。
- **断线重连**：客户端 `session_token` 身份标识 + 服务器 `disconnect_grace`（10s 宽限，重连恢复身份+状态，超时才真移除）。
- **分布式授权**：`MSG_CLAIM` 标记争夺 + `owner_id`（谁持标记谁主控），HUD「claim」按钮，解决 p2p 无法验证。
- **二进制协议 + Property/RPC 分离**：`protocol.py` 改用 struct 二进制 TLV 编码（替换 JSON，体积更小），
  并区分 Property（属性同步，snapshot）与 RPC（调用同步，其余），`is_property`/`is_rpc` 判断。
- **内插↔外插切换开关**：客户端 `interp_extrap_mode`（auto/interp/extrap 三档）+ HUD 循环切换按钮。
- **UI 中文化 + 分档 + 开关集中**：HUD 全部中文（微软雅黑字体）；网速改「极好/好/一般/差」四档一键切
  （替代 6 参数自定义）；等帧追帧/预测回滚/内插外插/渲染跳帧/争夺标记/容差 全部集中到 HUD 面板。
- **UE GAS 风格技能系统重构**：`core/ability.py` 独立 Ability 对象（MeleeAttack/Dash/Freeze），
  激活→检测命中→结算伤害→结束 的状态机驱动；`World.active_abilities` 挂技能，随快照/hash 同步；
  修复了技能「无表现无伤害」的 bug（原 as_dict 漏字段 + lockstep 预推演 tick 重复 + 散乱 timer 字段）。
- **技能输入改事件驱动 + RPC**：客户端键盘改用 `KEYDOWN/KEYUP` 事件维护 `_held_keys`（修复
  `get_pressed` 的 keycode↔scancode 混淆导致方向键/Shift 永久失效）；攻击/冲刺/冰冻改为「按键
  上升沿」发 `MSG_ATTACK`/`MSG_DASH`/`MSG_FREEZE` RPC（走 TCP 可靠通道），服务器 `_on_skill`
  立即激活 Ability——修掉「瞬时技能被每帧 input 覆盖吞掉」导致按技能无反应的问题。
- **GUI 测试工具**：`scripts/gui_e2e_test.py`（真实 GUI 按键 → 三技能验证）、
  `scripts/gui_screenshot_test.py`（真实窗口 + 截图）、`scripts/min_key_test.py`（最小键盘测试）。
- **窗口焦点问题修复**：`_grab_focus()` 启动/点击时主动抢键盘焦点（Windows win32 API）；
  监听 `WINDOWFOCUSGAINED/LOST`，失焦时清空按键状态；HUD 显示「键盘 有焦点/无焦点」。
  根因：pygame 窗口创建后焦点可能仍在终端，KEYDOWN 全被终端吃掉，表现为「按什么都没反应」。
- **键盘输入链修复（多个叠加 bug）**：① `get_pressed` 的 keycode↔scancode 混淆 → 改
  `KEYDOWN/KEYUP` 事件维护 `_held_keys`；② **SDL 文本输入(IME)拦截字母键** → 启动时
  `pygame.key.stop_text_input()`；③ `event.pump()` 在事件循环中间调用吞事件 → 移到循环最前；
  ④ 技能被每帧 input 覆盖吞掉 → 改「上升沿」RPC（`MSG_ATTACK`/`MSG_DASH`/`MSG_FREEZE`）；
  ⑤ 冲刺拖尾永远画左边 → `Dash.activate` 同步 `caster.dash_dir`。HUD 增加「按键/键盘/最近键」诊断。
- 研究演示：`scripts/desync_demo.py` · `scripts/desync_socket_demo.py` · `scripts/prediction_demo.py` · `scripts/lagcomp_side_effect_demo.py` · `scripts/benchmark_sync.py`。
- 网络层 `net/`：`protocol` · `server` · `client` · `fault` · `kcp` · `transport` · `prediction` · `smoothing` · `timewarp` · `logic_clock` · `jitter_buffer` · `movement_predict` · `correction` · `latency_probe` · `redundancy` · `replay` · `clock_sync`。
- 核心层 `core/`：`game` · `sim` · `sync_diff` · `ability`（UE GAS 风格技能）。
- 入口 `sync_demo/server.py` / `client.py`，`SYNC_DEMO_ROLE` 区分。
- 测试：`tests/`（134 项）。

## 进行中

- （无）

## 待做

- 双网卡双通道：**用户决定不做**（跳出范围）。
- 所有代码、文档改动均**未 git commit**（按硬规矩等用户批准）。
