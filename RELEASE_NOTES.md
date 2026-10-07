# svault 1.1.0

- 内置正式主库/项目模板，仅凭 exe 即可从零离线生成完整两级科研 Vault，不生成 Python 脚本或虚构研究记录。
- 新增 init、profile inspect、blueprint list/generate 与 dry-run；本地源库配置由用户明确选择，插件程序继承，data.json、密钥和状态不复制。
- 新增 vault get/list/register，稳定主库身份与父库引用；兼容 1.0.0，URI 使用实际主库注册名。
- 新增 doctor/repair，补缺与派生清单修复要求预览计划哈希；不替换已有内容、规则或设置。
- 新增 agents diff/sync，核对差异后生成当前库规则，保留本库补充与旧版本。
- 离线项目创建不再要求主库安装或授权同步。
- 保留增删改查列、.trash 恢复、哈希保护、审计与覆盖式升级；Windows 文件版本 1.1.0.0，CLI/包版本 1.1.0。

本版不包含项目改名/移动、整库备份、模板包导入导出或结构迁移。Windows x64 exe 与 SHA256SUMS.txt 随 Release 提供。
