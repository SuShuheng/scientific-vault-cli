"""Identity adoption, structured diagnosis and conservative, hashed repair plans."""
from pathlib import Path
from dataclasses import replace
from urllib.parse import urlencode
import difflib
import hashlib
import json
import yaml
from . import __version__, blueprint
from .bootstrap import main_manifest, source_content, operations_document
from .context import Context, VaultError, main_vault, identity, leaves
from .identity import main_identity, main_name, new_main_identity
from .profiles import no_links
from .storage import Store, atomic, digest, locked

def target_context(context, path=None):
    if path is None:
        return context
    target = no_links(path)
    root = main_vault(target)
    if root != context.root:
        raise VaultError('诊断/登记目标不属于当前主库', 'permission_denied')
    if target == root:
        if context.role == 'project':
            raise VaultError('子库不能诊断或修复全主库', 'permission_denied')
        return replace(context, scope=root, project_id=None)
    if target not in leaves(root):
        raise VaultError('目标不是主库或叶级项目根目录')
    if context.role == 'project' and target != context.scope:
        raise VaultError('子库只能操作自身', 'permission_denied')
    return replace(context, scope=target, project_id=identity(target))

def vault_get(context):
    if context.project_id:
        data = blueprint.load(context.scope / '项目.json', {})
        return {**data, 'kind': 'project', 'project_id': context.project_id, 'path': str(context.scope), 'registered': bool(data), 'parent': {'path': str(context.root), 'vault_id': main_identity(context.root)['vault_id'], 'vault_name': main_name(context.root)}}
    return {**main_identity(context.root), 'path': str(context.root), 'registered': (context.root / 'vault.json').is_file()}

def vault_list(context):
    if context.role == 'project':
        return [vault_get(context)]
    return [vault_get(replace(context, scope=context.root, project_id=None))] + [vault_get(replace(context, scope=v, project_id=identity(v))) for v in leaves(context.root)]

def register(context, path, dry_run=False):
    context.require_main()
    ctx = target_context(context, path)
    if ctx.project_id:
        homes = list(ctx.scope.glob('*-项目主页.md'))
        if len(homes) != 1:
            raise VaultError('项目主页不能唯一确定，拒绝登记')
        from .service import split_note
        props, _ = split_note(homes[0].read_text(encoding='utf-8-sig'))
        if props.get('project_id') != ctx.project_id or props.get('vault_name') != ctx.scope.name:
            raise VaultError('项目元数据与主页身份不一致，拒绝猜测')
        for other in leaves(ctx.root):
            if other != ctx.scope and (identity(other).casefold() == ctx.project_id.casefold() or other.name.casefold() == ctx.scope.name.casefold()):
                raise VaultError('项目编号或注册名冲突')
        if ctx.scope.name.casefold() == main_name(ctx.root).casefold():
            raise VaultError('项目名称与主库冲突')
        target = ctx.scope / '项目.json'
        data = blueprint.load(target, {})
        data.setdefault('schema_version', 1)
        data.setdefault('project_id', ctx.project_id)
        data.setdefault('vault_name', ctx.scope.name)
        data.setdefault('category', ctx.scope.parent.relative_to(ctx.root / '科研项目').as_posix())
        data.setdefault('layout_version', '1.0.0')
        data.setdefault('parent_vault_id', main_identity(ctx.root)['vault_id'])
        if data.get('project_id') != ctx.project_id:
            raise VaultError('项目身份冲突')
    else:
        target = ctx.root / 'vault.json'
        data = main_identity(ctx.root) if target.exists() else new_main_identity(ctx.root)
        if not target.exists():
            data['layout_version'] = '1.0.0'
    plan = {'target': str(target), 'kind': 'project' if ctx.project_id else 'main', 'metadata': data, 'before_hash': digest(target), 'files': [target.name]}
    if dry_run:
        return plan
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
    if target.exists() and json.loads(target.read_text(encoding='utf-8-sig')) == data:
        return {**plan, 'status': 'already_registered'}
    with locked(ctx.root):
        result = Store(ctx).write(target, encoded, 'vault-register', digest(target), managed=True, create=not target.exists())
    return {**result, 'metadata': data, 'status': 'complete'}

def expected_content(ctx):
    if not ctx.project_id:
        manifest = main_manifest()
        files = {name: (source_content() / name).read_bytes() for name in manifest['files'] if not name.startswith('.obsidian/')}
        files['规则与模板/Vault管理操作清单.md'] = operations_document().encode()
        agent_source = ctx.root / '规则与模板/Agent模板/主库AGENTS.md.template'
        if agent_source.exists():
            no_links(agent_source)
            agent_text = agent_source.read_text(encoding='utf-8-sig')
        else:
            agent_text = files['AGENTS.md'].decode()
        local = ctx.root / 'AGENTS.local.md'
        if local.exists():
            no_links(local)
            agent_text += '\n\n## 本库补充规则\n\n补充规则不得放宽主库约束。\n\n' + local.read_text(encoding='utf-8-sig')
        files['AGENTS.md'] = agent_text.encode()
        # Repair never adopts/replaces identities or user settings.
        return manifest['directories'], files
    project_source = ctx.root / blueprint.TEMPLATE
    if not (project_source / 'template.json').exists():
        project_source = source_content() / blueprint.TEMPLATE
    manifest = blueprint.load(project_source / 'template.json')
    values = {'PROJECT_ID': ctx.project_id, 'PROJECT_NAME': ctx.scope.name, 'MAIN_VAULT_RELATIVE': '/'.join('..' for _ in ctx.scope.relative_to(ctx.root).parts)}
    files = {}
    for entry in manifest['files']:
        if entry['destination'].endswith('-项目主页.md'):
            continue  # Missing research homepage/rules require human review, not guessing.
        p = project_source / blueprint.relative_path(entry['source'])
        if p.is_file():
            no_links(p)
            text = blueprint.render(p.read_text(encoding='utf-8-sig'), values)
            if entry['destination'] == 'AGENTS.md' and (ctx.scope / 'AGENTS.local.md').exists():
                text += '\n\n## 本库补充规则\n\n补充规则不得放宽主库约束。\n\n' + (ctx.scope / 'AGENTS.local.md').read_text(encoding='utf-8-sig')
            files[blueprint.render(entry['destination'], values)] = text.encode()
    notes = ctx.root / '规则与模板/笔记模板'
    if not notes.is_dir():
        notes = source_content() / '规则与模板/笔记模板'
    for p in notes.glob('*.md'):
        if p.name != '共享知识.md':
            no_links(p)
            files['90 模板/' + p.name] = p.read_text(encoding='utf-8-sig').replace('PROJECT_ID', ctx.project_id).encode()
    return manifest['directories'], files

def doctor(ctx):
    issues = []
    def issue(code, location, reason, repairable=False, severity='error'):
        issues.append({'code': code, 'location': location, 'severity': severity, 'reason': reason, 'repairable': repairable})
    try:
        main_identity(ctx.root)
    except VaultError:
        issue('invalid_identity', 'vault.json', '主库身份元数据损坏，必须人工核对')
    if not (ctx.root / 'vault.json').exists() and ctx.role == 'main':
        issue('legacy_identity', 'vault.json', '旧版主库尚未登记；使用 vault register', False, 'info')
    try:
        dirs, files = expected_content(ctx)
        for name in dirs:
            if not (ctx.scope / name).is_dir():
                issue('missing_directory', name, '标准目录缺失', True)
        for name in files:
            if not (ctx.scope / name).exists():
                issue('missing_standard_file', name, '标准模板、说明或索引缺失', True)
    except (ValueError, KeyError, TypeError, VaultError):
        issue('invalid_blueprint', str(blueprint.TEMPLATE), '蓝图损坏，不能自动替换')
    try:
        if ctx.project_id:
            from .service import Service
            checked = Service(ctx).scoped_check()
        else:
            from .checks import check
            checked = check(ctx.root)
        for error in checked['errors']:
            description = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
            is_derived = description in {'共享子库同步目录清单与实际项目不一致', '本机同步目录遗漏子库配置'}
            issue('derived_list' if is_derived else 'validation_error', str(ctx.scope), description, is_derived and ctx.role == 'main')
        for warning in checked.get('warnings', []):
            issue('warning', str(ctx.scope), str(warning), False, 'warning')
    except (ValueError, KeyError, TypeError, OSError, VaultError, yaml.YAMLError):
        issue('unreadable_structure', str(ctx.scope), '配置、身份或结构无法完整读取，禁止自动替换')
    try:
        settings = blueprint.load(ctx.root / '.obsidian/plugins/fast-note-sync/data.json', {})
        if not isinstance(settings, dict):
            raise TypeError()
    except (ValueError, TypeError):
        settings = {}
        issue('invalid_sync_config', '.obsidian/plugins/fast-note-sync/data.json', '同步配置语法损坏，不自动替换')
    return {'scope': str(ctx.scope), 'issues': issues, 'errors': [i['reason'] for i in issues if i['severity'] == 'error'], 'warnings': [], 'sync': 'configured' if settings.get('syncEnabled') else 'not_configured'}

def repair_plan(ctx):
    report = doctor(ctx)
    dirs, files = expected_content(ctx)
    actions = []
    for name in dirs:
        p = no_links(ctx.scope / name)
        if not p.exists():
            actions.append({'kind': 'directory', 'path': name})
        elif not p.is_dir():
            raise VaultError('标准目录位置被文件占用，不能自动修复')
    for name, body in files.items():
        p = no_links(ctx.scope / name)
        if not p.exists():
            actions.append({'kind': 'file', 'path': name, 'content_hash': hashlib.sha256(body).hexdigest()})
    if ctx.scope == ctx.root and ctx.role == 'main':
        derived = {'schema_version': 1, 'directories': blueprint.sync_patterns(ctx.root)}
        p = ctx.root / blueprint.SYNC_LIST
        if blueprint.load(p, None) != derived:
            actions = [a for a in actions if a['path'] != blueprint.SYNC_LIST.as_posix()]
            actions.append({'kind': 'derived', 'path': blueprint.SYNC_LIST.as_posix(), 'content_hash': hashlib.sha256(json.dumps(derived, sort_keys=True).encode()).hexdigest()})
        settings = blueprint.load(ctx.root / '.obsidian/plugins/fast-note-sync/data.json', {})
        if settings.get('syncEnabled') and settings.get('configSyncEnabled'):
            applied = json.loads(settings.get('configSyncOtherDirs') or '[]')
            if not {x['pattern'] for x in derived['directories']}.issubset({x['pattern'] for x in applied}):
                actions.append({'kind': 'local_sync', 'path': '.obsidian/plugins/fast-note-sync/data.json'})
    # Hash every relevant file/dir: a concurrent user change invalidates the preview.
    snapshot = {}
    for name in sorted(set(files) | set(dirs) | {'AGENTS.md', '项目.json', 'vault.json'}):
        p = ctx.scope / name
        snapshot[name] = digest(p) if p.is_file() else 'directory' if p.is_dir() else None
    for name in ('AGENTS.local.md', '规则与模板/Agent模板/主库AGENTS.md.template'):
        snapshot[name] = digest(ctx.scope / name)
    snapshot['project_identities'] = [{'path': v.relative_to(ctx.root).as_posix(), 'home': digest(v / (identity(v) + '-项目主页.md')), 'metadata': digest(v / '项目.json')} for v in leaves(ctx.root)] if ctx.role == 'main' else []
    snapshot['sync_config'] = digest(ctx.root / '.obsidian/plugins/fast-note-sync/data.json') if ctx.role == 'main' else None
    encoded = json.dumps({'actions': actions, 'snapshot': snapshot, 'scope': str(ctx.scope)}, ensure_ascii=False, sort_keys=True).encode()
    return {'scope': str(ctx.scope), 'actions': actions, 'plan_hash': hashlib.sha256(encoded).hexdigest(), 'issues': report['issues'], 'errors': [], 'unrepairable': [i for i in report['issues'] if i['severity'] == 'error' and not i['repairable']]}

def repair(ctx, apply=False, plan_hash=None):
    with locked(ctx.root) if apply else _nullcontext():
        plan = repair_plan(ctx)
        if not apply:
            return plan
        if not plan_hash or plan_hash != plan['plan_hash']:
            raise VaultError('修复计划发生变化或未提供 --if-plan-hash', 'conflict')
        if any(i['code'] in {'invalid_identity', 'unreadable_structure'} for i in plan['unrepairable']):
            raise VaultError('身份或结构无法可靠识别，拒绝应用修复')
        _, files = expected_content(ctx)
        created, saved, operations = [], {}, []
        store = Store(ctx)
        try:
            for action in plan['actions']:
                p = no_links(ctx.scope / action['path'])
                if action['kind'] == 'directory':
                    p.mkdir(parents=True, exist_ok=False)
                    created.append(p)
                elif action['kind'] == 'file':
                    operations.append(store.write(p, files[action['path']], 'repair-add', managed=True, create=True))
                    created.append(p)
                elif action['kind'] == 'derived':
                    saved[p] = p.read_bytes() if p.exists() else None
                    body = json.dumps({'schema_version': 1, 'directories': blueprint.sync_patterns(ctx.root)}, ensure_ascii=False, indent=2).encode()
                    operations.append(store.write(p, body, 'repair-derived', digest(p), managed=True, create=not p.exists()))
                elif action['kind'] == 'local_sync':
                    saved[p] = p.read_bytes()
                    blueprint.apply_sync_dirs(ctx.root)
            if not operations and created:
                directory, record = store.begin('repair-directories', created[0], False, managed=True, kind='directory')
                operations.append(store.finish(directory, record, created[0]))
            return {**plan, 'status': 'complete', 'operations': operations}
        except Exception:
            for p, content in saved.items():
                if content is None:
                    p.unlink(missing_ok=True)
                else:
                    atomic(p, content)
            for p in reversed(created):
                if p.is_file():
                    p.unlink()
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            raise

def agents_diff(service):
    rendered = service.agents_render()
    target = service.ctx.scope / 'AGENTS.md'
    existing = target.read_text(encoding='utf-8-sig') if target.exists() else ''
    diff = ''.join(difflib.unified_diff(existing.splitlines(True), rendered['content'].splitlines(True), fromfile='current/AGENTS.md', tofile='rendered/AGENTS.md'))
    return {'target': str(target), 'hash': rendered['hash'], 'changed': bool(diff), 'diff': diff}

from contextlib import nullcontext as _nullcontext
