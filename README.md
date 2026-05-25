# SlashCoSense

SlashCoSense 是面向 VRChat 地图 SlashCo 的中文实时辅助工具。它通过读取 VRChat 本地日志，显示本局物品、位置、发电机燃油和电池进度、封锁房间统计，并提供中文翻译、图片参考和赞助者名单覆盖等功能。

当前版本：`3.6.1`

## 主要功能

- 实时追踪最新 VRChat 日志，识别 SlashCo 对局开始、结束和地图数据加载。
- 启动时从最新日志尾部恢复当前对局，避免从旧日志头部回放造成 CPU 占用过高。
- 显示地图物品、玩家物品、中文名称、位置、锁房状态和颜色标记。
- 显示发电机燃油、电池状态，以及本局油桶和物品刷新统计。
- 支持本地翻译缓存、在线翻译更新、图片参考和图片资源更新。
- 支持赞助者名单覆盖，可在启动和停止时恢复系统代理设置。
- 支持单 exe 后台自动更新：有新版本时显示更新状态并自动下载替换。

## 下载和使用

从 GitHub Releases 下载 `SlashCoSense.exe` 后直接运行即可，不需要安装。

程序会读取当前用户的 VRChat 日志目录：

```text
%USERPROFILE%\AppData\LocalLow\VRChat\VRChat
```

建议先启动 VRChat，再启动 SlashCoSense。程序会自动锁定最新的 `output_log_*.txt` 并监听后续新增日志。

## 自动更新

程序启动后会在后台检查 GitHub Releases 最新版本。无更新时不会显示更新区域；发现新版本后会在界面显示状态、自动下载新 exe，并在下载完成后使用临时 updater 替换原路径程序。

更新请求会优先使用 GitHub 镜像地址，全部失败后再访问 GitHub 原始地址。

## 开发环境

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python slashco.py
```

也可以不激活虚拟环境，直接使用：

```powershell
.\.venv\Scripts\python.exe slashco.py
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 打包

```powershell
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm slashco.spec
```

打包完成后主程序位于：

```text
dist\SlashCoSense.exe
```

发布到 GitHub Releases 时，建议资产名称固定为 `SlashCoSense.exe`，或使用 `SlashCoSense_v版本.exe`。

## 注意事项

- SlashCoSense 只依赖本地日志解析，不修改 VRChat 客户端文件。
- 赞助者名单覆盖功能会临时修改系统代理，停止覆盖后会尝试恢复原代理配置。
- 开发环境运行 `python slashco.py` 时只检查和显示更新，不执行 exe 自替换。
- 若 GitHub Release 不存在或网络不可用，自动更新会静默失败或只记录日志，不影响主要日志监听功能。
