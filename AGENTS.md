# sync_demo —— 联机实时对战 demo（帧同步 / 状态同步教学）

**一句话目标**：用**纯 Python（无引擎，仅 pygame 做画面）**，写一个 2D 横板联机对战 demo，
同一份代码既做**服务器**也做**客户端**，靠环境变量区分身份；演示**帧同步（lockstep）**与
**状态同步（state sync）**两种联网架构，并能在**服务器 console 后台运行时切换**二者，
供学习「同步原理 + gameplay 实现」用。

## 文档入口

| 段 | 类别 | 读哪份 |
| --- | --- | --- |
| 001–099 | **总纲** | **`docs/001_open-questions.md`**（未定项）· **`docs/002_status.md`**（进度 / 待做） |
| 201–299 | **实现规范** | 总：**`docs/201_architecture.md`**（架构 / 分层 / 命名）· **`docs/208_sync-references.md`**（同步参考文献） |
| 301–399 | **流程与台账** | 总：**`docs/301_workflow.md`**（怎么跑 / 命令）→ 分：`docs/302_changelog.md` · `docs/303_decisions.md` · **`docs/304_backlog.md`**（看板）· **`docs/305_pipeline.md`**（管线 / DoD）· `docs/306_pitfalls.md` · **`docs/307_conventions.md`**（工程约定） |

## 硬规矩（守卫真会拦；细节见 `docs/307_conventions.md`）

- **代码风格**：禁 `try/except` 包业务逻辑 · 禁 `lambda` · import 只在文件顶部。
- **注释与 docstring 一律中文**；说明贴在它描述的代码旁边。
- **命名**：全 ASCII 标识符（变量 `snake_case` / 常量 `UPPER_SNAKE` / 文件 `snake_case`）。
- **分层**：逻辑层（纯数据与规则，不碰渲染/网络）与表现层/网络层解耦；逻辑层可脱离画面在测试里跑。
- **一个模块一个子目录**；根只放入口（`AGENTS.md` / `README.md` / 运行脚本 / 依赖清单 / 点文件）。
- **改文件用部分替换，禁止全盘重写**（新建文件才允许一次写全）。
- **`git commit` / `git push` 先经用户批准**。
- **改动就要同步文档**，同一轮改完（尤其「本 demo 现状 / ✅❌」标注，做完功能就同步改，不留过时状态）。
- **文档去链接 / 去网易化 / 去人名化**，且不留「（已去…仅留标题）」「本轮」「待补」这类处理性备注。
- **读大文档先 `grep` 定位再窗口读**；`AGENTS.md ≤ 120 行`（它是每条消息都重发的路由器）。

## 最快上手

```bash
pip install -r requirements.txt
python -m pytest tests/ -q          # 工具链、逻辑层、守卫
# 起服务器（Linux）：
SYNC_DEMO_ROLE=server python -m sync_demo.server
# 起客户端（可开多个，进入同一张地图）：
SYNC_DEMO_ROLE=client python -m sync_demo.client
```

详见 `docs/301_workflow.md`。
