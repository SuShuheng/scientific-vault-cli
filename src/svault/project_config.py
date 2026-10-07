"""Inherit settings without copying sync credentials; no initialization side effects."""
from pathlib import Path
import shutil
from .blueprint import load, atomic_json
from .profiles import no_links

SKIP = {'configHashMap.json', 'fileHashMap.json', 'syncHashMap.json', 'folderSnapshot.json', 'temp-chunks', 'conflict-notes', '__pycache__'}

def configure_project(main, project, project_id):
    dest = project / '.obsidian'
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('app.json', 'appearance.json', 'hotkeys.json', 'core-plugins.json', 'page-preview.json', 'backlink.json', 'types.json'):
        source = main / '.obsidian' / name
        if source.exists():
            no_links(source)
            shutil.copy2(source, dest / name)
    for name in ('themes', 'snippets', 'plugins'):
        source = main / '.obsidian' / name
        if source.exists():
            no_links(source)
            for child in source.rglob('*'):
                no_links(child)
            def ignore(directory, names):
                excluded = set(SKIP)
                excluded.add('data.json')
                excluded.update(n for n in names if n.startswith('.'))
                return excluded.intersection(names)
            shutil.copytree(source, dest / name, ignore=ignore, dirs_exist_ok=True)
    enabled = load(main / '.obsidian/community-plugins.json', [])
    atomic_json(dest / 'community-plugins.json', [p for p in enabled if p != 'fast-note-sync'])
    if (dest / 'plugins/fast-note-sync/manifest.json').exists():
        atomic_json(dest / 'plugins/fast-note-sync/data.json', {'syncEnabled': False, 'configSyncEnabled': False, 'apiToken': '', 'api': '', 'vault': project_id, 'configSyncOtherDirs': ''})
    app = load(dest / 'app.json', {})
    app.update(attachmentFolderPath='80 附件', newFileLocation='current', alwaysUpdateLinks=True)
    atomic_json(dest / 'app.json', app)
    templates = load(main / '.obsidian/templates.json', {})
    templates['folder'] = '90 模板'
    templates.setdefault('dateFormat', 'YYYY-MM-DD')
    templates.setdefault('timeFormat', 'HH:mm')
    atomic_json(dest / 'templates.json', templates)
    daily = load(main / '.obsidian/daily-notes.json', {})
    daily.update(folder='06 科研日志', template='90 模板/科研日志', format=f'{project_id}-YYYY-MM-DD')
    atomic_json(dest / 'daily-notes.json', daily)
    if (dest / 'plugins/templater-obsidian/manifest.json').exists():
        atomic_json(dest / 'plugins/templater-obsidian/data.json', {'templates_folder': '90 模板', 'trigger_on_file_creation': False, 'enable_system_commands': False})
