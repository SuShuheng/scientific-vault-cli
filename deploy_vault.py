"""Install runtime and compatibility entry points; never copy research data into this repo."""
from pathlib import Path
import argparse
import json
import shutil
import sys

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / 'src'))
sys.dont_write_bytecode = True
from svault.context import Context
from svault.capabilities import OPERATIONS
from svault.service import MAIN_AGENT_TEMPLATE

PREFIX = "from pathlib import Path\nimport sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, str(Path(__file__).resolve().parent / 'svault_runtime'))\n"

def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')

def deploy(root):
    context = Context.resolve(vault=root)
    context.require_main()
    root = context.root
    executable = REPO / 'dist/svault.exe'
    if not executable.is_file():
        raise ValueError('请先构建 Windows 成品')
    target = root / '工具/svault_runtime/svault'
    shutil.copytree(REPO / 'src/svault', target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(executable, root / '工具/svault.exe')
    put(root / '工具/svault.py', PREFIX + "from svault.cli import main\nif __name__ == '__main__':\n    main()\n")
    put(root / '工具/project_template.py', PREFIX + 'from svault.blueprint import *\n')
    put(root / '工具/check_vault.py', PREFIX + "from svault.checks import check\nfrom svault.cli import main\nROOT = Path(__file__).resolve().parents[1]\nif __name__ == '__main__':\n    main(['--vault', str(ROOT), 'check'])\n")
    put(root / '工具/new_project.py', PREFIX + "import argparse\nfrom svault.cli import main\ndef run():\n    p=argparse.ArgumentParser(description='兼容入口：统一 CLI 新建科研项目')\n    p.add_argument('--category', required=True)\n    p.add_argument('--name', required=True)\n    p.add_argument('--id', required=True)\n    a=p.parse_args()\n    main(['--vault', str(Path(__file__).resolve().parents[1]), 'project', 'create', '--category', a.category, '--name', a.name, '--id', a.id])\nif __name__ == '__main__':\n    run()\n")
    put(root / '工具/apply_sync_dirs.py', PREFIX + "import argparse\nfrom svault.cli import main\ndef run():\n    p=argparse.ArgumentParser(description='兼容入口：统一 CLI 登记子库配置目录')\n    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])\n    a=p.parse_args()\n    main(['--vault', str(a.root.resolve()), 'sync', 'apply'])\nif __name__ == '__main__':\n    run()\n")
    legacy = root / '工具/setup_vault.py'
    source = legacy.read_text(encoding='utf-8')
    a, b = source.index('def configure_project('), source.index('def create_project(')
    adapter = "def configure_project(root, project_id):\n    import sys\n    sys.path.insert(0, str(Path(__file__).resolve().parent / 'svault_runtime'))\n    from svault.project_config import configure_project as configure\n    return configure(ROOT, root, project_id)\n\n"
    source = source[:a] + adapter + source[b:]
    entry = source.index("if __name__ == '__main__':")
    source = source[:entry] + "if __name__ == '__main__':\n    raise SystemExit('主库初始化已完成；请使用 svault 管理，禁止重复初始化。')\n"
    put(legacy, source)
    manifest_path = root / '规则与模板/项目文件夹模板/标准科研项目/template.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if '维护日志' not in manifest['directories']:
        manifest['directories'].append('维护日志')
    put(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (manifest_path.parent / '维护日志').mkdir(exist_ok=True)
    put(root / MAIN_AGENT_TEMPLATE, (root / 'AGENTS.md').read_text(encoding='utf-8'))
    lines = ['# Vault 管理操作清单', '', '本清单对应已实现的 svault 命令；执行方法见 [[Vault管理CLI使用说明]]。', '', '| 对象/操作 | 命令 | Agent 范围 | 行为与约束 |', '| --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(row) + ' |' for row in OPERATIONS]
    lines += ['', '## 默认边界', '', '- 子库写入仅当前项目；共享知识只读。主库可通过 --project 选择项目。', '- 删除移动到 .trash，提供恢复；修改保存旧版本，防止覆盖后续变化。', '- 不提供永久删除、自动清理附件或项目/目录跨库改名搬迁。', '- 本阶段仅笔记库维护，不包含训练执行、实验仓库关联或 AI 调用。', '- CLI 作用域不能替代系统权限和 Agent 规则；其他设备实际运行与回收数据跨端同步尚需验证。']
    put(root / '规则与模板/Vault管理操作清单.md', '\n'.join(lines) + '\n')
    put(root / '工具/svault_runtime/installation.json', json.dumps({'version': '0.1.0', 'source_repository': 'H:/GitHub/scientific-vault-cli', 'entry': '工具/svault.exe'}, ensure_ascii=False, indent=2) + '\n')
    print('CLI 已部署：' + str(root / '工具/svault.exe'))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='安装已构建的 Vault 管理 CLI')
    p.add_argument('--vault', type=Path, required=True)
    deploy(p.parse_args().vault)
