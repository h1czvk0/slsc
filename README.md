# SLSC

这是一个本地日志辅助工具。它通过读取本机日志，显示本局物品、位置、进度、房间统计，并提供中文翻译、图片参考和名单覆盖等功能。

当前版本：`3.8.2`

## 主要功能

- 实时追踪最新本地日志，识别本局开始、结束和数据加载。
- 自动识别 SlashCo 与 Ecliptica 地图并切换对应数据面板，也可手动固定显示任一面板。
- 支持 Ecliptica 职业、Boss 阶段、伤害结算、DPS、受伤来源和击败数量统计。
- 提供可手动开关的 Ecliptica 透明置顶桌面 HUD，显示伤害数据和 Boss 当前锁定推测。
- 启动时从最新日志尾部恢复当前对局，避免从旧日志头部回放造成 CPU 占用过高。
- 显示地图物品、玩家物品、中文名称、位置、锁房状态和颜色标记。
- 显示关键进度状态，以及本局资源和物品刷新统计。
- 支持本地翻译缓存、在线翻译更新、图片参考和图片资源更新。
- 支持赞助者名单覆盖，可在 `mitmdump` 系统代理模式和 `hosts + Caddy` 模式之间切换，并在停止时恢复对应网络设置。
- 支持单 exe 后台自动更新：有新版本时显示更新状态并自动下载替换。

## 下载和使用

从 GitHub Releases 下载最新版本后直接运行即可，不需要安装。

建议先启动目标程序，再启动本工具。程序会自动锁定最新日志并监听后续新增内容。

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

## 注意事项

- 本工具只依赖本地日志解析，不修改目标程序文件。
- 赞助者名单覆盖功能会临时修改系统代理，停止覆盖后会尝试恢复原代理配置。
- 开发环境运行主程序脚本时只检查和显示更新，不执行 exe 自替换。
- 若 GitHub Release 不存在或网络不可用，自动更新会静默失败或只记录日志，不影响主要日志监听功能。
