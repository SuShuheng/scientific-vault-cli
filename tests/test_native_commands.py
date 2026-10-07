"""Exercise every maintenance command family through the packaged executable."""
from pathlib import Path
import json
import os
import subprocess
import unittest
import test_cli as support

class NativeCommands(unittest.TestCase):
    cleanup = support.VaultTests.cleanup

    def setUp(self):
        self.exe = os.environ.get('SVAULT_EXE')
        if not self.exe:
            self.skipTest('须设置 SVAULT_EXE 后验证原生成品')
        support.VaultTests.setUp(self)

    def call(self, *args, child=False, expected=0):
        proc = subprocess.run([self.exe, *args], cwd=self.child if child else self.root, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(proc.returncode, expected, proc.stderr or proc.stdout)
        data = json.loads(proc.stdout if expected == 0 else proc.stderr)
        return data['result'] if expected == 0 else data['error']

    def write_input(self, name, text):
        path = self.base / name
        path.write_text(text, encoding='utf-8')
        return str(path)

    def test_identity_rules_reading_and_permission_denial(self):
        self.assertEqual(self.call('version')['version'], '1.0.0')
        self.assertEqual(self.call('whoami')['role'], 'main')
        self.assertEqual(self.call('whoami', child=True)['role'], 'project')
        self.assertTrue(self.call('operations')['operations'])
        self.assertTrue(self.call('permissions')['operations'])
        self.assertIn('main_rules', self.call('rules', child=True))
        self.assertTrue(self.call('shared', 'list', child=True))
        self.assertTrue(self.call('shared', 'find', '知识', child=True))
        self.assertIn('obsidian://', self.call('shared', 'get', '共享知识入口.md', child=True)['uri'])
        self.assertEqual(self.call('update', 'apply', child=True, expected=1)['code'], 'permission_denied')
        self.assertEqual(self.call('sync', 'apply', child=True, expected=1)['code'], 'permission_denied')
        self.assertEqual(self.call('file', 'get', '.obsidian/plugins/fast-note-sync/data.json', child=True, expected=1)['code'], 'permission_denied')

    def test_files_folders_notes_and_conflict_recovery(self):
        csv = self.write_input('metrics.csv', 'metric,value\nscore,0\n')
        file = self.call('file', 'add', '04 结果与图表/P001-指标.csv', '--source', csv, child=True)
        self.assertIn('metric', self.call('file', 'get', '04 结果与图表/P001-指标.csv', '--text', child=True)['content'])
        replacement = self.write_input('updated.csv', 'metric,value\nscore,1\n')
        edited = self.call('file', 'edit', '04 结果与图表/P001-指标.csv', '--body-file', replacement, '--if-hash', file['hash'], '--authorized-replace', child=True)
        self.call('history', 'get', edited['operation_id'], child=True)
        self.call('history', 'undo', edited['operation_id'], child=True)
        self.call('file', 'list', '04 结果与图表', child=True)
        deleted = self.call('file', 'delete', '04 结果与图表/P001-指标.csv', child=True)
        self.call('trash', 'get', deleted['operation_id'], child=True)
        self.call('trash', 'restore', deleted['operation_id'], child=True)
        self.call('folder', 'add', '临时目录', child=True)
        self.call('folder', 'list', child=True)
        deletion = self.call('folder', 'delete', '临时目录', child=True)
        self.call('trash', 'restore', deletion['operation_id'], child=True)
        self.call('trash', 'list', child=True)
        path = '03 实验记录/P001-原生-R001.md'
        added = self.call('note', 'add', path, '--template', '实验记录', '--set', 'run_id=R001', child=True)
        self.call('note', 'get', path, child=True)
        self.call('note', 'edit', path, '--if-hash', added['hash'], '--append-text', '测试夹具证据', child=True)
        self.assertTrue(self.call('note', 'find', '测试夹具证据', child=True))
        self.assertTrue(self.call('note', 'list', '--type', 'experiment', child=True))
        deleted = self.call('note', 'delete', path, child=True)
        self.call('trash', 'restore', deleted['operation_id'], child=True)
        self.assertEqual(self.call('check', child=True)['errors'], [])

    def test_templates_agents_indexes_and_blueprint(self):
        self.call('template', 'list', child=True)
        self.call('template', 'get', '实验记录', child=True)
        self.assertIn('新笔记', self.call('template', 'render', '实验记录', child=True)['content'])
        text = '---\ntype: note\nstatus: draft\nproject_id: "PROJECT_ID"\ncreated: {{date:YYYY-MM-DD}}\n---\n# {{title}}\n'
        template = self.write_input('template.md', text)
        added = self.call('template', 'add', '原生测试模板', '--body-file', template)
        self.call('template', 'set', '原生测试模板', '--body-file', template, '--if-hash', added['hash'])
        deletion = self.call('template', 'delete', '原生测试模板')
        self.call('trash', 'restore', deletion['operation_id'])
        self.call('agents', 'template-get', 'main')
        project_template = self.call('agents', 'template-get', 'project')
        self.call('agents', 'template-set', 'project', '--body-file', self.write_input('agent-template.txt', project_template['content']), '--if-hash', project_template['hash'])
        self.call('agents', 'customize', '--body-file', self.write_input('local-agent.txt', '本项目补充，不放宽主规则。'), child=True)
        self.call('agents', 'show', '--local', child=True)
        previous = self.call('agents', 'show', child=True)
        self.call('agents', 'render', child=True)
        self.call('agents', 'apply', '--if-hash', previous['hash'], child=True)
        self.call('index', 'list', child=True)
        index = self.call('index', 'get', 'P001-实验', child=True)
        self.call('index', 'set', 'P001-实验', '--body-file', self.write_input('index.txt', index['content']), '--if-hash', index['hash'], child=True)
        self.call('blueprint', 'show')
        self.call('blueprint', 'validate')
        source = self.root / '规则与模板/项目文件夹模板/标准科研项目/95 索引/PROJECT_ID-结果.base.template'
        import hashlib
        self.call('blueprint', 'set-file', '95 索引/PROJECT_ID-结果.base.template', '--body-file', self.write_input('base.txt', source.read_text(encoding='utf-8')), '--if-hash', hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(self.call('check')['errors'], [])

    def test_project_crud_and_sync(self):
        self.call('project', 'create', '--category', '脑机接口', '--name', 'P002-脑电研究', '--id', 'P002')
        self.assertEqual(len(self.call('project', 'list')), 2)
        project = self.call('project', 'get', 'P002')
        changed = self.call('--project', 'P002', 'project', 'status', 'active', '--if-hash', project['hash'])
        self.call('--project', 'P002', 'project', 'edit', '--if-hash', changed['hash'], '--set', 'next_step=整理证据')
        self.call('sync', 'show')
        self.call('sync', 'rebuild')
        self.call('sync', 'apply')
        deletion = self.call('project', 'delete', 'P002')
        self.call('trash', 'restore', deletion['operation_id'])
        self.call('history', 'list')
        self.assertEqual(self.call('check')['errors'], [])

if __name__ == '__main__':
    unittest.main()
