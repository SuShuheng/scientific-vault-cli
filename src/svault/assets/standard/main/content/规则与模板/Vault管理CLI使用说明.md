# Vault 管理 CLI 使用说明

入口为 `工具/svault.exe`，无需 Python。先执行 version、whoami、rules、operations 确认版本与作用域。
主库可用 --project ID 选择项目；子库不得切换目录或参数冒充主库。
笔记用 note add/get/edit/find/list/delete；修改传 --if-hash，正文优先追加，替换须已有用户授权。
删除进入 .trash，恢复用 trash restore，修改回滚用 history undo。原始交接材料不可自动修改或清理。
完整功能见 [[Vault管理操作清单]]；结构诊断用 doctor，repair 默认只读预览，应用用 --apply --if-plan-hash。
agents diff 对比规则，agents sync 应用当前库模板及本库补充，不批量覆盖其他项目。
版本查询用 version；更新用 update check/apply/status，pending 不代表升级完成。子库不能覆盖共享 exe。

[公开源码与 skill](https://github.com/SuShuheng/scientific-vault-cli) · [Releases](https://github.com/SuShuheng/scientific-vault-cli/releases)
