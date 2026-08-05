# Ecliptica 实时伤害同步服务

该服务与桌面客户端协议版本 1 配套，提供：

- 按日志中的 `session_id` 隔离 WebSocket 房间；
- 每局最多 4 个不同的 VRChat 用户 ID；
- 收到伤害变化后立即广播，客户端上传频率上限为 100ms 一次；
- PostgreSQL 每 2 秒异步保存一次检查点，断线或房间结束时保存最终状态；
- 按 VRC 用户名搜索玩家，以及查看玩家每局历史和单局详情。

## Ubuntu 部署

安装 Docker Engine、Docker Compose 插件和 Caddy，然后执行：

```bash
cd server
cp .env.example .env
# 修改 .env 中的两个密码/密钥
docker compose up -d --build
```

把 `Caddyfile.example` 中的域名替换为真实域名并放入 Caddy 配置，确认 DNS 指向服务器，然后重新加载 Caddy。应用容器只监听 `127.0.0.1:8000`，公网只需开放 80/443。

客户端服务器地址填写：

```text
wss://sync.example.com/ws?token=<SYNC_API_KEY>
```

生产环境必须使用 `wss://`。`SYNC_API_KEY` 为空时服务允许任意客户端写入，仅建议本机开发时这样做。

## 查询接口

```text
GET /health
GET /api/players/search?username=Alice
GET /api/players/usr_xxx/sessions?limit=50&offset=0
GET /api/sessions/<数据库中的 game_session_id>
```

历史查询目前公开，不包含隐私开关，符合当前需求。玩家主身份使用日志中的 `usr_xxx`，VRC 用户名用于显示和搜索；名称变化会保留在 `player_names` 表中。

## 数据与房间生命周期

- 相同 `session_id` 的连接进入同一房间，不同 ID 不会互相广播。
- 同一 `usr_xxx` 重连会替换旧连接，不占用额外名额。
- 最后一个玩家断开 5 分钟后，本局标记为完成。
- 服务重启后，短时间内重连仍复用原数据库对局；超过两倍空房超时时间的遗留对局标记为中断。
- 客户端发送累计伤害和递增序号，服务拒绝同一连接上的旧序号。

## 本地测试

在仓库根目录已有虚拟环境时：

```powershell
.venv\Scripts\python.exe -m pip install -r server\requirements-dev.txt
.venv\Scripts\python.exe -m unittest server.tests.test_rooms server.tests.test_api
```
