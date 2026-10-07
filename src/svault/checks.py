"""Read-only checks of the current vault, independent of the previous vault."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import sys
sys.dont_write_bytecode = True
import json
import re
import yaml
from .blueprint import discover_vaults, load, SYNC_LIST, validate_blueprint, ID_RE
from .identity import main_name, main_identity

ROOT = Path(__file__).resolve().parents[1]

def check(root):
    errors, warnings = [], []
    projects = discover_vaults(root)
    vaults = [root] + projects
    configurations = 0
    known_ids, names, metadata_by_vault = {}, {}, {}
    for vault in projects:
        if vault.name.casefold() == main_name(root).casefold():
            errors.append('子库名称与主库注册名冲突: ' + str(vault.relative_to(root)))
        for parent in projects:
            if parent != vault and vault.is_relative_to(parent):
                errors.append('项目内继续嵌套 Vault: ' + str(vault.relative_to(root)))
        identity = load(vault / '项目.json', {})
        homes = list(vault.glob('*-项目主页.md'))
        if len(homes) != 1:
            errors.append('项目主页数量必须为一: ' + str(vault.relative_to(root)))
            continue
        content = homes[0].read_text(encoding='utf-8-sig')
        try:
            data = yaml.safe_load(content.split('---', 2)[1]) if content.startswith('---') else {}
        except (yaml.YAMLError, IndexError):
            errors.append('项目主页属性无效: ' + str(homes[0].relative_to(root)))
            continue
        if not isinstance(data, dict):
            errors.append('项目主页属性必须为对象: ' + str(homes[0].relative_to(root)))
            continue
        project_id = data.get('project_id', '')
        if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
            errors.append('项目编号无效: ' + str(vault.relative_to(root)))
            continue
        if identity and identity.get('project_id') != project_id:
            errors.append('项目元数据与主页编号不一致: ' + str(vault.relative_to(root)))
        if identity.get('parent_vault_id') and identity['parent_vault_id'] != main_identity(root)['vault_id']:
            errors.append('项目父库身份不一致: ' + str(vault.relative_to(root)))
        if data.get('vault_name') != vault.name:
            errors.append('子库注册名与目录名不一致: ' + str(vault.relative_to(root)))
        if project_id.casefold() in known_ids:
            errors.append('项目编号重复: ' + project_id)
        if vault.name.casefold() in names:
            errors.append('子库名称重复，URI 有歧义: ' + vault.name)
        known_ids[project_id.casefold()] = vault
        names[vault.name.casefold()] = vault
        metadata_by_vault[vault] = project_id
        if not (vault / 'AGENTS.md').exists():
            errors.append('项目缺少 AGENTS.md: ' + str(vault.relative_to(root)))

    for vault in vaults:
        config_dir = vault / '.obsidian'
        for p in config_dir.rglob('*.json'):
            configurations += 1
            try:
                load(p)
            except (ValueError, UnicodeError):
                errors.append('JSON 配置无效: ' + str(p.relative_to(root)))
        try:
            enabled = load(config_dir / 'community-plugins.json', [])
            if not isinstance(enabled, list):
                raise ValueError()
            for plugin in enabled:
                for filename in ('main.js', 'manifest.json'):
                    if not (config_dir / 'plugins' / plugin / filename).is_file():
                        errors.append('启用插件文件缺失: ' + str(vault.relative_to(root)) + '/' + plugin + '/' + filename)
            sync = load(config_dir / 'plugins/fast-note-sync/data.json', {})
            if vault != root and ('fast-note-sync' in enabled or sync.get('syncEnabled') or sync.get('apiToken')):
                errors.append('子库启用独立同步或保存主库同步凭据: ' + str(vault.relative_to(root)))
            # Offline vaults are valid; sync state is informational in doctor.
        except (ValueError, TypeError):
            errors.append('插件启用列表或同步配置格式无效: ' + str(vault.relative_to(root)))

    excluded = {'.obsidian', '.git', '.claude', '.trash', '初始化备份', '__pycache__', 'tests', 'Agent模板', 'svault_runtime'}
    files = [p for p in root.rglob('*') if p.is_file() and not excluded.intersection(p.relative_to(root).parts)]
    notes = [p for p in files if p.suffix == '.md' and '笔记模板' not in p.parts and '90 模板' not in p.parts]
    bases = [p for p in files if p.suffix == '.base']
    for p in bases:
        try:
            definition = yaml.safe_load(p.read_text(encoding='utf-8-sig'))
            if not isinstance(definition, dict) or not isinstance(definition.get('views'), list):
                raise ValueError()
            if any(not isinstance(v, dict) or not v.get('type') for v in definition['views']):
                raise ValueError()
        except (yaml.YAMLError, ValueError, TypeError):
            errors.append('Bases 定义无效: ' + str(p.relative_to(root)))
    link_files = notes + bases
    for p in notes:
        content = p.read_text(encoding='utf-8-sig')
        if 'eyJhbGci' in content:
            errors.append('文档中发现疑似凭据: ' + str(p.relative_to(root)))
        scope = max((v for v in vaults if p.is_relative_to(v)), key=lambda v: len(v.parts))
        if content.startswith('---'):
            try:
                data = yaml.safe_load(content.split('---', 2)[1])
                if not isinstance(data, dict):
                    raise ValueError()
                if data.get('type'):
                    for prop in ('type', 'status', 'created'):
                        if data.get(prop) in (None, ''):
                            errors.append('记录缺少属性 ' + prop + ': ' + str(p.relative_to(root)))
                    if scope in metadata_by_vault and data.get('project_id') != metadata_by_vault[scope]:
                        errors.append('记录项目编号不匹配: ' + str(p.relative_to(root)))
                    if data.get('type') != 'maintenance' and ('PROJECT_ID' in str(data) or '{{' in str(data)):
                        errors.append('正式记录仍有模板占位符: ' + str(p.relative_to(root)))
            except (yaml.YAMLError, ValueError, IndexError):
                errors.append('笔记属性无效: ' + str(p.relative_to(root)))
        visible = [f for f in link_files if f.is_relative_to(scope)]
        for target in re.findall(r'\[\[([^\]]+)\]\]', content):
            target = target.split('|')[0].split('#')[0]
            if not target:
                continue
            wanted = target[:-3] if target.endswith('.md') else target
            matches = [f for f in visible if (f.name if f.suffix == '.base' else f.stem) == wanted or f.relative_to(scope).as_posix().removesuffix('.md') == wanted]
            if len(matches) != 1:
                errors.append('Wiki 链接未解析或有歧义: ' + str(p.relative_to(root)) + ' -> ' + target)
        for address in re.findall(r'\]\((obsidian://[^)]+)\)', content):
            query = parse_qs(urlparse(address).query)
            vault_name = query.get('vault', [''])[0]
            destination = root if vault_name == main_name(root) else names.get(vault_name.casefold())
            target = query.get('file', [''])[0]
            if destination is None or not target:
                warnings.append('无法核实 URI 目标: ' + str(p.relative_to(root)))
            elif not any(f.is_relative_to(destination) and (f.stem == target or f.relative_to(destination).as_posix().removesuffix('.md') == target) for f in link_files):
                errors.append('URI 目标文件不存在: ' + str(p.relative_to(root)) + ' -> ' + target)
    expected = {(v.relative_to(root) / '.obsidian').as_posix() for v in projects}
    try:
        shared = load(root / SYNC_LIST, {})
        recorded = {d['pattern'] for d in shared.get('directories', [])}
        settings = load(root / '.obsidian/plugins/fast-note-sync/data.json', {})
        local = settings.get('configSyncOtherDirs') or '[]'
        local = json.loads(local) if isinstance(local, str) else local
        applied = {d['pattern'] for d in local}
        if recorded != expected:
            errors.append('共享子库同步目录清单与实际项目不一致')
        if settings.get('syncEnabled') and settings.get('configSyncEnabled') and not expected.issubset(applied):
            errors.append('本机同步目录遗漏子库配置')
        validate_blueprint(root)
    except (ValueError, KeyError, TypeError):
        errors.append('项目模板或同步目录清单无效')
    return {'projects': len(projects), 'json_configs': configurations, 'bases': len(bases), 'notes': len(notes), 'errors': errors, 'warnings': warnings}

