from pathlib import Path
import hashlib
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
from svault.cli import run
from svault.context import VaultError
from svault import bootstrap, blueprint, maintenance, profiles
from svault.storage import digest, json_write

def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='svault-v11-test-')
        self.base = Path(self.temp.name).resolve()
        self.addCleanup(self.cleanup)
        self.root = self.base / '医学科研库'

    def cleanup(self):
        self.assertTrue(self.base.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertTrue(self.base.name.startswith('svault-v11-test-'))
        self.temp.cleanup()

    def init_main(self):
        return run(['init', str(self.root)], cwd=self.base)

    def project(self):
        self.init_main()
        target = self.root / '科研项目/脑机接口/P001-脑电'
        run(['init', str(target), '--kind', 'project', '--parent', str(self.root), '--id', 'P001'], cwd=self.root)
        return target

    def test_offline_dry_run_matches_files_and_no_python(self):
        preview = run(['init', str(self.root), '--dry-run'], cwd=self.base)
        self.assertFalse(self.root.exists())
        result = self.init_main()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(set(preview['files']), set(snapshot(self.root)))
        self.assertFalse(list(self.root.rglob('*.py')))
        self.assertFalse(list(self.root.rglob('*.pyc')))
        self.assertEqual(run(['check'], cwd=self.root)['errors'], [])
        self.assertEqual(run(['vault', 'get'], cwd=self.root)['vault_name'], self.root.name)

    def test_empty_target_and_existing_content_refused(self):
        self.root.mkdir()
        self.init_main()
        before = snapshot(self.root)
        with self.assertRaises(VaultError):
            self.init_main()
        self.assertEqual(before, snapshot(self.root))
        other = self.base / '非空'
        other.mkdir()
        (other / '用户文件.md').write_text('保留', encoding='utf-8')
        with self.assertRaises(VaultError):
            run(['init', str(other)], cwd=self.base)

    def test_failed_init_preserves_empty_target(self):
        self.root.mkdir()
        with patch('svault.bootstrap.publish', side_effect=OSError('模拟发布失败')):
            with self.assertRaises(OSError):
                self.init_main()
        self.assertTrue(self.root.is_dir())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(list(self.base.glob('.svault-init-*')))

    def test_other_main_name_project_uri_and_offline_create(self):
        target = self.project()
        home = (target / 'P001-项目主页.md').read_text(encoding='utf-8')
        from urllib.parse import quote_plus
        self.assertIn('vault=' + quote_plus(self.root.name), home)
        self.assertNotIn('vault=scientific_notes', home)
        parent = json.loads((self.root / 'vault.json').read_text(encoding='utf-8'))
        child = json.loads((target / '项目.json').read_text(encoding='utf-8'))
        self.assertEqual(child['parent_vault_id'], parent['vault_id'])
        self.assertEqual(run(['check'], cwd=self.root)['errors'], [])
        self.assertEqual(run(['shared', 'get', '共享知识入口.md'], cwd=target)['uri'].split('vault=')[1].split('&')[0], quote_plus(self.root.name))

    def test_project_dry_run_and_child_permissions(self):
        target = self.project()
        before = snapshot(self.root)
        proposed = self.root / '科研项目/脑机接口/P002-研究'
        preview = run(['init', str(proposed), '--kind', 'project', '--parent', str(self.root), '--id', 'P002', '--dry-run'], cwd=self.root)
        self.assertEqual(preview['errors'], [])
        self.assertFalse(proposed.exists())
        self.assertEqual(snapshot(self.root), before)
        for argv in (['init', str(self.base / '非法主库')], ['blueprint', 'generate', '--kind', 'main', '--output', str(self.base / '非法模板')], ['vault', 'register', str(self.root)], ['doctor', str(self.root)]):
            with self.assertRaises(VaultError):
                run(argv, cwd=target)

    def test_generated_blueprints_are_not_vaults(self):
        for kind in ('main', 'project'):
            target = self.base / ('蓝图-' + kind)
            preview = run(['blueprint', 'generate', '--kind', kind, '--output', str(target), '--dry-run'], cwd=self.base)
            self.assertFalse(target.exists())
            run(['blueprint', 'generate', '--kind', kind, '--output', str(target)], cwd=self.base)
            self.assertFalse((target / '.obsidian').exists())
            self.assertFalse((target / 'AGENTS.md').exists())
            self.assertEqual(set(preview['files']), set(snapshot(target)))
            self.assertTrue((target / 'template.json').exists())
        self.assertEqual(len(run(['blueprint', 'list'], cwd=self.base)), 2)

    def make_profile(self):
        source = self.base / '旧笔记库'
        json_write(source / '.obsidian/app.json', {'showLineNumber': True, 'newFileFolderPath': '旧私有路径', 'apiToken': 'SECRET-EDITOR'})
        json_write(source / '.obsidian/hotkeys.json', {'editor:toggle-bold': [{'modifiers': ['Ctrl'], 'key': 'B'}]})
        json_write(source / '.obsidian/appearance.json', {'cssTheme': '测试主题', 'baseFontSize': 18, 'secret': 'SECRET-APPEARANCE'})
        (source / '.obsidian/themes/测试主题').mkdir(parents=True)
        (source / '.obsidian/themes/测试主题/theme.css').write_text('body{}', encoding='utf-8')
        json_write(source / '.obsidian/plugins/demo/manifest.json', {'id': 'demo', 'version': '1'})
        (source / '.obsidian/plugins/demo/main.js').write_text('// program', encoding='utf-8')
        json_write(source / '.obsidian/plugins/demo/data.json', {'apiToken': 'SECRET-PLUGIN', 'custom': True})
        return source

    def test_selected_profile_only_and_no_plugin_state(self):
        source = self.make_profile()
        inventory = run(['profile', 'inspect', str(source)], cwd=self.base)
        self.assertEqual(inventory['plugins'][0]['id'], 'demo')
        args = ['init', str(self.root), '--from-vault', str(source), '--include', 'editor,plugins', '--plugin', 'demo']
        plan = run(args + ['--dry-run'], cwd=self.base)
        self.assertEqual(plan['errors'], [])
        run(args, cwd=self.base)
        self.assertTrue(json.loads((self.root / '.obsidian/app.json').read_text(encoding='utf-8'))['showLineNumber'])
        self.assertEqual(json.loads((self.root / '.obsidian/app.json').read_text(encoding='utf-8'))['newFileFolderPath'], '收件箱')
        self.assertFalse((self.root / '.obsidian/plugins/demo/data.json').exists())
        self.assertFalse((self.root / '.obsidian/themes/测试主题').exists())
        self.assertEqual(json.loads((self.root / '.obsidian/hotkeys.json').read_text(encoding='utf-8')), {})
        self.assertNotIn('SECRET-', ''.join(p.read_text(encoding='utf-8') for p in self.root.rglob('*.json')))

    def test_selected_missing_profile_stops_and_preview_reports(self):
        source = self.make_profile()
        args = ['init', str(self.root), '--from-vault', str(source), '--include', 'plugins', '--plugin', 'not-installed']
        self.assertTrue(run(args + ['--dry-run'], cwd=self.base)['errors'])
        with self.assertRaises(VaultError):
            run(args, cwd=self.base)
        self.assertFalse(self.root.exists())

    def test_register_legacy_preserves_notes_and_dry_run(self):
        target = self.project()
        (self.root / 'vault.json').unlink()
        (target / '项目.json').unlink()
        before = snapshot(self.root)
        preview = run(['vault', 'register', str(self.root), '--dry-run'], cwd=self.root)
        self.assertEqual(before, snapshot(self.root))
        applied = run(['vault', 'register', str(self.root)], cwd=self.root)
        self.assertEqual(preview['metadata']['vault_id'], applied['metadata']['vault_id'])
        home = (target / 'P001-项目主页.md').read_bytes()
        run(['vault', 'register', str(target)], cwd=self.root)
        self.assertEqual(home, (target / 'P001-项目主页.md').read_bytes())
        self.assertEqual(run(['vault', 'get', str(target)], cwd=self.root)['project_id'], 'P001')

    def test_repair_preview_hash_and_preservation(self):
        self.init_main()
        existing_rule = (self.root / 'AGENTS.md').read_bytes()
        missing = self.root / '规则与模板/笔记模板/实验记录.md'
        missing.unlink()
        (self.root / '收件箱').rmdir()
        plan = run(['repair'], cwd=self.root)
        self.assertFalse(missing.exists())
        self.assertTrue(any(a['path'].endswith('实验记录.md') for a in plan['actions']))
        (self.root / '规则与模板/笔记模板/文献笔记.md').write_text('用户修改，不替换', encoding='utf-8')
        with self.assertRaises(VaultError):
            run(['repair', '--apply', '--if-plan-hash', plan['plan_hash']], cwd=self.root)
        plan = run(['repair'], cwd=self.root)
        run(['repair', '--apply', '--if-plan-hash', plan['plan_hash']], cwd=self.root)
        self.assertTrue(missing.exists())
        self.assertEqual((self.root / 'AGENTS.md').read_bytes(), existing_rule)
        self.assertEqual((self.root / '规则与模板/笔记模板/文献笔记.md').read_text(encoding='utf-8'), '用户修改，不替换')

    def test_doctor_does_not_replace_corrupt_existing_base(self):
        self.init_main()
        p = self.root / '索引/实验总览.base'
        p.write_text('views: [invalid', encoding='utf-8')
        report = run(['doctor'], cwd=self.root)
        self.assertTrue(report['errors'])
        plan = run(['repair'], cwd=self.root)
        self.assertFalse(any(a['path'] == '索引/实验总览.base' for a in plan['actions']))
        run(['repair', '--apply', '--if-plan-hash', plan['plan_hash']], cwd=self.root)
        self.assertEqual(p.read_text(encoding='utf-8'), 'views: [invalid')

    def test_child_repairs_only_own_missing_templates_and_rules_diff(self):
        target = self.project()
        original = (target / 'AGENTS.md').read_bytes()
        missing = target / '90 模板/实验记录.md'
        missing.unlink()
        plan = run(['repair'], cwd=target)
        run(['repair', '--apply', '--if-plan-hash', plan['plan_hash']], cwd=target)
        self.assertTrue(missing.exists())
        self.assertEqual((target / 'AGENTS.md').read_bytes(), original)
        local = self.base / '补充.txt'
        local.write_text('只补充本项目。', encoding='utf-8')
        run(['agents', 'customize', '--body-file', str(local)], cwd=target)
        difference = run(['agents', 'diff'], cwd=target)
        self.assertTrue(difference['changed'])
        run(['agents', 'sync', '--if-hash', difference['hash']], cwd=target)
        self.assertFalse(run(['agents', 'diff'], cwd=target)['changed'])

    def test_native_exe_from_zero_no_network_and_complete_commands(self):
        native = os.environ.get('SVAULT_EXE')
        if not native:
            self.skipTest('构建 exe 后运行')
        def call(*args, cwd=None):
            environment = dict(os.environ)
            environment['HTTPS_PROXY'] = 'http://127.0.0.1:1'
            environment['HTTP_PROXY'] = 'http://127.0.0.1:1'
            proc = subprocess.run([native, *args], cwd=cwd or self.base, env=environment, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            return json.loads(proc.stdout)['result']
        call('init', str(self.root), '--dry-run')
        self.assertFalse(self.root.exists())
        call('init', str(self.root))
        self.assertTrue((self.root / '工具/svault.exe').exists())
        self.assertEqual(digest(self.root / '工具/svault.exe'), digest(Path(native)))
        target = self.root / '科研项目/交叉研究/P001-研究'
        call('init', str(target), '--kind', 'project', '--parent', str(self.root), '--id', 'P001', cwd=self.root)
        call('vault', 'list', cwd=self.root)
        call('vault', 'get', cwd=target)
        self.assertEqual(call('check', cwd=self.root)['errors'], [])
        self.assertEqual(call('check', cwd=target)['errors'], [])
        self.assertEqual(call('doctor', cwd=self.root)['errors'], [])
        call('blueprint', 'validate', cwd=self.root)
        call('agents', 'diff', cwd=target)
        self.assertFalse(list(self.root.rglob('*.py')))
        self.assertFalse(list(self.root.rglob('*.pyc')))

    def test_theme_hotkeys_selected_and_plugin_state_not_in_child(self):
        source = self.make_profile()
        run(['init', str(self.root), '--from-vault', str(source), '--include', 'appearance,hotkeys,plugins', '--plugin', 'demo'], cwd=self.base)
        self.assertTrue((self.root / '.obsidian/themes/测试主题/theme.css').exists())
        self.assertEqual(json.loads((self.root / '.obsidian/appearance.json').read_text(encoding='utf-8'))['baseFontSize'], 18)
        json_write(self.root / '.obsidian/plugins/demo/data.json', {'apiKey': 'DO-NOT-COPY'})
        target = self.root / '科研项目/医学影像/P001-项目'
        run(['project', 'create', '--category', '医学影像', '--name', target.name, '--id', 'P001'], cwd=self.root)
        self.assertTrue((target / '.obsidian/plugins/demo/main.js').exists())
        self.assertFalse((target / '.obsidian/plugins/demo/data.json').exists())

    def test_register_duplicate_identity_and_child_metadata_protection(self):
        first = self.project()
        other = self.root / '科研项目/脑机接口/P002-别的项目'
        run(['project', 'create', '--category', '脑机接口', '--name', other.name, '--id', 'P002'], cwd=self.root)
        info = json.loads((other / '项目.json').read_text(encoding='utf-8'))
        info['project_id'] = 'P001'
        json_write(other / '项目.json', info)
        with self.assertRaises(VaultError):
            run(['vault', 'register', str(first)], cwd=self.root)
        with self.assertRaises(VaultError):
            run(['note', 'delete', 'vault.json'], cwd=self.root)

    def test_missing_rules_can_be_diagnosed_and_supplements_survive(self):
        self.init_main()
        local = self.root / 'AGENTS.local.md'
        local.write_text('必须保留的本库补充。', encoding='utf-8')
        (self.root / 'AGENTS.md').unlink()
        (self.root / '规则与模板/科研库规则.md').unlink()
        report = run(['doctor'], cwd=self.root)
        self.assertTrue(any(i['code'] == 'missing_standard_file' for i in report['issues']))
        plan = run(['repair'], cwd=self.root)
        run(['repair', '--apply', '--if-plan-hash', plan['plan_hash']], cwd=self.root)
        self.assertIn('必须保留', (self.root / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertTrue((self.root / '规则与模板/科研库规则.md').exists())

if __name__ == '__main__':
    unittest.main()
