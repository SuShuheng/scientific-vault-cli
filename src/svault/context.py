from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import json
import re
import yaml

class VaultError(Exception):
    def __init__(self, message, code='invalid_operation'):
        super().__init__(message)
        self.code = code

def main_vault(path):
    path = path.resolve()
    if path.is_file():
        path = path.parent
    for parent in (path, *path.parents):
        if (parent / '规则与模板/科研库规则.md').is_file() and (parent / '.obsidian').is_dir():
            return parent
    return None

def leaves(root):
    directory = root / '科研项目'
    if not directory.exists():
        return []
    return sorted(p.parent for p in directory.rglob('.obsidian') if p.is_dir() and not any(x.startswith('.project-staging-') for x in p.parts))

def identity(vault):
    metadata = vault / '项目.json'
    if metadata.exists():
        data = json.loads(metadata.read_text(encoding='utf-8-sig'))
    else:
        homes = list(vault.glob('*-项目主页.md'))
        if len(homes) != 1:
            raise VaultError('项目身份无法唯一确定')
        text = homes[0].read_text(encoding='utf-8-sig')
        data = yaml.safe_load(text.split('---', 2)[1])
    project_id = data.get('project_id')
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', project_id):
        raise VaultError('项目编号无效')
    return project_id

def parts(value):
    value = value.replace('\\', '/')
    if not value or value.startswith('/') or re.match(r'^[A-Za-z]:', value):
        raise VaultError('只接受当前作用域内的相对路径', 'permission_denied')
    path = PurePosixPath(value)
    if any(x in ('', '.', '..') for x in value.split('/')):
        raise VaultError('路径包含空段或目录跳转', 'permission_denied')
    for part in path.parts:
        if part.endswith((' ', '.')) or any(c in part for c in ':*?"<>|\r\n\t') or any(ord(c) < 32 for c in part):
            raise VaultError('路径包含不支持的字符')
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part):
            raise VaultError('路径包含保留设备名称')
    return path.parts

@dataclass(frozen=True)
class Context:
    root: Path
    scope: Path
    role: str
    project_id: str | None = None

    @classmethod
    def resolve(cls, cwd=None, vault=None, project=None):
        cwd = Path(cwd or Path.cwd()).resolve()
        detected = main_vault(cwd)
        requested = main_vault(Path(vault)) if vault else None
        if vault and requested is None:
            raise VaultError('指定路径不属于科研主库')
        if detected and requested and detected != requested:
            raise VaultError('当前 Agent 不能切换到其他主库', 'permission_denied')
        root = detected or requested
        if root is None:
            raise VaultError('请在主库或子库目录执行，或从库外显式指定 --vault')
        anchor = cwd if detected else Path(vault).resolve()
        if any(part.startswith('.') for part in anchor.relative_to(root).parts):
            raise VaultError('不能从回收站或隐藏配置目录启动维护命令', 'permission_denied')
        local = [v for v in leaves(root) if anchor.is_relative_to(v.resolve())]
        if len(local) > 1:
            raise VaultError('检测到非法的多层项目 Vault')
        role = 'project' if local else 'main'
        scope = local[0] if local else root
        ctx = cls(root, scope, role, identity(scope) if local else None)
        return ctx.select(project) if project else ctx

    def select(self, project):
        matches = [v for v in leaves(self.root) if identity(v).casefold() == project.casefold()]
        if len(matches) != 1:
            raise VaultError('项目编号不存在或不唯一')
        if self.role == 'project' and matches[0] != self.scope:
            raise VaultError('子库 Agent 只能操作自己的项目', 'permission_denied')
        return replace(self, scope=matches[0], project_id=identity(matches[0]))

    def require_main(self):
        if self.role != 'main':
            raise VaultError('此操作仅主库 Agent 可执行', 'permission_denied')

    def info(self):
        return {'role': self.role, 'main_vault': str(self.root), 'scope': str(self.scope), 'project_id': self.project_id}

    def path(self, value, mutate=False, managed=False):
        segments = parts(value)
        if any(x.startswith('.') for x in segments):
            raise VaultError('普通操作不能访问隐藏配置或回收站', 'permission_denied')
        if any(x in {'工具', '维护日志'} for x in segments):
            raise VaultError('工具与维护日志由专用命令管理', 'permission_denied')
        path = self.scope.joinpath(*segments)
        if not path.resolve().is_relative_to(self.scope.resolve()):
            raise VaultError('路径或链接越出当前作用域', 'permission_denied')
        current = path
        while current != self.scope:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise VaultError('不通过符号链接或目录联接操作文件', 'permission_denied')
            current = current.parent
        if mutate:
            protected = path.name in {'AGENTS.md', 'AGENTS.local.md', '项目.json'} or path.name.endswith('-项目主页.md')
            if protected and not managed:
                raise VaultError('项目身份和 Agent 规则需使用专用命令', 'permission_denied')
            if '原始材料' in segments:
                raise VaultError('交接原件只读，不能修改或删除', 'permission_denied')
            if self.scope == self.root and segments[0] == '科研项目':
                raise VaultError('请通过 --project 选定子库；项目结构用 project 命令管理', 'permission_denied')
            if segments[0] in {'规则与模板', '90 模板', '95 索引', '索引'} and not managed:
                raise VaultError('模板与索引需使用专用命令管理', 'permission_denied')
        return path
