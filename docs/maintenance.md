# 项目维护说明

## 日志与 UI

`log_stream.py` 按字节保留未完成的行，收到换行后才解码。启动恢复返回实际读取到的最后一条完整行的字节偏移，实时监听从该位置继续，避免扫描期间追加的内容被跳过。支持 VRChat 的 LF 和 CRLF 日志。

主监听每轮最多读取 1 MiB。待处理事件达到 2000 条或主线程回调达到 20 个时暂停读取，待处理内容保留在磁盘。界面按时间预算处理事件；诊断日志每轮最多合并 200 条，只保留最近 2000 行。

`vrchat_log_selector.py` 仅解析身份、世界和会话相关事件；日志文件名时间戳使用最多 4096 项的缓存。日志追加超过单次读取上限时，后续轮次继续处理，不跳过中间的世界切换事件。

`atomic_file.py` 为配置和下载资源提供同目录临时写入与原子替换。写入或替换失败时原文件保留，临时文件清理。

## 依赖与构建

- Python 3.12；所有 Python 命令使用项目 `.venv`。
- `requirements.txt` 固定已验证的直接运行依赖版本；`requirements-build.txt` 增加 PyInstaller。传递依赖仍由 pip 解析。
- `requirements-test.txt` 增加 mitmproxy，供生成的 addon 集成测试使用；CI 和发布测试显式安装它，主程序和打包产物仍不依赖该 Python 包。
- `tools/mitmdump.exe` 和 `tools/caddy/caddy.exe` 是运行必需的独立程序；主 EXE 不再重复收集 mitmproxy Python 包。
- `tools/check_repository.py` 验证 Git 文件清单、Python 和 spec 语法、版本、打包资源、图片映射。
- CI 只执行检查；Release 工作流继续使用手动确认。发布必须遵循根目录 `AGENTS.md` 的升版和跟踪流程。

## 仓库清理原则

2026-11-07 清理了未调用、未打包的 `mitmproxy.exe`、`mitmweb.exe` 和旧 `_sounddevice_data`，以及硬编码旧路径的生成 addon、Caddyfile、PAC、临时名单和信任标记。

`sponsor_config.json`、`untranslated_locations.txt` 从 Git 跟踪中移除，本地副本保留。522 张图片全部被映射引用，完整保留。实施计划移入 `docs/plans/`。

Git 历史保留，删除文件通过普通提交记录。历史版本中的大文件仍在完整克隆中；此次不重写提交历史。日常维护可使用 `git gc --no-prune`，不需要手动删除 `.git` 内容。

本机缓存和构建中间文件可以重建；保留 `.venv` 和最近一次验证的 `dist/SlashCoSense.exe`，避免删除用户运行数据。

## 验证与性能测量

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe tools\check_repository.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tools\benchmark_runtime.py --output benchmark-results.json
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm slashco.spec
```

基准默认构造 20003 行日志，并匹配 1440 个日志文件名，另外检查 HUD 重复绘制和结算表更新次数。输出各场景的中位耗时；`--log` 可切换为实际日志。合成基准结果用于比较对应代码路径，不能视为整个游戏场景的 CPU 占用降幅。

2026-11-07 本机 Python 3.12.9 对比优化前提交 `0f276d7`，每个场景运行 7 次取中位数：

| 场景 | 优化前 | 优化后 | 耗时降低 |
| --- | ---: | ---: | ---: |
| 20003 行日志的首次实例识别 | 69.320 ms | 12.593 ms | 81.8% |
| 1440 个日志文件匹配进程（缓存已预热） | 11.775 ms | 3.410 ms | 71.0% |

本机原有 EXE 为 70,564,582 字节，优化后的验证构建约 59.88 MB（57.1 MiB）；旧构建环境未完全锁定，因此包体对比仅代表本机产物。停止跟踪的文件合计 58,935,346 字节（约 56.2 MiB），历史对象另行保留。
