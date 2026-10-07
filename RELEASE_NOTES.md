# svault 1.0.0

正式发布两级 Obsidian 科研 Vault 管理 CLI。

- 笔记增删改查列，按主库/项目子库工作目录限制操作范围。
- 删除移入 .trash，支持恢复；修改保留旧版本，支持冲突保护与回滚。
- 项目创建、状态与主页维护，笔记/结构模板、两级 AGENTS 生成，索引及同步目录维护。
- 新增 `version` 和 `--version`，版本号统一为 1.0.0，Windows 文件属性为 1.0.0.0。
- 新增 `update check/apply/status`，从本仓库公开 Release 获取 exe，校验大小、SHA256、产品与版本，退出后覆盖升级；失败恢复旧版，成功保留备份。
- Vault 只部署 exe，Python 开发源码和测试留在独立代码仓库。

Windows x64 资产：`svault-windows-x64.exe`。校验值：`SHA256SUMS.txt`。
CLI 不调用 AI、不运行训练、不管理外部实验仓库。权限检查是命令层约束，不替代系统权限。
macOS/Linux 原生二进制不包含在本次 Release。
