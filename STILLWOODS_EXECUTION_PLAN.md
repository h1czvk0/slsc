# Stillwoods Execution 辅助工具实施计划

## 结论摘要

`Stillwoods Execution` 可以做一个类似 SlashCoSense 的日志辅助工具，但能力边界不同。

当前日志能稳定获得的信息包括：世界识别、玩家加入/离开、当前玩家数、是否房主、对局开始、成功逃脱、对局重置、玩家进入车辆座位、本地玩家拾取/丢弃物品、远程 Patreon 支持者名单。

当前日志暂时不能稳定获得的信息包括：全图物品刷新位置、所有玩家拾取了什么、精确任务进度、物品实时位置、失败原因、实时座位离开状态。

## 赞助者名单与 signature

Stillwoods 会通过 `VRCStringDownloader` 加载远程 JSON：

```text
https://gist.githubusercontent.com/DISTROYER190/8f78b0adc79793d4ae18bf1bda4663cb/raw?w
```

JSON 结构：

```json
{
  "data": {
    "玩家名": 0,
    "玩家名": 1,
    "玩家名": 2,
    "玩家名": 3
  },
  "signature": 1118964351
}
```

根据世界内文字 `Patreon supporters`、`Mega Supporters` 和 JSON 中玩家名对应关系，可以判断这是 Patreon/赞助者名单。

`signature` 不是固定值。用户提供的历史内容中为 `2962698415`，当前实际拉取内容为 `1118964351`，说明它会随名单内容或版本变化。

已尝试的常见算法未匹配当前 `signature`：

- `crc32`
- `adler32`
- `fnv1a`
- `djb2`
- `sdbm`
- `jenkins`

测试对象包括原始 JSON、去掉 `signature` 后的 JSON、仅 `data`、排序后的 `data`、按行拼接的 `name:value`。均未匹配。

因此第一版不依赖生成 `signature`，只把它当作远程名单的版本/变更标记：

- 下载名单后记录 `signature`。
- 如果 `signature` 变化，刷新本地缓存。
- 如果下载失败，继续使用本地缓存。
- 不尝试伪造、覆盖或修改远程名单。

后续如果需要确认算法，可以继续收集多个 Gist 历史 revision 的 `{data, signature}` 样本，再做拟合分析。

## 目标

做一个独立的 Stillwoods 辅助模式，复用 SlashCoSense 已有架构：

- Python/Tkinter。
- 单 exe 打包。
- 启动只尾部扫描最新 VRChat 日志。
- 实时监听新增日志。
- 不阻塞 UI。
- 所有缓存和运行产物放到 `%LOCALAPPDATA%` 下。

可以选择两种形态：

- 方案 A：在现有 SlashCoSense 中新增世界模式，检测到 `Stillwoods Execution` 后切换到 Stillwoods 面板。
- 方案 B：单独做 `StillwoodsSense.exe`，复用日志监听、更新器、运行目录、UI 组件。

建议先做方案 A。原因是日志监听、自动更新、单 exe 打包、缓存目录、UI 基础能力已经存在，改动更小。

## 可解析字段

### 世界识别

匹配：

```text
Entering Room: Stillwoods Execution
Joining or Creating Room: Stillwoods Execution
Destination set: wrld_0153e19d-9447-4365-8c69-7559fbc4e1a8...
```

输出：

- 当前是否在 Stillwoods。
- 世界 ID。
- 实例类型、区域、group ID。
- 进入时间。

### 玩家列表

匹配：

```text
OnPlayerJoined name (usr_xxx)
OnPlayerLeft name (usr_xxx)
Initialized PlayerAPI "name" is local
Initialized PlayerAPI "name" is remote
```

输出：

- 当前玩家列表。
- 玩家数量。
- 本地玩家名。
- 玩家加入/离开时间。
- 历史最高同时在线人数。

### 房主状态

匹配：

```text
I am MASTER
I am *NOT* MASTER
```

输出：

- 当前是否房主。

### 对局状态

匹配：

```text
gorenests game start
Successful Escape
GameResetGaragedoor
failed start
```

建议规则：

- `gorenests game start`：对局开始。
- `Successful Escape`：成功逃脱。
- `GameResetGaragedoor`：重置/回合边界候选，成对出现时合并为一次事件。
- `failed start`：开始失败或启动条件不足，记录次数和时间。

输出：

- 当前是否对局中。
- 对局计时。
- 成功逃脱次数。
- 开始失败次数。
- 最近一次重置时间。

### 本地物品交互

匹配：

```text
Pickup object: 'object' equipped = ...
Drop object: 'object, was equipped = ...'
```

物品归类：

- `FuelCan*`：油桶。
- `CarBattery*`：电池。
- `Wheel*` / `GrabbableWheel*`：车轮。
- `Handle (*)`：把手。
- `Medkit*`：医疗包。
- `keycard (*)`：钥匙卡。
- `NotepadforBunker*`：地堡笔记。
- `remote`：遥控器。
- `map*`：地图。

输出：

- 本地见过/拿过的物品清单。
- 当前本地手持物品。
- 物品拾取/丢弃时间线。
- 每类物品计数。

限制：

- 只能代表本地玩家日志看到的交互。
- 不能表示全图刷新。
- 不能表示其他玩家手中物品。

### 车辆座位

匹配：

```text
[VRC Station][玩家名] Entered DriverSeat
[VRC Station][玩家名] Entered Passenger Seat
```

输出：

- 谁进入过驾驶位。
- 谁进入过乘客位。
- 最近座位进入时间。

限制：

- 当前日志没有稳定的离开座位字段。
- 第一版只做“进入记录”，不强行维护实时座位状态。

### 伤害与事件

匹配：

```text
Ouch
Carrion took damage!
Input Works!
toggleflashlightforDesktop
toggleflashlightforVR
```

建议：

- `Ouch`、`Carrion took damage!` 只记录为事件。
- `toggleflashlight*` 默认过滤，不显示在主 UI。
- `Input Works!` 默认过滤，放入调试视图。

### 赞助者名单

从 Gist 加载：

- 玩家名。
- 支持等级。
- signature。

UI 显示：

- 当前房间玩家是否在支持者名单中。
- 等级 1/2/3 的不同颜色或标签。
- 远程名单更新时间。
- 当前 signature。

建议等级命名先保持中性：

- `0`: Level 0
- `1`: Supporter
- `2`: Mega Supporter
- `3`: Top Supporter

等级文本后续根据世界内实际展示再修正。

## 架构设计

### 新增解析模块

新增：

```text
stillwoods_log_parser.py
```

职责：

- 只做纯日志解析。
- 输入单行日志。
- 输出结构化事件。
- 不依赖 Tkinter。
- 可单元测试。

事件类型建议：

```python
world_enter
world_leave
player_join
player_left
local_player
master_state
round_start
round_success
round_reset
round_failed_start
item_pickup
item_drop
station_enter
damage_event
sponsor_url_seen
```

### 新增状态模型

新增：

```text
stillwoods_state.py
```

职责：

- 维护当前世界状态。
- 维护玩家列表。
- 维护对局计时。
- 维护本地物品状态。
- 合并重复事件。
- 过滤无意义噪声。

### 新增赞助者名单模块

新增：

```text
stillwoods_supporters.py
```

职责：

- 后台下载 Gist JSON。
- 缓存到 `%LOCALAPPDATA%`。
- 校验 JSON 结构。
- 记录 `signature`。
- 下载失败时使用缓存。
- 提供 `get_supporter_level(player_name)`。

不做：

- 不修改远程名单。
- 不伪造 `signature`。
- 不接管系统代理。

### UI 集成

在主界面新增 Stillwoods 模式区域：

- 世界状态。
- 当前玩家数。
- 房主状态。
- 对局计时。
- 对局事件统计。
- 本地物品清单。
- 当前手持物品。
- 支持者名单命中。
- 车辆座位进入记录。

如果选择集成进 SlashCoSense：

- 当前世界是 SlashCo 时显示 SlashCo 面板。
- 当前世界是 Stillwoods 时显示 Stillwoods 面板。
- 其他世界时显示“等待支持的世界”。

## 启动恢复策略

沿用现有日志性能策略：

- 只选择最新 `output_log_*.txt`。
- 从尾部向前扫描，最多 20MB。
- 找到最近一次 `Entering Room: Stillwoods Execution` 后，只恢复该世界后的事件。
- 如果尾部没有 Stillwoods 活跃上下文，则 `seek(EOF)` 只监听后续新增日志。

恢复时只处理关键事件：

- world enter。
- player join/left。
- local player。
- master state。
- round start/reset/success。
- pickup/drop。
- station enter。

过滤：

- Avatar 下载。
- 图片下载。
- 纯数字调试。
- flashlight toggle。
- 大量 VRChat 客户端警告。

## 测试计划

### 解析器测试

新增：

```text
tests/test_stillwoods_log_parser.py
```

覆盖：

- 世界进入识别。
- Gist URL 识别。
- 玩家加入/离开。
- 本地/远程玩家识别。
- 房主状态识别。
- 对局开始/成功/重置/失败识别。
- 物品拾取/丢弃识别。
- 车辆座位识别。
- 非相关日志不产生事件。

### 状态测试

新增：

```text
tests/test_stillwoods_state.py
```

覆盖：

- 玩家计数增减。
- 重复加入/离开处理。
- 回合开始计时。
- 重置合并。
- 本地手持物品更新。
- 物品分类计数。
- 支持者等级匹配。

### 真实日志回归

使用：

```text
output_log_2026-06-04_22-00-08.txt
```

验证：

- 能识别两次进入 Stillwoods。
- 能识别 `gorenests game start`。
- 能识别 `Successful Escape`。
- 能识别 Gist 支持者名单 URL。
- 能统计本地交互物品。
- 不被 Avatar/图片下载日志拖慢。

## 里程碑

### Milestone 1：只读分析版

- 新增解析器。
- 新增测试。
- 命令行输出 Stillwoods 摘要。

目标：确认所有字段识别稳定。

### Milestone 2：UI 原型

- 在 Tkinter 中显示 Stillwoods 面板。
- 显示玩家数、回合计时、事件统计、本地物品。
- 支持日志实时监听。

目标：能边玩边看基础信息。

### Milestone 3：支持者名单

- 后台加载 Gist。
- 本地缓存。
- 显示当前房间玩家的支持者等级。
- signature 变化时刷新。

目标：复刻世界内 Patreon supporters 信息，但只做只读展示。

### Milestone 4：打包与发布

- 保持单 exe。
- 复用现有 PyInstaller 配置。
- 保证运行产物仍在 `%LOCALAPPDATA%`。
- 添加 README 说明。

## 风险与限制

- Stillwoods 没有像 SlashCo 一样输出全图物品位置，所以不能做完整地图物品清单。
- 当前只能追踪本地玩家拾取/丢弃，不能追踪其他玩家物品。
- `GameResetGaragedoor` 是否等价于每次重置，需要更多日志确认。
- `signature` 算法未知，但第一版不需要生成它。
- 远程 Gist 可能变更 URL，需要从日志中动态发现。

## 推荐实施顺序

1. 新增 `stillwoods_log_parser.py` 和解析器测试。
2. 用真实日志生成 Stillwoods 摘要报告。
3. 新增 `stillwoods_state.py`。
4. 接入主程序日志监听，但先隐藏 UI，只写系统日志。
5. 增加 Stillwoods UI 面板。
6. 增加赞助者名单只读下载与缓存。
7. 打包测试。

