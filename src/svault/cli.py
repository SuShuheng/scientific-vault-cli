import argparse
from dataclasses import replace
from pathlib import Path
import json
import sys
import yaml
from . import __version__, blueprint
from .context import Context, VaultError, parts, main_vault
from .storage import locked, digest
from .service import Service, render_core
from .capabilities import operations
from . import updater

READ_ONLY = {'whoami', 'permissions', 'operations', 'rules', 'check'}

def input_text(path):
    source = Path(path)
    if not source.is_file():
        raise VaultError('输入文件不存在')
    if '.obsidian' in source.resolve().parts or source.name.startswith('.'):
        raise VaultError('不能把隐藏配置文件当作正文输入', 'permission_denied')
    return source.read_text(encoding='utf-8-sig')

def fields(values):
    result = {}
    for value in values or []:
        key, sep, text = value.partition('=')
        if not sep or not key or key in {'apiToken', 'api'}:
            raise VaultError('--set 必须为属性=值，不能设置连接凭据')
        result[key] = yaml.safe_load(text) if text else ''
    return result

def parser():
    p = argparse.ArgumentParser(prog='svault', description='两级科研 Vault 维护 CLI；不调用 AI，不执行外部实验')
    p.add_argument('--vault', help='从库外执行时指定主库或子库路径；不提升当前子库权限')
    p.add_argument('--project', help='主库 Agent 选择项目编号；子库 Agent 只能选择自身')
    p.add_argument('--json', action='store_true', help='保留结构化 JSON 输出（默认即为 JSON）')
    p.add_argument('--version', action='version', version='svault ' + __version__)
    commands = p.add_subparsers(dest='group', required=True)
    for name in ('whoami', 'permissions', 'operations', 'rules', 'check', 'version'):
        commands.add_parser(name)
    def group(name, names):
        subs = commands.add_parser(name).add_subparsers(dest='action', required=True)
        return {n: subs.add_parser(n) for n in names}
    note = group('note', ['add', 'get', 'find', 'list', 'edit', 'delete'])
    for name in ('add', 'get', 'edit', 'delete'):
        note[name].add_argument('path')
    note['add'].add_argument('--template')
    note['add'].add_argument('--body-file')
    note['add'].add_argument('--text')
    note['add'].add_argument('--type')
    note['add'].add_argument('--status', default='draft')
    note['add'].add_argument('--set', action='append')
    for name in ('find', 'list'):
        note[name].add_argument('--type')
    note['list'].add_argument('--query')
    note['find'].add_argument('query')
    note['edit'].add_argument('--if-hash', required=True)
    note['edit'].add_argument('--set', action='append')
    note['edit'].add_argument('--append-file')
    note['edit'].add_argument('--append-text')
    note['edit'].add_argument('--replace-body-file')
    note['edit'].add_argument('--authorized-replace', action='store_true')
    note['delete'].add_argument('--if-hash')
    file = group('file', ['add', 'get', 'edit', 'delete', 'list'])
    for name in ('add', 'get', 'edit', 'delete'):
        file[name].add_argument('path')
    file['add'].add_argument('--source', required=True)
    file['get'].add_argument('--text', action='store_true')
    file['edit'].add_argument('--body-file', required=True)
    file['edit'].add_argument('--if-hash', required=True)
    file['edit'].add_argument('--authorized-replace', action='store_true', required=True)
    file['delete'].add_argument('--if-hash')
    file['list'].add_argument('path', nargs='?')
    folder = group('folder', ['add', 'list', 'delete'])
    folder['add'].add_argument('path')
    folder['list'].add_argument('path', nargs='?')
    folder['delete'].add_argument('path')
    project = group('project', ['create', 'list', 'get', 'status', 'edit', 'delete'])
    project['create'].add_argument('--category', required=True)
    project['create'].add_argument('--name', required=True)
    project['create'].add_argument('--id', required=True)
    project['get'].add_argument('id', nargs='?')
    project['status'].add_argument('status')
    project['status'].add_argument('--if-hash', required=True)
    project['delete'].add_argument('id')
    project['edit'].add_argument('--if-hash', required=True)
    project['edit'].add_argument('--set', action='append')
    project['edit'].add_argument('--append-file')
    project['edit'].add_argument('--replace-body-file')
    project['edit'].add_argument('--authorized-replace', action='store_true')
    index = group('index', ['list', 'get', 'set'])
    for name in ('get', 'set'):
        index[name].add_argument('name')
    index['set'].add_argument('--body-file', required=True)
    index['set'].add_argument('--if-hash', required=True)
    for name, actions in [('trash', ['list', 'get', 'restore']), ('history', ['list', 'get', 'undo'])]:
        sub = group(name, actions)
        for action in actions:
            if action != 'list':
                sub[action].add_argument('id')
    template = group('template', ['list', 'get', 'render', 'add', 'set', 'delete'])
    for name in ('get', 'render', 'add', 'set', 'delete'):
        template[name].add_argument('name')
    template['render'].add_argument('--title', default='新笔记')
    for name in ('add', 'set'):
        template[name].add_argument('--body-file', required=True)
    template['set'].add_argument('--if-hash', required=True)
    agents = group('agents', ['show', 'render', 'apply', 'customize', 'template-get', 'template-set'])
    agents['show'].add_argument('--local', action='store_true')
    agents['apply'].add_argument('--if-hash')
    agents['customize'].add_argument('--body-file', required=True)
    agents['customize'].add_argument('--if-hash')
    for name in ('template-get', 'template-set'):
        agents[name].add_argument('kind', choices=['main', 'project'])
    agents['template-set'].add_argument('--body-file', required=True)
    agents['template-set'].add_argument('--if-hash')
    shared = group('shared', ['list', 'get', 'find'])
    shared['get'].add_argument('path')
    shared['find'].add_argument('query')
    group('sync', ['show', 'rebuild', 'apply'])
    bp = group('blueprint', ['show', 'validate', 'set-file'])
    bp['set-file'].add_argument('path')
    bp['set-file'].add_argument('--body-file', required=True)
    bp['set-file'].add_argument('--if-hash', required=True)
    update = group('update', ['check', 'apply', 'status'])
    for name in ('check', 'apply'):
        update[name].add_argument('--repo', default=updater.REPOSITORY)
        update[name].add_argument('--tag')
    update['apply'].add_argument('--force', action='store_true', help='允许重新安装当前同版本，不允许降级')
    return p

def dispatch(args, context):
    service = Service(context)
    g, a = args.group, getattr(args, 'action', None)
    if g == 'whoami':
        return context.info()
    if g in ('permissions', 'operations'):
        return {'context': context.info(), 'operations': operations(), 'boundary': 'CLI 工作目录和路径校验是操作约束；不替代操作系统权限或 Agent 身份认证。'}
    if g == 'rules':
        result = {'main_agents': (context.root / 'AGENTS.md').read_text(encoding='utf-8-sig'), 'main_rules': (context.root / '规则与模板/科研库规则.md').read_text(encoding='utf-8-sig')}
        if context.project_id:
            result['project_agents'] = (context.scope / 'AGENTS.md').read_text(encoding='utf-8-sig')
        return result
    if g == 'check':
        return service.scoped_check()
    if g == 'note':
        if a == 'get':
            return service.note_get(args.path)
        if a in ('list', 'find'):
            return service.note_list(args.query, args.type)
        if a == 'add':
            return service.note_add(args.path, args.template, input_text(args.body_file) if args.body_file else args.text, fields(args.set), args.type, args.status)
        if a == 'edit':
            append = input_text(args.append_file) if args.append_file else args.append_text
            replacement = input_text(args.replace_body_file) if args.replace_body_file else None
            return service.note_edit(args.path, fields(args.set), append, replacement, args.if_hash, args.authorized_replace)
        return service.file_delete(args.path, args.if_hash)
    if g == 'file':
        if a == 'add':
            return service.file_add(args.path, args.source)
        if a == 'get':
            path = context.path(args.path)
            if not path.is_file():
                raise VaultError('文件不存在')
            result = {'path': args.path, 'size': path.stat().st_size, 'hash': digest(path)}
            if args.text:
                result['content'] = path.read_text(encoding='utf-8-sig')
            return result
        if a == 'edit':
            return service.file_edit(args.path, input_text(args.body_file), args.if_hash)
        if a == 'delete':
            return service.file_delete(args.path, args.if_hash)
        return service.folder_list(args.path)
    if g == 'folder':
        if a == 'add':
            return service.folder_add(args.path)
        if a == 'delete':
            return service.file_delete(args.path)
        return service.folder_list(args.path)
    if g == 'project':
        if a == 'list':
            return service.project_list()
        if a == 'create':
            return service.project_create(args.category, args.name, args.id)
        if a == 'status':
            return service.project_status(args.status, args.if_hash)
        if a == 'edit':
            return service.project_edit(fields(args.set), input_text(args.append_file) if args.append_file else None, input_text(args.replace_body_file) if args.replace_body_file else None, args.if_hash, args.authorized_replace)
        if a == 'delete':
            return service.project_delete(args.id)
        selected = Service(context.select(args.id)) if args.id else service
        if not selected.ctx.project_id:
            raise VaultError('请指定项目编号')
        return selected.note_get(selected.ctx.project_id + '-项目主页.md')
    if g in ('trash', 'history'):
        if a == 'list':
            return service.store.records('delete' if g == 'trash' else None)
        if a == 'get':
            return json.loads(service.store.record_path(args.id).read_text(encoding='utf-8'))
        if a == 'restore':
            return service.restore(args.id)
        return service.store.undo(args.id, service.restore_resolver)
    if g == 'template':
        if a == 'list':
            return service.template_list()
        if a == 'get':
            path = service.template_path(args.name)
            return {'name': args.name, 'content': path.read_text(encoding='utf-8-sig'), 'hash': digest(path)}
        if a == 'render':
            path = service.template_path(args.name)
            return {'content': render_core(path.read_text(encoding='utf-8-sig'), args.title).replace('PROJECT_ID', context.project_id or '')}
        if a in ('set', 'add'):
            return service.template_set(args.name, input_text(args.body_file), getattr(args, 'if_hash', None), a == 'add')
        return service.template_delete(args.name)
    if g == 'agents':
        if a == 'show':
            path = context.scope / ('AGENTS.local.md' if args.local else 'AGENTS.md')
            return {'path': str(path), 'hash': digest(path), 'content': path.read_text(encoding='utf-8-sig') if path.exists() else ''}
        if a == 'render':
            return service.agents_render()
        if a == 'apply':
            return service.agents_apply(args.if_hash)
        if a == 'customize':
            return service.agents_customize(input_text(args.body_file), args.if_hash)
        if a == 'template-get':
            path = service.agent_template(args.kind)
            return {'kind': args.kind, 'content': path.read_text(encoding='utf-8-sig'), 'hash': digest(path)}
        return service.agent_template_set(args.kind, input_text(args.body_file), args.if_hash)
    if g == 'shared':
        return service.shared(getattr(args, 'query', None), getattr(args, 'path', None))
    if g == 'index':
        if a == 'list':
            base = context.scope / ('95 索引' if context.project_id else '索引')
            return [{'name': p.stem, 'hash': digest(p)} for p in sorted(base.glob('*.base'))]
        path = service.index_path(args.name)
        if a == 'get':
            return {'name': args.name, 'hash': digest(path), 'content': path.read_text(encoding='utf-8-sig')}
        return service.index_set(args.name, input_text(args.body_file), args.if_hash)
    if g == 'sync':
        context.require_main()
        if a == 'show':
            return blueprint.load(context.root / blueprint.SYNC_LIST, {})
        if a == 'rebuild':
            return service.sync_maintain(rebuild=True)
        return service.sync_maintain()
    if g == 'blueprint':
        if a == 'show':
            return blueprint.load(context.root / blueprint.TEMPLATE / 'template.json')
        if a == 'validate':
            blueprint.validate_blueprint(context.root)
            return {'valid': True}
        context.require_main()
        base = context.root / blueprint.TEMPLATE
        path = base.joinpath(*parts(args.path))
        if not path.resolve().is_relative_to(base.resolve()) or path.is_symlink() or not path.is_file():
            raise VaultError('只能修改蓝图内现有的文件源', 'permission_denied')
        text = input_text(args.body_file)
        if 'eyJhbGciOi' in text:
            raise VaultError('蓝图不得包含凭据')
        main_service = Service(replace(context, scope=context.root, project_id=None))
        result = main_service.store.write(path, text.encode(), 'blueprint-set', args.if_hash, managed=True)
        try:
            blueprint.validate_blueprint(context.root)
        except Exception:
            main_service.store.undo(result['operation_id'], main_service.restore_resolver)
            raise VaultError('修改后的蓝图无效，已回滚')
        return result
    raise VaultError('未实现命令')

def is_mutation(args):
    return getattr(args, 'action', '') in {'add', 'edit', 'delete', 'create', 'status', 'restore', 'undo', 'set', 'apply', 'customize', 'template-set', 'set-file', 'rebuild'}

def run(argv=None, cwd=None):
    args = parser().parse_args(argv)
    if args.group == 'version':
        return updater.version_info()
    if args.group == 'update':
        if args.action == 'check':
            return updater.check_update(args.repo, args.tag)
        if args.action == 'status':
            return updater.update_status()
        if main_vault(Path(cwd or Path.cwd())) or args.vault:
            Context.resolve(cwd, args.vault, args.project).require_main()
        return updater.apply_update(args.repo, args.tag, args.force)
    context = Context.resolve(cwd, args.vault, args.project)
    if is_mutation(args):
        with locked(context.root):
            return dispatch(args, context)
    return dispatch(args, context)

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        result = run(argv)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2, default=str))
        if isinstance(result, dict) and result.get('errors'):
            raise SystemExit(2)
    except VaultError as exc:
        print(json.dumps({'ok': False, 'error': {'code': exc.code, 'message': str(exc)}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except yaml.YAMLError:
        print(json.dumps({'ok': False, 'error': {'code': 'invalid_yaml', 'message': '输入或项目身份中的 YAML 格式无效'}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # Never include parsed configuration contents or stack locals in output.
        print(json.dumps({'ok': False, 'error': {'code': 'operation_failed', 'message': str(exc)}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
