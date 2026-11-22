# 项目维护说明

## 日志与 UI

`log_stream.py` 按字节保留未完成的行，收到换行后才解码。启动恢复返回实际读取到的最后一条完整行的字节偏移，实时监听从该位置继续，避免扫描期间追加的内容被跳过。支持 VRChat 的 LF 和 CRLF 日志。

主监听每轮最多读取 1 MiB。待处理事件达到 2000 条或主线程回调达到 20 个时暂停读取，待处理内容保留在磁盘。界面按时间预算处理事件；诊断日志每轮最多合并 200 条，只保留最近 2000 行。

`vrchat_log_selector.py` 仅解析身份、世界和会话相关事件；日志文件名时间戳使用最多 4096 项的缓存。日志追加超过单次读取上限时，后续轮次继续处理，不跳过中间的世界切换事件。

`atomic_file.py` 为配置和下载资源提供同目录临时写入与原子替换。写入或替换失败时原文件保留，临时文件清理。

## 代码结构

`slashco.py` 只保留主窗口、SlashCo 面板和日志调度；悬浮 HUD（常量、布局归一化、逐像素 Alpha 渲染、`EclipticaDesktopHud`）位于 `ecliptica_hud.py`，测试和基准直接从该模块导入。主程序使用显式的 tkinter 导入，新增控件时需同步补充导入列表。

物品位置更新按清洗后的位置名缓存翻译结果，翻译表更新时由 `compile_wildcard_patterns` 清空缓存；表格行以物品 ID 作为行 ID，位置变化直接更新对应单元格，不再遍历整棵树。

`sponsor_mitm.py` 自行选择代理端口（跳过常见加速器端口和上游代理端口），界面层不再预选端口。`admin_helper.py` 只保留 `mitm_ca_trust`、`sponsor_hosts_add`、`sponsor_hosts_remove` 三种提权模式。

## 自动更新

`SlashCoSense.exe` 带有离线运行文件。启动器从 `%LOCALAPPDATA%\SlashCoMonitor\portable` 选择最新的完整版本，首次运行时从 EXE 展开。主程序、运行库、mitmdump 和 Caddy 分别打包，`tools/build_portable.py` 为每个组件生成固定的 ZIP 元数据，便于按 SHA-256 复用内容相同的组件。

主程序在后台检查更新，校验清单和组件后在缓存内组装新版。组装完成才会将新版目录切换为可启动状态。主程序正常清理退出后，启动器重启最新版。缓存保留最近两版，仍在运行的更旧版本会在后续启动时再清理。

从旧单文件版迁移时，旧版更新脚本会等待程序退出并原地覆盖为新版启动器。`slashco_updater.py` 保留全量 EXE 下载和替换逻辑，兼容旧打包入口。

## 依赖与构建

- Python 3.12；所有 Python 命令使用项目 `.venv`。
- `requirements.txt` 固定已验证的直接运行依赖版本；`requirements-build.txt` 增加 PyInstaller。传递依赖仍由 pip 解析。
- `requirements-test.txt` 增加 mitmproxy，供生成的 addon 集成测试使用；CI 和发布测试显式安装它，主程序和打包产物仍不依赖该 Python 包。
- `tools/mitmdump.exe` 和 `tools/caddy/caddy.exe` 是运行必需的独立程序；主 EXE 不再重复收集 mitmproxy Python 包。
- `tools/check_repository.py` 验证 Git 文件清单、Python 和 spec 语法、版本、打包资源、图片映射。
- CI 只执行检查；Release 工作流继续使用手动确认。发布必须遵循根目录 `AGENTS.md` 的升版和跟踪流程。

## 仓库清理原则

2026-11-07 清理了未调用、未打包的 `mitmproxy.exe`、`mitmweb.exe` 和旧 `_sounddevice_data`，以及硬编码旧路径的生成 addon、Caddyfile、PAC、临时名单和信任标记。

2026-11-16 移除了无调用的代码：旧画廊窗口、电池“安装中”状态机、界面层重复的代理端口预选、`admin_helper.py` 的 DNS 与旧 hosts 模式，以及主 EXE 中未被调用的 `tools/reset_network.py` 打包项（脚本本身保留，供 `tools/fix_network.bat` 手动恢复网络使用）。源码、文档和 JSON 的行尾统一为 LF。

同日将两个独立程序移出本仓库：`sponsor_standalone.py`（独立版名单覆盖，已有单独的 `slashco-sponsor` 项目）直接删除；`ImageTool.py`（位置图片与翻译管理工具）迁到同级目录 `slashco-image-tool`，默认读写本项目的 `img_assets/`、`images.json` 和 `locations.json`，也可用命令行参数或环境变量 `SLASHCO_PROJECT_DIR` 指定项目目录。

`sponsor_config.json`、`untranslated_locations.txt` 从 Git 跟踪中移除，本地副本保留。522 张图片全部被映射引用，完整保留。实施计划移入 `docs/plans/`。

Git 历史保留，删除文件通过普通提交记录。历史版本中的大文件仍在完整克隆中；此次不重写提交历史。日常维护可使用 `git gc --no-prune`，不需要手动删除 `.git` 内容。

本机缓存和构建中间文件可以重建。保留 `.venv`、最近一次验证的 `dist/release/SlashCoSense.exe` 和用户运行数据。

## 验证与性能测量

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe tools\check_repository.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tools\benchmark_runtime.py --output benchmark-results.json
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm slashco.spec
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm slashco_launcher.spec
.\.venv\Scripts\python.exe tools\build_portable.py
```

基准默认构造 20003 行日志，并匹配 1440 个日志文件名，另外检查 HUD 重复绘制和结算表更新次数。输出各场景的中位耗时；`--log` 可切换为实际日志。合成基准结果用于比较对应代码路径，不能视为整个游戏场景的 CPU 占用降幅。

2026-11-07 本机 Python 3.12.9 对比优化前提交 `0f276d7`，每个场景运行 7 次取中位数：

| 场景 | 优化前 | 优化后 | 耗时降低 |
| --- | ---: | ---: | ---: |
| 20003 行日志的首次实例识别 | 69.320 ms | 12.593 ms | 81.8% |
| 1440 个日志文件匹配进程（缓存已预热） | 11.775 ms | 3.410 ms | 71.0% |

本机原有 EXE 为 70,564,582 字节，优化后的验证构建约 59.88 MB（57.1 MiB）；旧构建环境未完全锁定，因此包体对比仅代表本机产物。停止跟踪的文件合计 58,935,346 字节（约 56.2 MiB），历史对象另行保留。
