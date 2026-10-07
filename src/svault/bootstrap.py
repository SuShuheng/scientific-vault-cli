"""Offline bootstrap using production assets embedded in the executable."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
from . import __version__
from .context import Context, VaultError, main_vault, identity, leaves
from .identity import main_name, main_identity, new_main_identity
from . import blueprint, profiles
from .project_config import configure_project
from .storage import locked

ASSETS = Path(__file__).resolve().parent / 'assets/standard'

def main_manifest():
    return blueprint.load(ASSETS / 'main/manifest.json')

def source_content():
    return ASSETS / 'main/content'

def permission(cwd=None, vault=None):
    cwd = Path(cwd or Path.cwd()).absolute()
    if main_vault(cwd) or vault:
        context = Context.resolve(cwd, vault)
        context.require_main()
        return context
    # A project whose main files are damaged must not become a provisioning context.
    for p in (cwd, *cwd.parents):
        if (p / '项目.json').exists() or list(p.glob('*-项目主页.md')):
            raise VaultError('项目 Agent 不能初始化或生成全局结构', 'permission_denied')
    return None

def empty_target(path):
    path = profiles.no_links(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise VaultError('目标必须是不存在或完全空的目录，拒绝覆盖', 'conflict')
    return path

def operations_document():
    from .capabilities import OPERATIONS
    lines = ['# Vault 管理操作清单', '', '| 对象/操作 | 命令 | Agent 范围 | 行为与约束 |', '| --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(row) + ' |' for row in OPERATIONS]
    return '\n'.join(lines) + '\n'

def publish(stage, target):
    empty = target.exists()
    empty_target(target)
    if empty:
        target.rmdir()
    try:
        stage.rename(target)
    except Exception:
        if empty and not target.exists():
            target.mkdir()
        raise

def project_plan(root, target, project_id):
    root, target = Path(root).resolve(), profiles.no_links(target)
    if not target.resolve().is_relative_to((root / '科研项目').resolve()) or target == root / '科研项目':
        raise VaultError('项目目标必须在父库科研项目目录内', 'permission_denied')
    if not project_id or not blueprint.ID_RE.fullmatch(project_id):
        raise VaultError('项目编号无效')
    empty_target(target)
    blueprint.safe_name(target.name)
    if target.name.casefold() == main_name(root).casefold():
        raise VaultError('子库名称与主库注册名冲突')
    for ancestor in target.parents:
        if ancestor == root:
            break
        if (ancestor / '.obsidian').exists():
            raise VaultError('不能在已有 Vault 中继续嵌套项目', 'permission_denied')
    for vault in leaves(root):
        if identity(vault).casefold() == project_id.casefold() or vault.name.casefold() == target.name.casefold():
            raise VaultError('项目编号或注册名已存在', 'conflict')
    manifest, dirs, sources, notes = blueprint.validate_blueprint(root)
    files = [p.as_posix().replace('PROJECT_ID', project_id) for p, _ in sources]
    files += ['90 模板/' + p.name for p in notes.glob('*.md') if p.name != '共享知识.md']
    files += ['项目.json', '.obsidian/app.json', '.obsidian/core-plugins.json', '.obsidian/hotkeys.json', '.obsidian/templates.json', '.obsidian/daily-notes.json']
    return {'kind': 'project', 'target': str(target), 'parent': str(root), 'project_id': project_id, 'directories': [p.as_posix() for p in dirs], 'files': sorted(files), 'config_source': str(root / '.obsidian'), 'excluded': ['插件 data.json', '凭据', '窗口布局', '缓存', '同步索引'], 'errors': []}

def initialize(target, kind='main', parent=None, project_id=None, source=None, include=None, plugins=None, dry_run=False, cwd=None, vault=None):
    permission(cwd, vault)
    target = empty_target(target)
    try:
        preferences = profiles.selection(source, include, plugins)
    except VaultError as exc:
        if dry_run:
            return {'kind': kind, 'target': str(target), 'errors': [str(exc)], 'profile': {'source': str(source), 'blocked': True}}
        raise
    if kind == 'project':
        if not parent:
            raise VaultError('项目初始化必须提供 --parent')
        root = main_vault(Path(parent))
        if root is None or root != Path(parent).resolve():
            raise VaultError('--parent 必须指向主 Vault')
        Context.resolve(cwd, parent).require_main()
        plan = project_plan(root, target, project_id)
        plan['profile'] = preferences
        plan['errors'] += preferences['errors']
        if dry_run:
            return plan
        if plan['errors']:
            raise VaultError('选定配置资源缺失，初始化未执行')
        category = target.parent.relative_to(root / '科研项目').as_posix()
        def configure(dst, pid):
            configure_project(root, dst, pid)
            profiles.apply(preferences, dst)
            # Source profiles cannot turn on project sync or redirect template paths.
            enabled = blueprint.load(dst / '.obsidian/community-plugins.json', [])
            blueprint.atomic_json(dst / '.obsidian/community-plugins.json', [x for x in enabled if x != 'fast-note-sync'])
        with locked(root):
            actual = blueprint.create_project(root, category, target.name, project_id, configure)
        settings = blueprint.load(root / '.obsidian/plugins/fast-note-sync/data.json', {})
        return {**plan, 'path': str(actual), 'status': 'complete', 'sync': 'configured' if settings.get('syncEnabled') else 'not_configured'}
    if parent or project_id:
        raise VaultError('主库初始化不接受项目参数')
    for ancestor in target.parents:
        if (ancestor / '.obsidian').exists():
            raise VaultError('主 Vault 不能嵌套在已有 Vault 中', 'permission_denied')
    blueprint.safe_name(target.name)
    manifest = main_manifest()
    plan = {'kind': 'main', 'target': str(target), 'directories': manifest['directories'], 'files': sorted(manifest['files'] + ['vault.json', '规则与模板/Vault管理操作清单.md'] + (['工具/svault.exe'] if getattr(sys, 'frozen', False) else [])), 'profile': preferences, 'errors': preferences['errors']}
    if dry_run:
        return plan
    if plan['errors']:
        raise VaultError('选定配置资源缺失，初始化未执行')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.svault-init-', dir=target.parent))
    assert stage.resolve().is_relative_to(target.parent.resolve())
    try:
        for name in manifest['directories']:
            (stage / name).mkdir(parents=True, exist_ok=True)
        for name in manifest['files']:
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_content() / name, p)
        blueprint.atomic_json(stage / 'vault.json', new_main_identity(target))
        (stage / '规则与模板/Vault管理操作清单.md').write_text(operations_document(), encoding='utf-8')
        profiles.apply(preferences, stage)
        if getattr(sys, 'frozen', False):
            shutil.copy2(sys.executable, stage / '工具/svault.exe')
        from .checks import check
        checked = check(stage)
        if checked['errors']:
            raise VaultError('生成内容检查失败：' + '; '.join(checked['errors']))
        publish(stage, target)
        return {**plan, 'path': str(target), 'status': 'complete', 'sync': 'not_configured', 'message': '本地创建完成，同步尚未配置；请在 Obsidian 中将目标文件夹打开为 Vault'}
    finally:
        if stage.exists():
            assert stage.resolve().is_relative_to(target.parent.resolve()) and stage.name.startswith('.svault-init-')
            shutil.rmtree(stage)

def blueprint_list(context=None):
    result = [{'kind': k, 'origin': 'builtin', 'version': __version__} for k in ('main', 'project')]
    if context and (context.root / blueprint.TEMPLATE / 'template.json').exists():
        result.append({'kind': 'project', 'origin': 'current_vault', 'path': str(context.root / blueprint.TEMPLATE)})
    return result

def generate_blueprint(kind, target, dry_run=False, cwd=None, vault=None):
    permission(cwd, vault)
    target = empty_target(target)
    if kind == 'main':
        manifest = main_manifest()
        files = ['template.json'] + [p.replace('.obsidian/', 'Obsidian配置/') + '.template' for p in manifest['files']]
        directories = manifest['directories']
    else:
        project_source = source_content() / blueprint.TEMPLATE
        manifest = blueprint.load(project_source / 'template.json')
        files = ['template.json'] + [p['source'] for p in manifest['files']]
        files += ['90 模板/' + p.name + '.template' for p in (source_content() / '规则与模板/笔记模板').glob('*.md') if p.name != '共享知识.md']
        directories = manifest['directories']
    plan = {'kind': kind, 'target': str(target), 'directories': directories, 'files': sorted(files), 'valid_vault': False}
    if dry_run:
        return plan
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.svault-blueprint-', dir=target.parent))
    try:
        for d in directories:
            (stage / d).mkdir(parents=True, exist_ok=True)
        if kind == 'main':
            mappings = []
            for name in manifest['files']:
                # Configuration files are templates, never an active .obsidian folder.
                destination = name.replace('.obsidian/', 'Obsidian配置/') + '.template'
                p = stage / destination
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_content() / name, p)
                mappings.append({'source': destination, 'destination': name})
            blueprint.atomic_json(stage / 'template.json', {'schema_version': 1, 'kind': 'main', 'template_version': __version__, 'directories': directories, 'files': mappings})
        else:
            for entry in manifest['files']:
                p = stage / entry['source']
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(project_source / entry['source'], p)
            manifest['note_template_files'] = []
            for note in (source_content() / '规则与模板/笔记模板').glob('*.md'):
                if note.name != '共享知识.md':
                    name = '90 模板/' + note.name + '.template'
                    (stage / name).write_text(note.read_text(encoding='utf-8'), encoding='utf-8')
                    manifest['note_template_files'].append(name)
            blueprint.atomic_json(stage / 'template.json', manifest)
        publish(stage, target)
        return {**plan, 'status': 'complete'}
    finally:
        if stage.exists():
            assert stage.resolve().is_relative_to(target.parent.resolve()) and stage.name.startswith('.svault-blueprint-')
            shutil.rmtree(stage)
