# 仓库协作约定

## 推送与发布

- 用户要求「推送」时，按一次版本发布处理。默认将当前版本的补丁号加 1，用户指定版本时采用指定值。同一次发布已完成升版时沿用该版本。
- 同步更新 `slashco_updater.py` 的 `APP_VERSION`、`README.md` 的当前版本，以及 `CHANGELOG.md` 对应版本的变更说明。检查版本一致性和测试结果后提交并推送。
- 推送成功后，在目标仓库和分支触发 `.github/workflows/release.yml`。本仓库默认使用 `gh workflow run release.yml --repo h1czvk0/slsc --ref main -f confirm=RELEASE`。
- 跟踪本次 GitHub Actions 运行，核对运行对应的提交、发布结果和 `SlashCoSense.exe` 附件。向用户报告版本、工作流链接与发布结果。
- 用户单独要求触发 GitHub Actions 时，使用目标分支上已推送的版本。
