"""Generate leaf vaults from a visible folder blueprint, without reinitializing a vault."""
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import json
import os
import re
import shutil
import tempfile
import yaml
from .identity import main_name, main_identity
from . import __version__

TEMPLATE = Path('规则与模板/项目文件夹模板/标准科研项目')
SYNC_LIST = Path('规则与模板/子库同步目录.json')
ID_RE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}

def load(p, default=None):
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else default

def atomic_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix='vault-config-', suffix='.tmp', dir=p.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, p)
    finally:
        if temporary.exists():
            temporary.unlink()

def safe_name(value):
    if not value or value in {'.', '..'} or value[-1] in ' .' or any(c in value for c in '/\\:*?"<>|#^[]\n\r\t') or any(ord(c) < 32 for c in value):
        raise ValueError('目录名称无效，不能含路径跳转、控制字符或链接保留字符')
    if value.split('.')[0].upper() in RESERVED:
        raise ValueError('目录名不能使用 Windows 保留设备名')

def relative_path(value):
    parts = PurePosixPath(value).parts
    if not parts or PurePosixPath(value).is_absolute() or '\\' in value:
        raise ValueError('模板路径必须是相对路径')
    for part in parts:
        safe_name(part)
    return Path(*parts)

def discover_vaults(root):
    projects = root / '科研项目'
    if not projects.exists():
        return []
    return sorted(p.parent for p in projects.rglob('.obsidian') if p.is_dir() and not any(x.startswith('.project-staging-') for x in p.parts))

def sync_patterns(root):
    return [{'pattern': (p.relative_to(root) / '.obsidian').as_posix(), 'caseSensitive': True} for p in discover_vaults(root)]

def apply_sync_dirs(root):
    shared = load(root / SYNC_LIST, {})
    if shared.get('schema_version') != 1 or not isinstance(shared.get('directories'), list):
        raise ValueError('共享同步目录清单缺失或格式无效')
    directories = []
    for item in shared['directories']:
        value = item.get('pattern', '')
        path = relative_path(value)
        if path.parts[0] != '科研项目' or path.name != '.obsidian':
            raise ValueError('共享清单只允许科研项目中的 .obsidian 目录')
        if not (root / path).resolve().is_relative_to((root / '科研项目').resolve()):
            raise ValueError('同步配置目录越出科研项目范围')
        directories.append({'pattern': path.as_posix(), 'caseSensitive': True})
    settings_path = root / '.obsidian/plugins/fast-note-sync/data.json'
    settings = load(settings_path)
    if not isinstance(settings, dict):
        raise ValueError('请先在本机主库安装并授权 fast-note-sync')
    existing = settings.get('configSyncOtherDirs') or '[]'
    try:
        rules = json.loads(existing) if isinstance(existing, str) else existing
    except json.JSONDecodeError:
        rules = [{'pattern': p.strip(), 'caseSensitive': True} for p in existing.splitlines() if p.strip()]
    if not isinstance(rules, list):
        raise ValueError('本机额外同步目录格式无效')
    # Preserve custom directories while replacing the managed project .obsidian rules.
    custom = [r for r in rules if not (r.get('pattern', '').startswith('科研项目/') and r.get('pattern', '').endswith('/.obsidian'))]
    settings['configSyncOtherDirs'] = json.dumps(custom + directories, ensure_ascii=False)
    atomic_json(settings_path, settings)
    return len(directories)

def render(text, values):
    return re.sub('|'.join(re.escape(k) for k in sorted(values, key=len, reverse=True)), lambda m: values[m.group()], text)

def validate_blueprint(root):
    blueprint = root / TEMPLATE
    manifest = load(blueprint / 'template.json')
    if not manifest or manifest.get('schema_version') != 1:
        raise ValueError('项目文件夹模板缺失或版本不支持')
    directories = [relative_path(x) for x in manifest['directories']]
    if any('.obsidian' in p.parts for p in directories):
        raise ValueError('蓝图内容目录不能再嵌套 Vault')
    sources = []
    for entry in manifest['files']:
        source = blueprint / relative_path(entry['source'])
        destination = relative_path(entry['destination'])
        if not source.is_file() or not source.resolve().is_relative_to(blueprint.resolve()):
            raise ValueError('文件模板不存在或越出蓝图目录')
        if '.obsidian' in destination.parts:
            raise ValueError('配置目录只允许由配置继承步骤生成')
        text = source.read_text(encoding='utf-8-sig')
        if source.name.endswith('.base.template'):
            try:
                definition = yaml.safe_load(text)
            except yaml.YAMLError:
                raise ValueError('Bases 文件模板 YAML 无效')
            if not isinstance(definition, dict) or not isinstance(definition.get('views'), list) or not definition['views']:
                raise ValueError('Bases 文件模板必须包含有效 views')
        sources.append((destination, text))
    notes = root / relative_path(manifest['note_templates'])
    if not notes.is_dir() or not notes.resolve().is_relative_to(root.resolve()):
        raise ValueError('笔记模板目录不存在或越出主库')
    return manifest, directories, sources, notes

def create_project(root, category, name, project_id, configure, example=False):
    root = root.resolve()
    if not ID_RE.fullmatch(project_id):
        raise ValueError('项目编号须以英文字母开头，仅含字母、数字、下划线或连字符，最长 64 字符')
    safe_name(name)
    if name.casefold() == main_name(root).casefold():
        raise ValueError('主库注册名不能用作子库名称')
    category_path = Path() if category in ('', '.') else relative_path(category.replace('\\', '/'))
    project_root = root / '科研项目'
    target = project_root / category_path / name
    if not target.resolve().is_relative_to(project_root.resolve()):
        raise ValueError('项目目标越出科研项目范围')
    empty_existing = target.exists() and target.is_dir() and not any(target.iterdir())
    if target.exists() and not empty_existing:
        raise ValueError('项目目录已存在，拒绝覆盖')
    for ancestor in target.parents:
        if ancestor == root:
            break
        if (ancestor / '.obsidian').exists():
            raise ValueError('不能在已有项目 Vault 内创建子 Vault')
    for vault in discover_vaults(root):
        if vault.name.casefold() == name.casefold():
            raise ValueError('子库名称必须全局唯一，建议在名称中加入项目编号')
        metadata = load(vault / '项目.json', {})
        if str(metadata.get('project_id', '')).casefold() == project_id.casefold():
            raise ValueError('项目编号已使用')
        if any(p.stem.casefold() == f'{project_id}-项目主页'.casefold() for p in vault.glob('*-项目主页.md')):
            raise ValueError('项目编号已使用')
    manifest, directories, sources, notes = validate_blueprint(root)
    sync_path = root / '.obsidian/plugins/fast-note-sync/data.json'
    settings = load(sync_path, {})
    uri = lambda file, vault=None: 'obsidian://open?' + urlencode({'vault': vault or main_name(root), 'file': file})
    created = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    values = {
        'PROJECT_ID': project_id, 'PROJECT_NAME': name, 'PROJECT_NAME_YAML': json.dumps(name, ensure_ascii=False),
        'PROJECT_CATEGORY_YAML': json.dumps(category_path.as_posix(), ensure_ascii=False), 'PROJECT_CREATED': created,
        'PROJECT_STATUS': 'example' if example else 'planning',
        'PROJECT_INTRO': '这是结构示例，所有内容均为占位，未代表真实研究或实验结果。' if example else '请先明确研究目标与成功标准。',
        'MAIN_VAULT_RELATIVE': '/'.join('..' for _ in target.relative_to(root).parts),
        'PROJECT_OPEN_URI': uri(project_id + '-项目主页', name),
        'SHARED_KNOWLEDGE_URI': uri('共享知识/共享知识入口'), 'MAIN_HOME_URI': uri('科研工作台')
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.project-staging-', dir=target.parent))
    assert stage.resolve().is_relative_to(project_root.resolve())
    published = False
    try:
        for directory in directories:
            (stage / directory).mkdir(parents=True, exist_ok=True)
        for destination, text in sources:
            p = stage / relative_path(render(destination.as_posix(), values))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(render(text, values), encoding='utf-8')
        note_dest = stage / relative_path(manifest['note_template_destination'])
        for p in notes.glob('*.md'):
            # Shared knowledge belongs in the main vault; project notes describe applications.
            if p.name == '共享知识.md':
                continue
            (note_dest / p.name).write_text(p.read_text(encoding='utf-8-sig').replace('PROJECT_ID', project_id), encoding='utf-8')
        atomic_json(stage / '项目.json', {'schema_version': 1, 'project_id': project_id, 'vault_name': name, 'category': category_path.as_posix(), 'created': created, 'template': manifest['name'], 'template_version': manifest['schema_version'], 'layout_version': __version__, 'parent_vault_id': main_identity(root)['vault_id']})
        configure(stage, project_id)
        if not (stage / 'AGENTS.md').exists() or not (stage / f'{project_id}-项目主页.md').exists():
            raise ValueError('模板未生成项目规则或主页')
        # On Windows rename refuses an existing destination; the final directory is never used as scratch space.
        if target.exists():
            if not empty_existing or any(target.iterdir()):
                raise ValueError('项目目录已被其他操作创建，拒绝覆盖')
            target.rmdir()
        stage.rename(target)
        published = True
        atomic_json(root / SYNC_LIST, {'schema_version': 1, 'directories': sync_patterns(root)})
        if settings.get('syncEnabled') and settings.get('configSyncEnabled'):
            try:
                apply_sync_dirs(root)
            except Exception as exc:
                raise RuntimeError(f'项目已完整创建，但本机同步清单应用失败；保留项目，请运行 sync apply：{exc}') from exc
        return target
    finally:
        if not published and stage.exists():
            assert stage.resolve().is_relative_to(project_root.resolve()) and stage.name.startswith('.project-staging-')
            shutil.rmtree(stage)
        if not published and empty_existing and not target.exists():
            target.mkdir()
