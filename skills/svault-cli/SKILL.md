---
name: svault-cli
description: 使用 svault CLI 管理两级 Obsidian 科研 Vault，包括笔记增删改查列、项目与模板维护、AGENTS 生成、回收恢复、索引与同步目录、版本查询和 GitHub Release 升级。适用于 svault 或对应科研库维护请求；不负责运行实验、管理训练物料或开发实验交接服务。
---

# svault CLI

使用 svault 完成用户指定的笔记库维护，遵循当前主库及项目的 AGENTS。CLI 不调用 AI；理解、证据核对和笔记整理由 Agent 完成。

## 定位程序与操作身份

- 优先使用已安装的 `svault` 命令；Windows Vault 通常部署于主库 `工具/svault.exe`。
- 从当前目录向上查找主库标识（`.obsidian` 与 `规则与模板/科研库规则.md`）或按项目 AGENTS 的相对路径定位 exe。调用时保持工作目录在当前 Agent 的 Vault，不为获取主库权限切换目录。
- 若程序缺失，先报告并给出[官方 Release](https://github.com/SuShuheng/scientific-vault-cli/releases)入口；没有安装/升级请求时，不自动下载或替换程序。
- 先查询 `version`、`whoami`、`rules`；遇到不熟悉的操作，查询 `operations`、`permissions` 和对应命令的 `--help`。本 skill 的命令示例以 1.0.0 为基准，实际程序帮助优先。
- 普通操作使用选定库内的相对路径；全局参数 `--vault`、`--project` 放在子命令之前。库外人工运行才需要显式 `--vault`。

## 两级分工

| 当前身份 | 可以执行 | 边界 |
| --- | --- | --- |
| 主库 Agent | 管理全库项目、共享知识、模板、主规则、同步目录；用 `--project ID` 选择项目 | 不覆盖用户调整、原始交接材料或同步凭据 |
| 项目 Agent | 维护本项目笔记、附件、主页进展、状态、索引、规则补充、回收站与历史 | 不能操作其他项目、创建/整体删除项目、修改共享模板或执行覆盖升级 |
| 项目读取共享知识 | `shared list/find/get` 与 `rules` | 原件只存主库，引用使用主库 URI |

CLI 的上下文检查是命令层约束，不是操作系统隔离或身份认证。不要用 `--vault`、切换目录或其他文件工具绕过当前 Agent 的边界。

## 按任务执行

- 笔记与资产：使用 `note`、`file`、`folder`；新增优先选现有模板，填写实际项目/运行编号，未知证据标记待核实。
- 项目与配置：主库使用 `project create/delete`、`template`、`blueprint` 和 `sync`；主页进展/状态与索引在当前项目范围内更新。
- Agent 规则：先 `agents show/render`，再应用；主库维护共享模板，本库补充用 `agents customize`，保存在 `AGENTS.local.md`，不能放宽主规则。
- 删除、恢复与回滚：用 `trash`、`history`，保留返回的操作编号。删除默认进入当前库 `.trash`，不手改恢复记录。
- 版本升级：用户已要求升级时才执行 `update apply`；主库或库外独立安装执行，项目 Agent 只能查询。

执行上述操作时，按需读取 [references/commands.md](references/commands.md) 中对应段落，获取准确参数与示例。无需为只读版本查询加载全部参考。

## 修改与失败处理

1. 修改前用 get/show 查询目标及当前 SHA256，传入 `--if-hash`；优先追加正文/证据。身份文件和维护日志使用专用命令。
2. 全文替换必须已有用户明确授权，才声明 `--authorized-replace`；此标志本身不是授权来源。不要为了修复哈希冲突盲目读取新哈希后覆盖，应先比较新变化并调整操作。
3. 恢复遇到占位冲突时停止该恢复，不删除现有内容、不永久清空回收站。回滚仅针对有旧版本且后续未再改变的文件。
4. 交接只有产出可定位、未核实项已记录后才完成；`handoff` 的 complete 状态需要真实 `outputs` 路径列表。代码、原始数据和权重仍留在库外。
5. 默认读取 UTF-8 JSON 的 `ok/result/error`。退出码 0 是命令成功，1 是操作错误，2 是检查发现问题；升级返回 pending 仅表示已安排替换，必须用 `update status` 核实 complete/failed。
6. 完成后执行当前范围的 `check`；改动结构蓝图另执行 `blueprint validate`。报告实际变更、操作编号、验证结果和未完成事项，不把 CLI 校验当作研究结论或跨设备同步已获验证。

本 skill 不授权额外推送、公开仓库、发布 Release、永久删除、自动清理附件或运行外部实验；这些行为以用户当前任务授权为准。
