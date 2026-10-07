"""Explicit, local preference inheritance. Plugin state is never inherited."""
from pathlib import Path
import json
import os
from .context import VaultError, parts
from .blueprint import load, atomic_json

EDITOR_KEYS = {'useMarkdownLinks', 'newLinkFormat', 'promptDelete', 'alwaysUpdateLinks', 'trashOption', 'showInlineTitle', 'showLineNumber', 'defaultViewMode', 'showUnsupportedFiles', 'mobileToolbarCommands', 'readableLineLength', 'spellcheck', 'vimMode', 'strictLineBreaks'}
APPEARANCE_KEYS = {'cssTheme', 'theme', 'interfaceFontFamily', 'textFontFamily', 'monospaceFontFamily', 'baseFontSize', 'baseFontSizeAction', 'accentColor', 'enabledCssSnippets'}
SKIP_NAMES = {'data.json', 'workspace.json', 'workspace-mobile.json', 'configHashMap.json', 'fileHashMap.json', 'syncHashMap.json', 'folderSnapshot.json', 'temp-chunks', 'conflict-notes', '__pycache__', 'cache', 'logs'}

def no_links(path):
    path = Path(path).absolute()
    for p in (path, *path.parents):
        if p.exists() or p.is_symlink():
            if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
                raise VaultError('路径经过符号链接或目录联接：' + str(p), 'permission_denied')
    return path

def inspect(source):
    source = no_links(source)
    config = source / '.obsidian'
    if not config.is_dir():
        raise VaultError('参考目录不是 Obsidian Vault')
    plugins = []
    if (config / 'plugins').is_dir():
        for p in sorted((config / 'plugins').iterdir()):
            if not p.is_dir():
                continue
            try:
                no_links(p)
                no_links(p / 'manifest.json')
                manifest = load(p / 'manifest.json', {})
                if not isinstance(manifest, dict):
                    raise ValueError()
                plugins.append({'id': manifest.get('id', p.name), 'directory': p.name, 'version': manifest.get('version'), 'available': (p / 'manifest.json').is_file() and (p / 'main.js').is_file()})
            except (VaultError, ValueError):
                plugins.append({'id': p.name, 'directory': p.name, 'available': False})
    return {'source': str(source), 'groups': {key: (config / filename).is_file() for key, filename in [('editor', 'app.json'), ('hotkeys', 'hotkeys.json'), ('appearance', 'appearance.json')]}, 'plugins': plugins, 'excluded': ['插件 data.json', '凭据', '窗口布局', '缓存', '同步索引']}

def selection(source=None, include=None, plugin_ids=None):
    groups = [x.strip() for x in (include or '').split(',') if x.strip()]
    plugin_ids = plugin_ids or []
    if set(groups) - {'editor', 'hotkeys', 'appearance', 'plugins'}:
        raise VaultError('配置类别只能是 editor,hotkeys,appearance,plugins')
    if (groups or plugin_ids) and not source:
        raise VaultError('选择继承配置时必须提供 --from-vault')
    if plugin_ids and 'plugins' not in groups:
        raise VaultError('--plugin 需要 --include plugins')
    if 'plugins' in groups and not plugin_ids:
        raise VaultError('继承插件必须用 --plugin 明确列出插件 ID')
    result = {'source': str(Path(source).absolute()) if source else None, 'groups': groups, 'plugin_ids': plugin_ids, 'files': [], 'json': {}, 'errors': [], 'excluded': ['data.json', 'workspace*.json', '凭据与运行时状态']}
    if not source:
        return result
    inventory = inspect(source)
    config = Path(source) / '.obsidian'
    for group, filename, keys in [('editor', 'app.json', EDITOR_KEYS), ('hotkeys', 'hotkeys.json', None), ('appearance', 'appearance.json', APPEARANCE_KEYS)]:
        if group not in groups:
            continue
        p = config / filename
        if not p.is_file():
            result['errors'].append('选定配置缺失：' + filename)
            continue
        no_links(p)
        data = load(p)
        if not isinstance(data, dict):
            raise VaultError('选定配置不是 JSON 对象')
        if group == 'hotkeys':
            result['json'][filename] = {k: [{'modifiers': item.get('modifiers', []), 'key': item['key']} for item in v if isinstance(item, dict) and isinstance(item.get('key'), str)] for k, v in data.items() if isinstance(v, list)}
        else:
            result['json'][filename] = {k: v for k, v in data.items() if k in keys}
    if 'appearance' in groups:
        appearance = result['json'].get('appearance.json', {})
        theme = appearance.get('cssTheme')
        if theme:
            if len(parts(theme)) != 1:
                raise VaultError('主题名称无效')
            collect(config / 'themes' / theme, '.obsidian/themes/' + theme, result)
        for snippet in appearance.get('enabledCssSnippets', []):
            if len(parts(snippet)) != 1:
                raise VaultError('CSS snippet 名称无效')
            p = config / 'snippets' / (snippet + '.css')
            if not p.is_file():
                result['errors'].append('选定 CSS snippet 缺失：' + snippet)
            else:
                no_links(p)
                result['files'].append({'source': str(p), 'destination': '.obsidian/snippets/' + p.name})
    for plugin_id in plugin_ids:
        if len(parts(plugin_id)) != 1:
            raise VaultError('插件 ID 无效')
        matches = [p for p in inventory['plugins'] if p['id'] == plugin_id and p['available']]
        if len(matches) != 1:
            result['errors'].append('选定插件缺失或不完整：' + plugin_id)
        else:
            collect(config / 'plugins' / matches[0]['directory'], '.obsidian/plugins/' + plugin_id, result)
    return result

def collect(directory, destination, result):
    if not directory.is_dir():
        result['errors'].append('选定资源目录缺失：' + str(directory))
        return
    no_links(directory)
    for current, dirs, files in os.walk(directory, followlinks=False):
        for d in dirs:
            no_links(Path(current) / d)
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP_NAMES]
        for name in files:
            if name.startswith('.') or name in SKIP_NAMES or name.endswith(('.pyc', '.log', '.tmp')):
                continue
            source = Path(current) / name
            no_links(source)
            result['files'].append({'source': str(source), 'destination': destination + '/' + source.relative_to(directory).as_posix()})

def apply(plan, target):
    import shutil
    if plan['errors']:
        raise VaultError('选定配置存在缺失资源，不能继承')
    for name, preferences in plan['json'].items():
        p = target / '.obsidian' / name
        data = load(p, {})
        data.update(preferences)
        atomic_json(p, data)
    for entry in plan['files']:
        source = no_links(entry['source'])
        destination = target / entry['destination']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    if 'plugins' in plan['groups']:
        atomic_json(target / '.obsidian/community-plugins.json', plan['plugin_ids'])
