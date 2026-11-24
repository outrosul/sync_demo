# 验证与命令

## 跑起来

```bash
pip install -r requirements.txt   # pygame + pytest

# 服务器（Linux 部署；console 支持 mode lockstep / mode state 热切换）
SYNC_DEMO_ROLE=server python -m sync_demo.server

# 客户端（可开多个，进入同一张地图；HOST/PORT 环境变量可指定服务器）
SYNC_DEMO_ROLE=client python -m sync_demo.client
```

## 测试

```bash
python -m pytest tests/ -q
```

判据看末行成功标记；**不写死条数**。

## 环境变量

| 变量 | 取值 | 作用 |
| --- | --- | --- |
| `SYNC_DEMO_ROLE` | `server` / `client` | 区分身份 |
| `SYNC_DEMO_HOST` | IP | 客户端连的服务器地址，默认 127.0.0.1 |
| `SYNC_DEMO_PORT` | 整数 | 端口，默认 7808 |

## 怎么排错

先变红（复现成失败测试）再修；修在根因层（架构→类型→lint→测试→文档）；
修完做注入实验（把 bug 放回去测试必须变红，撤回必须回绿）。
