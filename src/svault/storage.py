from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import uuid
import yaml
from .context import VaultError, parts

def stamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.svault-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()

def json_write(path, data):
    atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

@contextmanager
def locked(root):
    key = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / ('svault-' + key + '.lock')
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise VaultError('另一维护命令正在执行；若进程已终止，请先核实系统临时目录中的遗留锁', 'busy')
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)

class Store:
    def __init__(self, context):
        self.ctx = context
        self.scope = context.scope
        self.directory = self.scope / '.trash/svault'
        if not self.directory.resolve().is_relative_to(self.scope.resolve()):
            raise VaultError('回收站通过链接越出当前作用域', 'permission_denied')
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in (self.scope / '.trash', self.directory)):
            raise VaultError('不能将符号链接或目录联接作为回收站', 'permission_denied')

    def safe_target(self, path):
        if not path.resolve().is_relative_to(self.scope.resolve()):
            raise VaultError('目标越出记录所属作用域', 'permission_denied')
        current = path
        while current != self.scope:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise VaultError('目标包含符号链接或目录联接', 'permission_denied')
            current = current.parent

    def record_path(self, operation_id):
        if not re_id(operation_id):
            raise VaultError('操作编号无效')
        p = self.directory / operation_id / 'record.json'
        self.safe_target(p)
        if not p.resolve().is_relative_to(self.directory.resolve()):
            raise VaultError('记录路径越界', 'permission_denied')
        if not p.is_file():
            raise VaultError('当前作用域内找不到此操作编号')
        return p

    def records(self, action=None):
        result = []
        if not self.directory.exists():
            return result
        for p in self.directory.glob('*/record.json'):
            self.safe_target(p)
            record = json.loads(p.read_text(encoding='utf-8'))
            if not action or record['action'] == action:
                result.append(record)
        return sorted(result, key=lambda r: r['created'], reverse=True)

    def log(self, record):
        frontmatter = {'type': 'maintenance', 'project_id': self.ctx.project_id or '', 'status': record['status'], 'created': record['created'][:10], 'operation_id': record['id'], 'action': record['action'], 'target': record['target']}
        text = '---\n' + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + '---\n# 维护操作 ' + record['id'] + '\n\n'
        text += f"- 操作：{record['action']}\n- 目标：`{record['target']}`\n- 身份：{self.ctx.role}\n- 时间：{record['created']}\n- 状态：{record['status']}\n"
        text += '\n恢复和回滚通过 svault 的 trash/history 命令进行。日志不保存笔记正文或配置凭据。\n'
        target = self.scope / '维护日志' / (record['id'] + '.md')
        self.safe_target(target)
        atomic(target, text.encode())

    def begin(self, action, target, original, managed=False, kind='file'):
        self.safe_target(target)
        operation_id = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
        directory = self.directory / operation_id
        directory.mkdir(parents=True, exist_ok=False)
        record = {'id': operation_id, 'action': action, 'target': target.relative_to(self.scope).as_posix(), 'created': stamp(), 'status': 'pending', 'managed': managed, 'kind': kind, 'before_hash': digest(target) if original else None}
        json_write(directory / 'record.json', record)
        if original and target.is_file():
            shutil.copy2(target, directory / 'before')
        return directory, record

    def finish(self, directory, record, target):
        record.update(status='complete', after_hash=digest(target))
        json_write(directory / 'record.json', record)
        self.log(record)
        return {'operation_id': record['id'], 'path': str(target), 'hash': record.get('after_hash'), 'action': record['action']}

    def write(self, path, content, action, if_hash=None, managed=False, create=False):
        exists = path.exists()
        if create and exists:
            raise VaultError('目标已经存在，拒绝覆盖', 'conflict')
        if exists and not path.is_file():
            raise VaultError('目标不是文件')
        if exists and (not if_hash or digest(path) != if_hash):
            raise VaultError('修改需要当前 SHA256；文件已经变化或未提供 --if-hash', 'conflict')
        directory, record = self.begin(action, path, exists, managed)
        try:
            atomic(path, content)
            return self.finish(directory, record, path)
        except Exception:
            if exists and (directory / 'before').exists():
                atomic(path, (directory / 'before').read_bytes())
            elif path.exists():
                path.unlink()
            record['status'] = 'failed'
            json_write(directory / 'record.json', record)
            raise

    def delete(self, path, if_hash=None, managed=False, kind='file'):
        if not path.exists():
            raise VaultError('目标不存在')
        if path.is_file() and if_hash and digest(path) != if_hash:
            raise VaultError('文件已变化，拒绝删除', 'conflict')
        directory, record = self.begin('delete', path, False, managed, kind)
        try:
            path.rename(directory / 'payload')
            self.finish(directory, record, path)
            return {'operation_id': record['id'], 'original': record['target'], 'trash': str(directory / 'payload')}
        except Exception:
            if (directory / 'payload').exists() and not path.exists():
                (directory / 'payload').rename(path)
            record['status'] = 'failed'
            json_write(directory / 'record.json', record)
            raise

    def restore(self, operation_id, path_resolver):
        p = self.record_path(operation_id)
        record = json.loads(p.read_text(encoding='utf-8'))
        payload = p.parent / 'payload'
        self.safe_target(payload)
        if record['action'] != 'delete' or record['status'] != 'complete' or not payload.exists():
            raise VaultError('此操作没有可恢复的删除原件')
        target = path_resolver(record)
        self.safe_target(target)
        if target.exists():
            raise VaultError('原位置已经有内容，拒绝覆盖；请先处理冲突', 'conflict')
        target.parent.mkdir(parents=True, exist_ok=True)
        payload.rename(target)
        try:
            record.update(status='restored', restored=stamp())
            json_write(p, record)
            self.log(record)
        except Exception:
            target.rename(payload)
            record['status'] = 'complete'
            json_write(p, record)
            raise
        return {'operation_id': operation_id, 'path': str(target), 'status': 'restored'}

    def undo(self, operation_id, path_resolver):
        p = self.record_path(operation_id)
        record = json.loads(p.read_text(encoding='utf-8'))
        if record['status'] != 'complete' or not (p.parent / 'before').is_file():
            raise VaultError('此操作没有可回滚的文件旧版本')
        target = path_resolver(record)
        if not target.is_file() or digest(target) != record.get('after_hash'):
            raise VaultError('文件在该操作后又有变化，拒绝覆盖', 'conflict')
        result = self.write(target, (p.parent / 'before').read_bytes(), 'undo', digest(target), record['managed'])
        record['status'] = 'undone'
        json_write(p, record)
        self.log(record)
        return result

def re_id(value):
    import re
    return bool(re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{12}', value))
