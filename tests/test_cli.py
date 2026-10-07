from pathlib import Path
from dataclasses import replace
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from svault.context import Context, VaultError
from svault.service import Service, MAIN_AGENT_TEMPLATE
from svault.storage import digest, atomic, json_write
from svault.cli import run
from svault import blueprint

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='svault-test-', dir=tempfile.gettempdir())
        self.base = Path(self.temp.name).resolve()
        self.addCleanup(self.cleanup)
        self.root = self.base / 'scientific_notes'
        shutil.copytree(REPO / 'tests/fixtures', self.root)
        self.root.joinpath('.obsidian').mkdir()
        self.root.joinpath('规则与模板/科研库规则.md').write_text('# 规则\n', encoding='utf-8')
        self.root.joinpath('AGENTS.md').write_text('# 主库\n必须遵循科研规则。\n', encoding='utf-8')
        self.root.joinpath('科研工作台.md').write_text('# 工作台\n', encoding='utf-8')
        self.root.joinpath('共享知识').mkdir()
        self.root.joinpath('共享知识/共享知识入口.md').write_text('# 共享知识\n跨项目知识原件。', encoding='utf-8')
        template = self.root / MAIN_AGENT_TEMPLATE
        template.parent.mkdir(parents=True)
        template.write_text('# 主库\n必须遵循科研规则。\n', encoding='utf-8')
        json_write(self.root / '.obsidian/community-plugins.json', ['fast-note-sync'])
        json_write(self.root / '.obsidian/plugins/fast-note-sync/manifest.json', {'id': 'fast-note-sync'})
        self.root.joinpath('.obsidian/plugins/fast-note-sync/main.js').write_text('// fixture', encoding='utf-8')
        json_write(self.root / '.obsidian/plugins/fast-note-sync/data.json', {'vault': 'scientific_notes', 'api': 'https://example.invalid', 'apiToken': 'TEST-ONLY-TOKEN', 'syncEnabled': True, 'configSyncEnabled': True, 'configSyncOtherDirs': '[]'})
        self.main = Service(Context.resolve(self.root))
        self.main.project_create('医学影像', 'P001-分割', 'P001')
        self.child = self.root / '科研项目/医学影像/P001-分割'
        self.sub = Service(Context.resolve(self.child))

    def cleanup(self):
        self.assertTrue(self.base.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertTrue(self.base.name.startswith('svault-test-'))
        self.temp.cleanup()

    def add_note(self):
        return self.sub.note_add('03 实验记录/P001-实验-R001.md', '实验记录', properties={'run_id': 'R001'})

    def test_note_crud_and_recovery(self):
        added = self.add_note()
        note = self.sub.note_get('03 实验记录/P001-实验-R001.md')
        self.assertEqual(note['properties']['project_id'], 'P001')
        edited = self.sub.note_edit('03 实验记录/P001-实验-R001.md', {'status': 'running'}, append='新证据待核实', if_hash=note['hash'])
        self.assertEqual(len(self.sub.note_list(query='新证据')), 1)
        self.assertEqual(self.sub.note_list(kind='experiment')[0]['properties']['status'], 'running')
        deleted = self.sub.file_delete('03 实验记录/P001-实验-R001.md', edited['hash'])
        self.assertFalse(Path(added['path']).exists())
        self.sub.restore(deleted['operation_id'])
        self.assertTrue(Path(added['path']).exists())
        self.assertEqual(self.sub.note_get('03 实验记录/P001-实验-R001.md')['hash'], edited['hash'])

    def test_stale_hash_and_identity_edits_refused(self):
        added = self.add_note()
        with self.assertRaises(VaultError):
            self.sub.note_edit('03 实验记录/P001-实验-R001.md', {'status': 'running'}, if_hash='stale')
        with self.assertRaises(VaultError):
            self.sub.note_edit('03 实验记录/P001-实验-R001.md', {'project_id': 'P002'}, if_hash=added['hash'])

    def test_replace_requires_existing_user_authorization(self):
        added = self.add_note()
        with self.assertRaises(VaultError):
            self.sub.note_edit('03 实验记录/P001-实验-R001.md', replacement='替换', if_hash=added['hash'])
        replaced = self.sub.note_edit('03 实验记录/P001-实验-R001.md', replacement='替换', if_hash=added['hash'], authorized=True)
        self.assertEqual(replaced['action'], 'note-replace')

    def test_history_undo_and_conflict(self):
        added = self.add_note()
        edited = self.sub.note_edit('03 实验记录/P001-实验-R001.md', append='修订', if_hash=added['hash'])
        result = self.sub.store.undo(edited['operation_id'], self.sub.restore_resolver)
        self.assertEqual(result['hash'], added['hash'])
        with self.assertRaises(VaultError):
            self.sub.store.undo(edited['operation_id'], self.sub.restore_resolver)

    def test_restore_refuses_overwrite(self):
        self.add_note()
        deleted = self.sub.file_delete('03 实验记录/P001-实验-R001.md')
        self.add_note()
        with self.assertRaises(VaultError):
            self.sub.restore(deleted['operation_id'])

    def test_project_agent_cannot_promote_or_access_sibling(self):
        self.main.project_create('脑机接口', 'P002-脑电', 'P002')
        self.assertEqual(Context.resolve(self.child, vault=self.root).role, 'project')
        with self.assertRaises(VaultError):
            Context.resolve(self.child, vault=self.root, project='P002')
        with self.assertRaises(VaultError):
            self.sub.project_create('其他', 'P003-越权', 'P003')
        with self.assertRaises(VaultError):
            self.sub.project_delete('P001')
        with self.assertRaises(VaultError):
            self.sub.template_set('实验记录', '# 越权', create=True)
        with self.assertRaises(VaultError):
            self.sub.agent_template_set('main', '# 越权', None)
        self.assertEqual(len(self.sub.project_list()), 1)

    def test_external_explicit_child_has_project_role(self):
        outside = self.base / 'outside'
        outside.mkdir()
        self.assertEqual(Context.resolve(outside, vault=self.child).role, 'project')

    def test_parent_escape_hidden_config_and_protected_paths(self):
        for value in ('../逃逸.md', '/绝对.md', 'C:/路径.md', '.obsidian/plugins/fast-note-sync/data.json', 'AGENTS.md', '项目.json', '08 交接记录/原始材料/原件.txt'):
            with self.assertRaises(VaultError):
                self.sub.ctx.path(value, mutate=True)

    def test_symlink_escape_and_trash_link(self):
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.child / '联接'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            script = self.base / 'create-junction.ps1'
            script.write_text('param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Value $Target -ErrorAction Stop | Out-Null\n', encoding='utf-8-sig')
            proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Link', str(link), '-Target', str(outside)], capture_output=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        with self.assertRaises(VaultError):
            self.sub.ctx.path('联接/逃逸.md', mutate=True)
        if os.environ.get('SVAULT_EXE'):
            proc = subprocess.run([os.environ['SVAULT_EXE'], 'note', 'add', '联接/P001-逃逸.md', '--text', '不应写入'], cwd=self.child, capture_output=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stderr)['error']['code'], 'permission_denied')

    def test_project_delete_restore_and_sync_registration(self):
        self.add_note()
        deleted = self.main.project_delete('P001')
        self.assertFalse(self.child.exists())
        self.assertEqual(blueprint.load(self.root / blueprint.SYNC_LIST)['directories'], [])
        self.main.restore(deleted['operation_id'])
        self.assertTrue(self.child.exists())
        self.assertEqual(len(blueprint.load(self.root / blueprint.SYNC_LIST)['directories']), 1)
        self.assertTrue((self.child / '03 实验记录/P001-实验-R001.md').exists())

    def test_project_restore_rejects_duplicate_id(self):
        deleted = self.main.project_delete('P001')
        self.main.project_create('其他', '不同名称', 'P001')
        with self.assertRaises(VaultError):
            self.main.restore(deleted['operation_id'])

    def test_reserved_name_and_nested_vault_refused(self):
        with self.assertRaises(ValueError):
            self.main.project_create('分类', 'scientific_notes', 'P003')
        with self.assertRaises(ValueError):
            self.main.project_create('医学影像/P001-分割/实验', 'P003-非法', 'P003')

    def test_agents_templates_preview_apply_custom_and_undo(self):
        self.assertIn('../../../AGENTS.md', self.sub.agents_render()['content'])
        original = digest(self.child / 'AGENTS.md')
        self.sub.agents_customize('只记录本项目，不改变主规则。')
        applied = self.sub.agents_apply(original)
        self.assertIn('只记录本项目', (self.child / 'AGENTS.md').read_text(encoding='utf-8'))
        self.sub.store.undo(applied['operation_id'], self.sub.restore_resolver)
        self.assertEqual(digest(self.child / 'AGENTS.md'), original)

    def test_agents_apply_requires_current_hash(self):
        with self.assertRaises(VaultError):
            self.sub.agents_apply(None)

    def test_template_versions_and_recovery(self):
        added = self.main.template_set('测试模板', '---\ntype: note\n---\n# {{title}}\n', create=True)
        updated = self.main.template_set('测试模板', '---\ntype: note\n---\n# 更新\n', added['hash'])
        self.main.store.undo(updated['operation_id'], self.main.restore_resolver)
        deleted = self.main.template_delete('测试模板')
        self.main.restore(deleted['operation_id'])
        self.assertEqual(digest(self.main.template_path('测试模板')), added['hash'])

    def test_shared_read_is_uri_based(self):
        result = self.sub.shared(path='共享知识入口.md')
        self.assertIn('vault=scientific_notes', result['uri'])
        with self.assertRaises(VaultError):
            self.sub.shared(path='../AGENTS.md')

    def test_handoff_complete_requires_real_outputs(self):
        note = self.sub.note_add('08 交接记录/P001-交接-H001.md', '交接日志', properties={'handoff_id': 'H001'})
        with self.assertRaises(VaultError):
            self.sub.note_edit('08 交接记录/P001-交接-H001.md', {'status': 'complete'}, if_hash=note['hash'])
        with self.assertRaises(VaultError):
            self.sub.note_edit('08 交接记录/P001-交接-H001.md', {'status': 'complete', 'outputs': ['缺失.md']}, if_hash=note['hash'])

    def test_attachments_materials_and_folder_recovery(self):
        source = self.base / '指标.csv'
        source.write_text('metric,value\n', encoding='utf-8')
        added = self.sub.file_add('04 结果与图表/P001-指标.csv', source)
        self.assertTrue(Path(added['path']).exists())
        with self.assertRaises(VaultError):
            self.sub.file_add('权重.pth', source)
        self.sub.folder_add('临时目录')
        deleted = self.sub.file_delete('临时目录')
        self.sub.restore(deleted['operation_id'])
        self.assertTrue((self.child / '临时目录').is_dir())

    def test_mutation_failure_restores_original(self):
        added = self.add_note()
        with patch.object(self.sub.store, 'log', side_effect=OSError('模拟日志写入失败')):
            with self.assertRaises(OSError):
                self.sub.note_edit('03 实验记录/P001-实验-R001.md', append='不应保留', if_hash=added['hash'])
        self.assertEqual(digest(Path(added['path'])), added['hash'])

    def test_sync_preserves_credentials_and_checks_are_read_only(self):
        settings = blueprint.load(self.root / '.obsidian/plugins/fast-note-sync/data.json')
        self.main.refresh_sync()
        self.assertEqual(blueprint.load(self.root / '.obsidian/plugins/fast-note-sync/data.json')['apiToken'], settings['apiToken'])
        before = {p.relative_to(self.root).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size) for p in self.root.rglob('*') if p.is_file()}
        checked = self.main.scoped_check()
        after = {p.relative_to(self.root).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(checked['errors'], [])

    def test_cli_json_errors_and_native_optional(self):
        result = run(['note', 'list', '--type', 'experiment'], cwd=self.child)
        self.assertEqual(result, [])
        command = [os.environ['SVAULT_EXE']] if os.environ.get('SVAULT_EXE') else [sys.executable, str(REPO / 'run_svault.py')]
        result = subprocess.run(command + ['whoami'], cwd=self.child, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['role'], 'project')
        result = subprocess.run(command + ['project', 'create', '--category', '分类', '--name', 'P003-越权', '--id', 'P003'], cwd=self.child, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)['error']['code'], 'permission_denied')

    def test_blueprint_invalid_edit_rolls_back(self):
        source = self.root / blueprint.TEMPLATE / '95 索引/PROJECT_ID-实验.base.template'
        before = source.read_bytes()
        bad = self.base / 'bad-template.txt'
        bad.write_text('views: [unterminated\n', encoding='utf-8')
        with self.assertRaises(VaultError):
            run(['blueprint', 'set-file', '95 索引/PROJECT_ID-实验.base.template', '--body-file', str(bad), '--if-hash', digest(source)], cwd=self.root)
        self.assertEqual(source.read_bytes(), before)

    def test_note_cannot_impersonate_project_or_log(self):
        for kind in ('project', 'maintenance', False):
            with self.assertRaises(VaultError):
                self.sub.note_add('00 收件箱/P001-伪造.md', text='# 非项目身份', properties={'type': kind})

    def test_sync_operations_are_audited_without_secrets(self):
        result = self.main.sync_maintain(rebuild=True)
        record = self.main.store.record_path(result['operation_id'])
        self.assertNotIn('TEST-ONLY-TOKEN', record.read_text(encoding='utf-8'))
        self.assertEqual(json.loads(record.read_text(encoding='utf-8'))['action'], 'sync-rebuild')

    def test_process_entry_note_lifecycle(self):
        command = [os.environ['SVAULT_EXE']] if os.environ.get('SVAULT_EXE') else [sys.executable, str(REPO / 'run_svault.py')]
        def invoke(*args):
            proc = subprocess.run(command + list(args), cwd=self.child, capture_output=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)['result']
        path = '03 实验记录/P001-进程实验-R002.md'
        added = invoke('note', 'add', path, '--template', '实验记录', '--set', 'run_id=R002')
        read = invoke('note', 'get', path)
        edited = invoke('note', 'edit', path, '--if-hash', read['hash'], '--append-text', '进程入口追加证据')
        deleted = invoke('note', 'delete', path)
        invoke('trash', 'restore', deleted['operation_id'])
        self.assertEqual(invoke('note', 'get', path)['hash'], edited['hash'])
        self.assertNotEqual(added['hash'], edited['hash'])
        self.assertEqual(invoke('check')['errors'], [])

    def test_project_progress_edit_keeps_identity(self):
        home = self.child / 'P001-项目主页.md'
        result = self.sub.project_edit({'next_step': '待核实数据划分'}, '追加进展，不替换结论。', None, digest(home))
        self.assertIn('追加进展', home.read_text(encoding='utf-8'))
        with self.assertRaises(VaultError):
            self.sub.project_edit({'project_id': 'P002'}, None, None, result['hash'])

    def test_index_scope_and_filter(self):
        index = self.child / '95 索引/P001-实验.base'
        content = index.read_text(encoding='utf-8')
        with self.assertRaises(VaultError):
            self.sub.index_set('P001-实验', content.replace('P001', 'P002'), digest(index))
        result = self.sub.index_set('P001-实验', content + '\n# 项目内显示调整\n', digest(index))
        self.assertEqual(result['action'], 'index-set')

if __name__ == '__main__':
    unittest.main()

