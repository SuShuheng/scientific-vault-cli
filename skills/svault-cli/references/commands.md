# svault 1.0.0 命令参考

## 入口与输出

以下 PowerShell 示例中的 `$svault` 是已确认的完整 exe 路径；保持当前 Agent 的工作目录不变。示例 `P001`、`R001` 必须替换为实际项目与运行编号；先查询已有项目，不为演示创建虚假科研记录。

```powershell
& $svault version
& $svault whoami
& $svault rules
& $svault operations
& $svault note edit --help
```

调用形状：`svault [--vault PATH] [--project ID] COMMAND ACTION ...`。主库用 `--project` 选择子库；子库可以省略它，也只能选择自身。
默认输出 `{ "ok": true, "result": ... }` 或 `{ "ok": false, "error": ... }`；`--version` 是普通版本字符串。

## 笔记：增删改查列

主库选择项目的示例；项目 Agent 在自己目录内使用同一组命令，省略 `--project P001`。

```powershell
& $svault project list
& $svault --project P001 template list
& $svault --project P001 template render "实验记录" --title "P001-实验-R001"
& $svault --project P001 note add "03 实验记录/P001-实验-R001.md" --template "实验记录" --set "run_id=R001"
$note = (& $svault --project P001 note get "03 实验记录/P001-实验-R001.md" | ConvertFrom-Json)
& $svault --project P001 note edit "03 实验记录/P001-实验-R001.md" --if-hash $note.result.hash --append-file "证据说明.md" --set "status=running"
& $svault --project P001 note find "R001"
& $svault --project P001 note list --type experiment
& $svault --project P001 note delete "03 实验记录/P001-实验-R001.md"
```

新增也支持 `--body-file`、`--text`、`--type`、`--status`；`--set KEY=VALUE` 可重复，值按 YAML 解析。需要纯文本而可能被 YAML 解释成布尔/数字时，在值中保留 YAML 引号。
科研笔记需有 type/project_id/status/created；项目文件名以其编号加 `-` 开头。实验/结果需 run_id，交接需 handoff_id。共享知识位于主库，project_id 留空。
普通编辑不改变 type/project_id/created；`--append-text` 可直接追加短内容。已获授权的全文替换使用 `--replace-body-file PATH --authorized-replace`，仍需要当前哈希。
交接标为 complete 时，使用 `--set 'outputs=["05 总结与写作/P001-整理记录.md"]'` 提供当前库内实际存在的产出路径。

## 附件与普通目录

```powershell
& $svault file add "04 结果与图表/P001-指标.csv" --source "本机导出的指标.csv"
$asset = (& $svault file get "04 结果与图表/P001-指标.csv" --text | ConvertFrom-Json)
& $svault file edit "04 结果与图表/P001-指标.csv" --body-file "更新后的指标.csv" --if-hash $asset.result.hash --authorized-replace
& $svault file list "04 结果与图表"
& $svault file delete "04 结果与图表/P001-指标.csv"
& $svault folder add "00 收件箱/待整理"
& $svault folder list "00 收件箱"
& $svault folder delete "00 收件箱/待整理"
```

file get 默认返回大小与哈希，文本读取加 --text；Markdown 科研记录走 note，不走 file edit。附件当前限 50 MiB，不接收常见权重和原始训练数据格式。不能对包含受保护文件/交接原件的目录做普通递归删除；不自动改名、搬迁或清理附件。

## 项目与索引

```powershell
& $svault project create --category "医学影像/分割" --name "P001-研究名称" --id P001
& $svault project get P001
& $svault --project P001 project get
# 从 project get 获取主页 hash 后更新。
& $svault --project P001 project status active --if-hash "当前主页哈希"
& $svault --project P001 project edit --if-hash "当前主页哈希" --set "next_step=核对数据划分" --append-file "进展.md"
& $svault --project P001 index list
& $svault --project P001 index get "P001-实验"
& $svault --project P001 index set "P001-实验" --body-file "索引配置.txt" --if-hash "当前索引哈希"
```

项目编号以字母开头，仅含字母/数字/下划线/连字符，最长 64 字符；编号和子库名称全局唯一，scientific_notes 保留给主库。分类可以多层，不能穿过已有项目 Vault。
状态包括 planning/active/paused/writing/complete/archived/example；归档优先更新状态。项目进展可追加或维护属性，不能用 project edit 改编号、注册名、分类；替换主页正文需已有用户授权及 --authorized-replace。
项目索引必须保留自身 project_id 的 AND 过滤。创建/整体删除仅主库执行；主库 `project delete ID` 把整库移至主库回收站，并更新同步清单。

## 删除恢复与修改回滚

```powershell
& $svault trash list
& $svault trash get "删除操作编号"
& $svault trash restore "删除操作编号"
& $svault history list
& $svault history get "修改操作编号"
& $svault history undo "修改操作编号"
```

操作编号从成功结果 operation_id 获取，不从文件名猜测。主库可用 --project 访问对应子库的回收/历史记录；项目整体回收记录在主库，恢复也由主库执行。
restore 拒绝覆盖原位置；undo 拒绝覆盖后续改动。新增文件没有旧版本，用软删除撤销；没有永久清空命令。日志位于当前库维护日志，恢复数据位于 .trash/svault，不直接编辑它们。

## 笔记模板与目录蓝图

```powershell
& $svault template list
& $svault template get "实验记录"
& $svault template render "实验记录" --title "新笔记"
& $svault template add "新模板" --body-file "模板.md"
& $svault template set "实验记录" --body-file "更新模板.md" --if-hash "当前模板哈希"
& $svault template delete "新模板"
& $svault blueprint show
& $svault blueprint validate
& $svault blueprint set-file "95 索引/PROJECT_ID-实验.base.template" --body-file "更新蓝图.txt" --if-hash "该文件当前哈希"
```

模板写入与蓝图修改仅主库；主库 --project 可维护指定子库模板，项目 Agent 只查询/预览模板。蓝图读取主库规则与模板下的标准科研项目，修改影响后续创建，不自动覆盖既有项目。set-file 只修改已有蓝图源，结构校验失败会回滚。

## AGENTS 规则维护

```powershell
& $svault agents template-get main
& $svault agents template-get project
& $svault agents template-set project --body-file "项目规则模板.txt" --if-hash "模板当前哈希"
& $svault agents show
& $svault agents render
& $svault agents apply --if-hash "当前AGENTS哈希"
& $svault agents customize --body-file "本库补充.txt"
& $svault agents show --local
```

主模板位于 `规则与模板/Agent模板/主库AGENTS.md.template`；项目模板位于标准项目蓝图的 `AGENTS.md.template`。
template-set 只允许主库。当前库的 render 会替换项目变量，并合并 AGENTS.local.md；apply 修改已有文件需当前哈希，新文件才可省略。customize 首次新增无需哈希，修改现有补充文件加 --if-hash。补充不会自动生效到 AGENTS，需要再次 render/apply。
项目 Agent 可生成自身 AGENTS 与补充，但不可改共享模板。先比较渲染内容与当前规则，保留用户调整；自然语言规则冲突仍需 Agent 判断，工具不作 AI 语义审查。

## 共享知识、同步与检查

```powershell
& $svault shared list
& $svault shared find "数据划分"
& $svault shared get "共享知识入口.md"
& $svault sync show
& $svault sync rebuild
& $svault sync apply
& $svault check
```

shared get 的路径相对主库共享知识目录，返回主库 URI；项目只读。sync 仅主库：rebuild 重新发现子库并更新共享/本机清单，apply 在本机登记已有共享清单，不输出密钥。每台设备登记后仍须重新加载同步插件并完整拉取配置，CLI 成功不证明多端同步通过。
check 为只读，主库检查全库，项目仅自身。库中无需 Python 脚本。

## 版本与覆盖式升级

```powershell
& $svault version
& $svault --version
& $svault update check
& $svault update apply
& $svault update status
```

只有用户请求的升级任务才执行 apply；主库或库外独立安装可执行，子库禁止。默认仓库为 [SuShuheng/scientific-vault-cli](https://github.com/SuShuheng/scientific-vault-cli)，默认最新稳定 Release；--tag v1.0.0 可指定版本。除用户明确指定的 fork 外，保持默认来源。
校验大小、SHA256、产品/版本后，命令退出才由隐藏辅助进程覆盖 exe。pending 不是成功；稍后检查 complete/failed/rollback_failed。失败时保留旧版及备份，不连续盲目重试，也不绕过校验。
同版本默认不覆盖；用户要求重装时才加 --force，不允许降级。无须把 GitHub 登录密钥写入 Vault。
