# scientific-vault-cli / svault

面向主 Vault 与叶级项目 Vault 的科研笔记管理 CLI。提供作用域校验、可恢复删除、文件旧版本、模板和 Agent 规则生成、项目结构维护及审计记录。工具不调用 AI，不运行训练，不连接外部实验仓库。

## 运行

Windows 原生版本为 `dist/svault.exe`，不需要另装 Python。安装到笔记库 `工具/svault.exe` 后，从主库执行：

```powershell
.\工具\svault.exe whoami
.\工具\svault.exe permissions
.\工具\svault.exe operations
.\工具\svault.exe project list
.\工具\svault.exe check
```

子库 Agent 使用同一可执行文件，保持工作目录在本项目，例如：

```powershell
& "..\..\..\工具\svault.exe" whoami
& "..\..\..\工具\svault.exe" note list --type experiment
```

多层分类下相对主库的层数可能不同，应使用子库 AGENTS 中给出的主库相对路径。主库 Agent 用全局参数 `--project P001` 选择子库；子库 Agent 不能借此选其他项目。全局参数放子命令之前。

源码运行适用于 Windows/macOS/Linux，有 Python 与 PyYAML 的环境可执行 `python run_svault.py ...`，或安装包后直接运行 `svault`。库内还附有 `工具/svault.py` 和运行时包，作为 Python 入口。

```text
python -m pip install .
svault --version
```

当前只验证 Windows；此版本没有声称已构建 macOS/Linux 原生二进制。

## 操作与权限

运行 `svault operations` 获取完整清单。主库负责项目新建/删除、共享知识写入、模板和全库规则；子库负责本项目笔记、状态、规则补充与整理记录。子库可以只读查询共享知识与主库规则。

作用域从当前工作目录推导；从库外运行需指定 `--vault`。指定子库路径会获得子库范围，子库工作目录不能通过 `--vault` 或 `--project` 提升权限。
这是 CLI 内的操作约束，不是操作系统权限隔离或 Agent 身份认证。拥有普通文件系统写权限的进程仍可能通过其他工具操作文件；AGENTS 和系统沙箱共同约束这种行为。

## 笔记增删改查列

```text
svault --project P001 note add "03 实验记录/P001-实验-R001.md" --template 实验记录 --set run_id=R001
svault --project P001 note get "03 实验记录/P001-实验-R001.md"
svault --project P001 note edit "03 实验记录/P001-实验-R001.md" --if-hash <查询返回的hash> --append-file evidence.md --set status=running
svault --project P001 note find R001
svault --project P001 note list --type experiment
svault --project P001 note delete "03 实验记录/P001-实验-R001.md"
svault --project P001 trash list
svault --project P001 trash restore <删除操作编号>
```

正文优先追加；全文替换必须已有用户授权，并传 `--authorized-replace`，工具不会自行认定用户已经授权。科研记录身份属性不能通过普通编辑改变。实验/结果必须提供运行编号，交接完成必须提供真实输出路径列表。

## 删除、旧版本与日志

删除移动到当前库 `.trash/svault/<操作编号>/payload`，记录原位置；恢复不会覆盖现有文件。原始交接材料不能通过普通命令修改或删除。整个项目可由主库整体回收，再整体恢复；这会同时更新同步目录清单。
每次文件修改保存旧版本；`history undo <操作编号>` 只在当前内容仍等于该次修改产出时回滚，不覆盖后续编辑。无永久删除、自动附件清理或跨库搬迁命令。
维护摘要写入当前库 `维护日志`，实际恢复记录和旧版本位于 `.trash/svault`。日志不包含正文、密钥或实验物料。此回收站适配 CLI 恢复，不应假设跨端同步服务一定保留隐藏恢复数据；需要多端恢复时另行验证备份策略。

## Agent 规则

主规则模板：`规则与模板/Agent模板/主库AGENTS.md.template`。
项目规则模板：`规则与模板/项目文件夹模板/标准科研项目/AGENTS.md.template`，也是项目创建蓝图中的唯一项目 Agent 模板。

```text
svault agents template-get main
svault agents template-get project
svault agents render
svault agents apply --if-hash <当前AGENTS的hash>
svault --project P001 agents render
svault --project P001 agents customize --body-file project-rules.md
svault --project P001 agents apply --if-hash <当前AGENTS的hash>
```

先预览，再应用；覆盖需要当前文件的 SHA256，旧版本可回滚。`AGENTS.local.md` 保存本库补充规则，重新生成时合并；补充规则不得放宽主库边界。自然语言规则的语义由 Agent 遵循，CLI 不声称能判断所有语义冲突。

## 项目、模板、检查与同步

```text
svault project create --category 医学影像/分割 --name P001-研究名称 --id P001
svault project get P001
svault --project P001 project status active --if-hash <项目主页hash>
svault project delete P001
svault trash restore <项目删除操作编号>
svault template list
svault blueprint validate
svault check
svault sync rebuild
svault sync apply
```

项目创建使用本 Vault 的结构模板与当前设置，不重新初始化主库。目录/项目改名和跨库移动暂不自动执行，避免未同步修改 Wiki/URI 与附件引用。
全库检查与子库检查均只读；新建目录/笔记只是操作，不代表真实实验或联网同步已经验证。

## 开发与构建

```text
python -m unittest discover -s tests -v
python -m PyInstaller --onefile --name svault --paths src run_svault.py
```

源码、测试及构建文件归独立仓库管理；笔记库中的运行时副本是安装产物。发布前验证 Windows 可执行文件的权限拒绝与 JSON 输出。输出默认 UTF-8 JSON，成功 exit=0，操作错误 exit=1，检查发现问题 exit=2。
