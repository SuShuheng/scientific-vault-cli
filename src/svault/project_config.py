"""Inherit settings without copying sync credentials; no initialization side effects."""
from pathlib import Path
import shutil
from .blueprint import load, atomic_json

SKIP = {'configHashMap.json', 'fileHashMap.json', 'syncHashMap.json', 'folderSnapshot.json', 'temp-chunks', 'conflict-notes', '__pycache__'}

def configure_project(main, project, project_id):
    dest = project / '.obsidian'
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('app.json', 'appearance.json', 'hotkeys.json', 'core-plugins.json', 'page-preview.json', 'backlink.json', 'types.json'):
        source = main / '.obsidian' / name
        if source.exists():
            shutil.copy2(source, dest / name)
    for name in ('themes', 'snippets', 'plugins'):
        source = main / '.obsidian' / name
        if source.exists():
            def ignore(directory, names):
                excluded = set(SKIP)
                if Path(directory).name == 'fast-note-sync':
                    excluded.add('data.json')
                return excluded.intersection(names)
            shutil.copytree(source, dest / name, ignore=ignore, dirs_exist_ok=True)
    enabled = load(main / '.obsidian/community-plugins.json', [])
    atomic_json(dest / 'community-plugins.json', [p for p in enabled if p != 'fast-note-sync'])
    sync = load(dest / 'plugins/fast-note-sync/data.json', {})
    sync.update(syncEnabled=False, configSyncEnabled=False, apiToken='', api='', vault=project_id, configSyncOtherDirs='')
    atomic_json(dest / 'plugins/fast-note-sync/data.json', sync)
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
    templater = load(dest / 'plugins/templater-obsidian/data.json', {})
    templater['templates_folder'] = '90 模板'
    templater.setdefault('trigger_on_file_creation', False)
    templater.setdefault('enable_system_commands', False)
    atomic_json(dest / 'plugins/templater-obsidian/data.json', templater)
