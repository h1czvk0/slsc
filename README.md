# SLSC

这是一个本地日志辅助工具。它通过读取本机日志，显示本局物品、位置、进度、房间统计，并提供中文翻译、图片参考和名单覆盖等功能。

当前版本：`3.9.28`

## 主要功能

- 实时追踪本地日志并绑定对应的 VRChat 进程，识别本局开始、结束和数据加载；VRChat 多开且无法自动判断时，可在界面选择正在游玩的账号。
- 自动识别不同地图并切换对应数据面板，也可手动固定显示任一面板。
- 支持职业、Boss 阶段、伤害结算、DPS、受伤来源和击败数量统计。
- 提供可手动开关的透明置顶桌面 HUD，使用 Windows 逐像素 Alpha 合成和纯白文字，背景透明度支持 `0%` 至 `100%`，并可选择显示内容、拖动缩放保存布局及恢复默认设置。
- 支持通过 VRChat Chatbox OSC 输出 Boss 当前锁定玩家，仅在 Boss 战期间显示，可选择完整提示或仅输出玩家名，可配置主机和端口，并兼容包含特殊 Unicode 字符的玩家名称；Boss 战结束或关闭输出时立即清除 Chatbox 文字。
- 支持 VRChat OSC 自动连跳；启用后仅在 Ecliptica 世界的非幕间阶段、VRChat 位于前台且按住空格时持续触发跳跃，幕间或按住任一 Windows 键时会自动暂停，并完整透传 `Win + 空格` 等系统快捷键。
- 同局实时伤害只显示仍在线且仍处于当前对局的玩家；玩家离开世界、结束对局或目标 VRChat 进程退出时会立即断开同步，异常断网则由服务端心跳超时清理。
- 启动时从最新日志尾部恢复当前对局，避免从旧日志头部回放造成 CPU 占用过高。
- 显示地图物品、玩家物品、中文名称、位置、锁房状态和颜色标记。
- 显示关键进度状态，以及本局资源和物品刷新统计。
- 支持本地翻译缓存、在线翻译更新、图片参考和图片资源更新。
- 支持赞助者名单覆盖，可在 `mitmdump` 系统代理模式和 `hosts + Caddy` 模式之间切换，并在停止时恢复对应网络设置。
- 支持单 exe 后台自动更新：有新版本时显示更新状态并自动下载替换。

## 下载和使用

从 GitHub Releases 下载最新版本后直接运行即可，不需要安装。

建议先启动 VRChat，再启动本工具。程序会把日志启动时间与 VRChat 进程启动时间对应起来，并优先锁定正在 Ecliptica 中的实例。若多个账号同时在 Ecliptica 且无法可靠判断，程序不会猜测账号，而会提示在“同局伤害同步”区域手动选择；选定的进程退出后会自动停止读取该日志。

## 自动更新

程序启动后会在后台检查 GitHub Releases 最新版本。无更新时不会显示更新区域；发现新版本后会在界面显示状态、自动下载新 exe，并在下载完成后使用临时 updater 替换原路径程序。

更新请求会优先使用 GitHub 镜像地址，全部失败后再访问 GitHub 原始地址。

## 开发环境

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python 主程序脚本.py
```

也可以不激活虚拟环境，直接使用：

```powershell
.\.venv\Scripts\python.exe 主程序脚本.py
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 打包

```powershell
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm 打包配置.spec
```

打包完成后主程序位于：

```text
dist
```

## GitHub Releases 发布

仓库只通过 `.github/workflows/release.yml` 的手动 `workflow_dispatch` 发布版本，不会因普通提交、推送或 Tag 自动发布。只有明确决定发布到 Releases 时，才输入 `RELEASE` 触发工作流；工作流会校验版本、运行测试、构建 EXE、核对附件摘要，然后发布为 Latest Release。

## 注意事项

- 本工具只依赖本地日志解析，不修改目标程序文件。
- 赞助者名单覆盖功能会临时修改系统代理，停止覆盖后会尝试恢复原代理配置。
- 开发环境运行主程序脚本时只检查和显示更新，不执行 exe 自替换。
- 若 GitHub Release 不存在或网络不可用，自动更新会静默失败或只记录日志，不影响主要日志监听功能。
