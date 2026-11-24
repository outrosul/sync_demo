# 改动历史

| 日期 | 改动 |
| --- | --- |
| 2026-11-23 | 修复帧同步「完全不同步」：① 客户端锁步改「本地预推演 + frame 回滚重放」（步长统一为 LOCKSTEP_STEP=2，修复 tick 差 2 错位）；② 服务器在玩家加入/离开时补发快照同步玩家集合（修复老客户端缺新玩家实体的永久不同步）；③ HUD 按钮/调试信息 mode 显示名 lockstep→frame。 |
| 2026-11-23 | 初始化仓库骨架：`AGENTS.md` + `docs/`（通用 agent 说明自 D:\nvdi 抽取）。 |
| 2026-11-23 | 双通道传输接线：`transport.py` 接入 `server.py`/`client.py`，重要走 TCP、非重要走 KCP/UDP；新增 `tests/test_dual_channel.py`。 |
