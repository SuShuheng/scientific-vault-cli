"""Deploy only the native exe; Python source stays in the independent repo."""
from pathlib import Path
import argparse
import shutil
import sys

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / 'src'))
sys.dont_write_bytecode = True
from svault.context import Context
from svault.capabilities import OPERATIONS

def deploy(root):
    context = Context.resolve(vault=root)
    context.require_main()
    root = context.root
    executable = REPO / 'dist/svault.exe'
    if not executable.is_file():
        raise ValueError('请先构建 Windows 成品并完成原生 exe 测试')
    (root / '工具').mkdir(exist_ok=True)
    shutil.copy2(executable, root / '工具/svault.exe')
    lines = ['# Vault 管理操作清单', '', '本清单对应已实现的 svault 1.1.0 命令；执行方法见 [[Vault管理CLI使用说明]]。', '', '| 对象/操作 | 命令 | Agent 范围 | 行为与约束 |', '| --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(row) + ' |' for row in OPERATIONS]
    lines += ['', '## 默认边界', '', '- 子库写入仅当前项目；共享知识只读。主库可通过 --project 选择项目。', '- 删除进入 .trash，提供恢复；修改保存旧版本，不覆盖后续变化。', '- 无永久删除、自动附件清理或项目跨库搬迁。', '- version 在任意目录可用；覆盖升级只限主库或库外独立安装，升级后用 update status 检查。', '- Python 源码与测试只保存在独立 Git 仓库，不重新复制进 Vault。']
    (root / '规则与模板/Vault管理操作清单.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('已部署 svault 1.1.0 exe：' + str(root / '工具/svault.exe'))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='部署通过测试的 svault.exe，不部署 Python 运行时')
    p.add_argument('--vault', type=Path, required=True)
    deploy(p.parse_args().vault)
