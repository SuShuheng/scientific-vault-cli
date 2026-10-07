from pathlib import Path
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlencode
import fnmatch
import json
import os
import re
import yaml
from .context import Context, VaultError, identity, leaves, parts
from .storage import Store, atomic, digest, json_write, stamp
from . import blueprint
from .project_config import configure_project

MAIN_AGENT_TEMPLATE = Path('规则与模板/Agent模板/主库AGENTS.md.template')
PROJECT_AGENT_TEMPLATE = blueprint.TEMPLATE / 'AGENTS.md.template'
STATE = {'planning', 'active', 'paused', 'writing', 'complete', 'archived', 'example'}
MATERIAL_SUFFIXES = {'.pt', '.pth', '.ckpt', '.safetensors', '.nii', '.npy', '.npz', '.h5', '.hdf5', '.dcm', '.dicom', '.edf', '.bdf', '.fif', '.fdt', '.mha', '.mhd'}

def split_note(text):
    match = re.match(r'\A\ufeff?---\r?\n(.*?)\r?\n---\r?\n?', text, re.S)
    if not match:
        return {}, text
    try:
        properties = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        raise VaultError('笔记 YAML 属性无效')
    if not isinstance(properties, dict):
        raise VaultError('笔记属性必须是对象')
    return properties, text[match.end():]

def compose(properties, body):
    return '---\n' + yaml.safe_dump(properties, allow_unicode=True, sort_keys=False) + '---\n' + body.rstrip() + '\n'

def render_core(text, title):
    today = stamp()[:10]
    text = re.sub(r'\{\{date(?::YYYY-MM-DD)?\}\}', today, text)
    text = re.sub(r'\{\{time(?::HH:mm)?\}\}', stamp()[11:16], text)
    return text.replace('{{title}}', title)

def visible_files(scope, templates=False):
    for current, directories, names in os.walk(scope, followlinks=False):
        directories[:] = [d for d in directories if not d.startswith('.') and d not in {'工具', '维护日志'} and not (Path(current) / d).is_symlink() and not (hasattr(Path(current) / d, 'is_junction') and (Path(current) / d).is_junction())]
        if not templates:
            directories[:] = [d for d in directories if d not in {'90 模板', '笔记模板', '项目文件夹模板', 'Agent模板'}]
        for name in names:
            p = Path(current) / name
            if not name.startswith('.') and not p.is_symlink() and p.resolve().is_relative_to(scope.resolve()):
                yield p

class Service:
    def __init__(self, context):
        self.ctx = context
        self.store = Store(context)

    def note_get(self, value):
        path = self.ctx.path(value)
        if not path.is_file() or path.suffix != '.md':
            raise VaultError('目标 Markdown 笔记不存在')
        text = path.read_text(encoding='utf-8-sig')
        properties, body = split_note(text)
        return {'path': value, 'hash': digest(path), 'properties': properties, 'body': body}

    def note_list(self, query=None, kind=None):
        result = []
        for p in visible_files(self.ctx.scope):
            if p.suffix != '.md' or p.name in {'AGENTS.md', 'AGENTS.local.md'}:
                continue
            text = p.read_text(encoding='utf-8-sig')
            try:
                properties, _ = split_note(text)
            except VaultError:
                properties = {'invalid_frontmatter': True}
            if kind and properties.get('type') != kind:
                continue
            if query and query.casefold() not in text.casefold() and query.casefold() not in p.name.casefold():
                continue
            result.append({'path': p.relative_to(self.ctx.scope).as_posix(), 'hash': digest(p), 'properties': properties})
        return sorted(result, key=lambda r: r['path'])

    def validate_note(self, path, properties, body):
        for key in ('type', 'status', 'created'):
            if properties.get(key) in (None, ''):
                raise VaultError('笔记必须填写属性 ' + key)
        if not isinstance(properties['type'], str) or not isinstance(properties['status'], str):
            raise VaultError('type 和 status 必须是非空文本')
        if properties['type'] in {'project', 'maintenance'}:
            raise VaultError('项目身份与维护日志只能由专用命令生成')
        if 'project_id' not in properties:
            raise VaultError('笔记必须填写 project_id（主库共享内容使用空字符串）')
        expected = self.ctx.project_id or ''
        if properties['project_id'] != expected:
            raise VaultError('project_id 与当前作用域不一致', 'permission_denied')
        if self.ctx.project_id and not path.stem.startswith(self.ctx.project_id + '-'):
            raise VaultError('项目笔记文件名必须以项目编号加连字符开头')
        if self.ctx.project_id and properties.get('type') == 'knowledge':
            raise VaultError('通用知识应存主库，项目使用 application 类型并引用原件')
        if 'PROJECT_ID' in str(properties) or '{{' in str(properties):
            raise VaultError('正式记录存在未替换占位符')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(properties['created'])):
            raise VaultError('created 应为 YYYY-MM-DD')
        try:
            datetime.strptime(str(properties['created']), '%Y-%m-%d')
        except ValueError:
            raise VaultError('created 不是有效日期')
        for key, kind in [('run_id', 'experiment'), ('run_id', 'result'), ('handoff_id', 'handoff')]:
            if properties['type'] == kind and not properties.get(key):
                raise VaultError(kind + ' 记录必须填写 ' + key)
        if properties['type'] == 'handoff' and properties['status'] == 'complete':
            outputs = properties.get('outputs')
            if not isinstance(outputs, list) or not outputs:
                raise VaultError('完成交接必须提供 outputs 属性，列出已整理笔记的相对路径')
            if not all(self.ctx.path(p).is_file() for p in outputs):
                raise VaultError('交接整理产出文件未找到')
        if re.search(r'eyJhbGciOi[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.', body + str(properties)):
            raise VaultError('正文包含疑似 JWT 凭据，请移除后写入')

    def unique_name(self, path):
        # Notes intentionally use globally unique names across parent/leaf contexts.
        collisions = [p for p in visible_files(self.ctx.root) if p != path and p.name.casefold() == path.name.casefold()]
        if collisions:
            raise VaultError('笔记名称在全库已存在，请使用唯一名称', 'conflict')

    def note_add(self, value, template=None, text=None, properties=None, kind=None, status='draft'):
        path = self.ctx.path(value, mutate=True)
        if path.suffix != '.md':
            raise VaultError('笔记必须使用 .md 扩展名')
        if path.exists():
            raise VaultError('目标已经存在', 'conflict')
        self.unique_name(path)
        if template:
            source = self.template_path(template)
            if source.suffix != '.md':
                raise VaultError('新增笔记需要 Markdown 笔记模板')
            raw = render_core(source.read_text(encoding='utf-8-sig'), path.stem).replace('PROJECT_ID', self.ctx.project_id or '')
            data, body = split_note(raw)
            if text:
                body += '\n' + text
        else:
            data, body = split_note(text or '# ' + path.stem + '\n')
            data.setdefault('type', kind or 'note')
            data.setdefault('status', status)
            data.setdefault('created', stamp()[:10])
            data.setdefault('project_id', self.ctx.project_id or '')
        data.update(properties or {})
        self.validate_note(path, data, body)
        return self.store.write(path, compose(data, body).encode(), 'note-add', create=True)

    def note_edit(self, value, properties=None, append=None, replacement=None, if_hash=None, authorized=False):
        path = self.ctx.path(value, mutate=True)
        current = self.note_get(value)
        if not if_hash or if_hash != current['hash']:
            raise VaultError('请提供 note get 返回的当前 --if-hash', 'conflict')
        data, body = current['properties'], current['body']
        if not data:
            raise VaultError('无属性的库级说明文档请用 file edit，科研笔记使用 note edit')
        for key in ('type', 'project_id', 'created'):
            if properties and key in properties and properties[key] != data.get(key):
                raise VaultError('不通过普通编辑改变记录身份属性')
        data.update(properties or {})
        if replacement is not None:
            if not authorized:
                raise VaultError('全文替换需已有用户授权，并显式传入 --authorized-replace')
            body = replacement
        if append is not None:
            body = body.rstrip() + '\n\n' + append
        if replacement is None and append is None and not properties:
            raise VaultError('没有指定要修改的内容')
        self.validate_note(path, data, body)
        return self.store.write(path, compose(data, body).encode(), 'note-replace' if replacement is not None else 'note-edit', if_hash)

    def guard_tree(self, path):
        for p in path.rglob('*') if path.is_dir() else [path]:
            if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
                raise VaultError('目录包含链接或联接，拒绝递归操作', 'permission_denied')
            if any(x.startswith('.') or x in {'原始材料', '维护日志'} for x in p.relative_to(path.parent).parts) or p.name in {'AGENTS.md', '项目.json'}:
                raise VaultError('目录包含受保护内容，请使用对应专用命令', 'permission_denied')

    def file_delete(self, value, if_hash=None):
        path = self.ctx.path(value, mutate=True)
        self.guard_tree(path)
        return self.store.delete(path, if_hash, kind='directory' if path.is_dir() else 'file')

    def file_add(self, value, source):
        path = self.ctx.path(value, mutate=True)
        source = Path(source)
        if not source.is_file():
            raise VaultError('源文件不存在')
        if path.suffix == '.md':
            raise VaultError('Markdown 内容请用 note add，确保属性正确')
        if path.suffix.casefold() in MATERIAL_SUFFIXES or path.name.casefold().endswith('.nii.gz'):
            raise VaultError('原始数据和权重应留在库外')
        if source.stat().st_size > 50 * 1024 * 1024:
            raise VaultError('此版本只接收不超过 50 MiB 的笔记附件，实验物料存库外')
        if '.obsidian' in source.resolve().parts or source.name.startswith('.'):
            raise VaultError('不从隐藏配置目录导入附件', 'permission_denied')
        if re.search(rb'eyJhbGciOi[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.', source.read_bytes()):
            raise VaultError('附件包含疑似 JWT 凭据')
        return self.store.write(path, source.read_bytes(), 'file-add', create=True)

    def file_edit(self, value, text, if_hash):
        path = self.ctx.path(value, mutate=True)
        if path.suffix not in {'.md', '.txt', '.json', '.csv', '.tsv', '.log', '.yaml', '.yml'}:
            raise VaultError('file edit 只支持文本资产')
        if path.suffix == '.md' and split_note(path.read_text(encoding='utf-8-sig'))[0]:
            raise VaultError('有属性的科研记录使用 note edit，保留身份与证据约束')
        return self.store.write(path, text.encode(), 'file-edit', if_hash)

    def folder_add(self, value):
        path = self.ctx.path(value, mutate=True)
        if path.exists():
            raise VaultError('目录已存在', 'conflict')
        directory, record = self.store.begin('folder-add', path, False, kind='directory')
        try:
            path.mkdir(parents=True, exist_ok=False)
            return self.store.finish(directory, record, path)
        except Exception:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
            record['status'] = 'failed'
            json_write(directory / 'record.json', record)
            raise

    def folder_list(self, value=None):
        path = self.ctx.path(value) if value else self.ctx.scope
        if not path.is_dir():
            raise VaultError('目录不存在')
        return [{'name': p.name, 'kind': 'directory' if p.is_dir() else 'file'} for p in sorted(path.iterdir()) if not p.name.startswith('.') and not p.is_symlink()]

    def project_list(self):
        vaults = [self.ctx.scope] if self.ctx.role == 'project' else leaves(self.ctx.root)
        result = []
        for vault in vaults:
            project_id = identity(vault)
            home = vault / (project_id + '-项目主页.md')
            metadata, _ = split_note(home.read_text(encoding='utf-8-sig'))
            result.append({'project_id': project_id, 'name': vault.name, 'path': vault.relative_to(self.ctx.root).as_posix(), 'status': metadata.get('status')})
        return result

    def project_create(self, category, name, project_id):
        self.ctx.require_main()
        path = blueprint.create_project(self.ctx.root, category, name, project_id, lambda dst, pid: configure_project(self.ctx.root, dst, pid))
        main_store = Store(replace(self.ctx, scope=self.ctx.root, project_id=None))
        directory, record = main_store.begin('project-create', path, False, managed=True, kind='project')
        result = main_store.finish(directory, record, path)
        result['project_id'] = project_id
        return result

    def project_status(self, value, if_hash):
        if not self.ctx.project_id:
            raise VaultError('请先用 --project 选定项目')
        if value not in STATE:
            raise VaultError('项目状态不支持')
        path = self.ctx.scope / (self.ctx.project_id + '-项目主页.md')
        metadata, body = split_note(path.read_text(encoding='utf-8-sig'))
        metadata['status'] = value
        return self.store.write(path, compose(metadata, body).encode(), 'project-status', if_hash, managed=True)

    def project_edit(self, properties, append, replacement, if_hash, authorized=False):
        if not self.ctx.project_id:
            raise VaultError('请先选定当前项目')
        path = self.ctx.scope / (self.ctx.project_id + '-项目主页.md')
        metadata, body = split_note(path.read_text(encoding='utf-8-sig'))
        if any(k in properties for k in ('type', 'project_id', 'created', 'vault_name', 'domain')):
            raise VaultError('项目身份、目录名称和分类不通过普通主页编辑改变')
        if properties.get('status', metadata.get('status')) not in STATE:
            raise VaultError('项目状态不支持')
        metadata.update(properties)
        if replacement is not None:
            if not authorized:
                raise VaultError('替换项目主页正文须已有用户授权并传 --authorized-replace')
            body = replacement
        if append:
            body = body.rstrip() + '\n\n' + append
        if not properties and append is None and replacement is None:
            raise VaultError('没有指定项目更新内容')
        return self.store.write(path, compose(metadata, body).encode(), 'project-edit', if_hash, managed=True)

    def index_path(self, name):
        if len(parts(name)) != 1:
            raise VaultError('索引名称必须为单层文件名')
        base = '95 索引' if self.ctx.project_id else '索引'
        return self.ctx.path(base + '/' + (name if name.endswith('.base') else name + '.base'))

    def index_set(self, name, text, if_hash):
        path = self.index_path(name)
        if not path.is_file():
            raise VaultError('只修改当前已有索引，新项目索引由目录模板生成')
        try:
            data = yaml.safe_load(text)
            if not isinstance(data, dict) or not isinstance(data.get('views'), list) or not data['views']:
                raise ValueError()
        except (yaml.YAMLError, ValueError):
            raise VaultError('索引 YAML 或 views 无效')
        if self.ctx.project_id and f'note.project_id == "{self.ctx.project_id}"' not in data.get('filters', {}).get('and', []):
            raise VaultError('项目索引必须保留当前项目编号的 AND 过滤')
        return self.store.write(path, text.encode(), 'index-set', if_hash, managed=True)

    def refresh_sync(self, apply=True):
        self.ctx.require_main()
        data = {'schema_version': 1, 'directories': blueprint.sync_patterns(self.ctx.root)}
        blueprint.atomic_json(self.ctx.root / blueprint.SYNC_LIST, data)
        if apply:
            blueprint.apply_sync_dirs(self.ctx.root)
        return data

    def sync_maintain(self, rebuild=False):
        self.ctx.require_main()
        main_context = replace(self.ctx, scope=self.ctx.root, project_id=None)
        store = Store(main_context)
        target = self.ctx.root / blueprint.SYNC_LIST
        if rebuild:
            data = {'schema_version': 1, 'directories': blueprint.sync_patterns(self.ctx.root)}
            text = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
            result = store.write(target, text, 'sync-rebuild', digest(target), managed=True, create=not target.exists())
        else:
            directory, record = store.begin('sync-apply', target, False, managed=True)
            result = None
        try:
            count = blueprint.apply_sync_dirs(self.ctx.root)
        except Exception as exc:
            raise VaultError('共享清单已保留，但本机目录应用失败；修复后重试 sync apply：' + str(exc))
        if not rebuild:
            result = store.finish(directory, record, target)
        result['directories'] = count
        return result

    def project_delete(self, project_id):
        self.ctx.require_main()
        target_ctx = self.ctx.select(project_id)
        main_ctx = replace(self.ctx, scope=self.ctx.root, project_id=None)
        result = Store(main_ctx).delete(target_ctx.scope, managed=True, kind='project')
        try:
            self.refresh_sync()
        except Exception:
            Store(main_ctx).restore(result['operation_id'], lambda r: self.ctx.root.joinpath(*parts(r['target'])))
            self.refresh_sync()
            raise
        return result

    def restore_resolver(self, record):
        if record['kind'] == 'project':
            self.ctx.require_main()
            if self.ctx.scope != self.ctx.root:
                raise VaultError('整个项目从主库回收站恢复')
            target = self.ctx.root.joinpath(*parts(record['target']))
            if not target.resolve().is_relative_to((self.ctx.root / '科研项目').resolve()):
                raise VaultError('项目回收记录路径无效', 'permission_denied')
            for ancestor in target.parents:
                if ancestor == self.ctx.root:
                    break
                if (ancestor / '.obsidian').exists():
                    raise VaultError('恢复位置已经位于其他项目内部', 'permission_denied')
            payload = self.store.record_path(record['id']).parent / 'payload'
            if payload.exists():
                restored_id = identity(payload)
                for vault in leaves(self.ctx.root):
                    if identity(vault).casefold() == restored_id.casefold() or vault.name.casefold() == target.name.casefold():
                        raise VaultError('恢复项目的编号或名称与现有项目冲突', 'conflict')
            return target
        target = self.ctx.path(record['target'], mutate=True, managed=record.get('managed', False))
        if target.suffix == '.md' and not record.get('managed'):
            self.unique_name(target)
        return target

    def restore(self, operation_id):
        result = self.store.restore(operation_id, self.restore_resolver)
        record = json.loads(self.store.record_path(operation_id).read_text(encoding='utf-8'))
        if record['kind'] == 'project':
            try:
                self.refresh_sync()
            except Exception as exc:
                result['warning'] = '项目已恢复；请运行 sync rebuild 修复同步登记：' + str(exc)
        return result

    def template_path(self, name):
        base = self.ctx.scope / ('90 模板' if self.ctx.project_id else '规则与模板/笔记模板')
        if len(parts(name)) != 1:
            raise VaultError('模板名称必须是单层文件名')
        path = base / (name if name.endswith('.md') else name + '.md')
        if not path.resolve().is_relative_to(base.resolve()):
            raise VaultError('模板路径越界', 'permission_denied')
        if not path.is_file():
            raise VaultError('模板不存在')
        return path

    def template_list(self):
        base = self.ctx.scope / ('90 模板' if self.ctx.project_id else '规则与模板/笔记模板')
        return [{'name': p.stem, 'hash': digest(p)} for p in sorted(base.glob('*.md')) if not p.is_symlink()]

    def template_set(self, name, text, if_hash=None, create=False):
        self.ctx.require_main()
        base = self.ctx.scope / ('90 模板' if self.ctx.project_id else '规则与模板/笔记模板')
        if len(parts(name)) != 1:
            raise VaultError('模板名称必须是单层文件名')
        path = base / (name if name.endswith('.md') else name + '.md')
        preview = render_core(text, '示例').replace('PROJECT_ID', self.ctx.project_id or '')
        split_note(preview)
        if 'eyJhbGciOi' in text:
            raise VaultError('模板不得包含同步凭据')
        return self.store.write(path, text.encode(), 'template-set', if_hash, managed=True, create=create)

    def template_delete(self, name):
        self.ctx.require_main()
        return self.store.delete(self.template_path(name), managed=True)

    def agent_template(self, kind):
        if kind not in {'main', 'project'}:
            raise VaultError('Agent 模板类型应为 main 或 project')
        return self.ctx.root / (MAIN_AGENT_TEMPLATE if kind == 'main' else PROJECT_AGENT_TEMPLATE)

    def agents_render(self):
        kind = 'project' if self.ctx.project_id else 'main'
        source = self.agent_template(kind)
        if not source.is_file():
            raise VaultError('Agent 模板不存在')
        text = source.read_text(encoding='utf-8-sig')
        if kind == 'project':
            relative = '/'.join('..' for _ in self.ctx.scope.relative_to(self.ctx.root).parts)
            text = blueprint.render(text, {'PROJECT_ID': self.ctx.project_id, 'PROJECT_NAME': self.ctx.scope.name, 'MAIN_VAULT_RELATIVE': relative})
        local = self.ctx.scope / 'AGENTS.local.md'
        if local.is_file():
            text += '\n\n## 本库补充规则\n\n补充规则不得放宽主库约束。\n\n' + local.read_text(encoding='utf-8-sig')
        target = self.ctx.scope / 'AGENTS.md'
        return {'target': str(target), 'hash': digest(target), 'content': text}

    def agents_apply(self, if_hash):
        rendered = self.agents_render()
        return self.store.write(self.ctx.scope / 'AGENTS.md', rendered['content'].encode(), 'agents-apply', if_hash, managed=True, create=not (self.ctx.scope / 'AGENTS.md').exists())

    def agents_customize(self, text, if_hash=None):
        path = self.ctx.scope / 'AGENTS.local.md'
        if 'eyJhbGciOi' in text:
            raise VaultError('规则不得包含凭据')
        return self.store.write(path, text.encode(), 'agents-customize', if_hash, managed=True, create=not path.exists())

    def agent_template_set(self, kind, text, if_hash):
        self.ctx.require_main()
        main_ctx = replace(self.ctx, scope=self.ctx.root, project_id=None)
        path = self.agent_template(kind)
        if not text.strip() or 'eyJhbGciOi' in text:
            raise VaultError('Agent 模板不能为空或包含凭据')
        return Store(main_ctx).write(path, text.encode(), 'agents-template-set', if_hash, managed=True, create=not path.exists())

    def shared(self, query=None, path=None):
        scope = self.ctx.root / '共享知识'
        if path:
            target = scope.joinpath(*parts(path))
            if not target.resolve().is_relative_to(scope.resolve()) or any(x.startswith('.') for x in parts(path)):
                raise VaultError('共享知识路径越界', 'permission_denied')
            if not target.is_file() or target.suffix != '.md':
                raise VaultError('共享知识笔记不存在')
            return {'path': path, 'content': target.read_text(encoding='utf-8-sig'), 'uri': 'obsidian://open?' + urlencode({'vault': 'scientific_notes', 'file': target.relative_to(self.ctx.root).as_posix().removesuffix('.md')})}
        return [{'path': p.relative_to(scope).as_posix()} for p in visible_files(scope) if p.suffix == '.md' and (not query or query.casefold() in p.read_text(encoding='utf-8-sig').casefold())]

    def scoped_check(self):
        if self.ctx.role == 'main':
            from .checks import check
            return check(self.ctx.root)
        errors = []
        for record in self.note_list():
            if record['properties'].get('type') in {'project'} or not record['properties']:
                continue
            p = self.ctx.path(record['path'])
            try:
                self.validate_note(p, record['properties'], self.note_get(record['path'])['body'])
            except VaultError as exc:
                errors.append({'path': record['path'], 'error': str(exc)})
        sync = self.ctx.scope / '.obsidian/plugins/fast-note-sync/data.json'
        if sync.exists():
            settings = json.loads(sync.read_text(encoding='utf-8-sig'))
            if settings.get('syncEnabled') or settings.get('apiToken'):
                errors.append({'error': '子库独立同步未关闭或包含同步凭据'})
        if len([v for v in leaves(self.ctx.root) if v.is_relative_to(self.ctx.scope)]) != 1:
            errors.append({'error': '叶级 Vault 边界无效'})
        return {'scope': str(self.ctx.scope), 'errors': errors, 'warnings': []}
