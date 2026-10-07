from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from svault import updater
from svault.cli import run
from svault.context import VaultError

class MemoryClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def get(self, url, limit):
        value = self.mapping[url]
        if len(value) > limit:
            raise VaultError('size exceeded')
        return value

def release(digest=True):
    content = b'MZ' + b'test' * 30
    prefix = 'https://github.com/SuShuheng/scientific-vault-cli/releases/download/v1.1.0/'
    entry = {'tag_name': 'v1.1.0', 'draft': False, 'prerelease': False, 'assets': [{'name': updater.ASSET, 'size': len(content), 'browser_download_url': prefix + updater.ASSET}]}
    sha = hashlib.sha256(content).hexdigest()
    if digest:
        entry['assets'][0]['digest'] = 'sha256:' + sha
    else:
        entry['assets'].append({'name': 'SHA256SUMS.txt', 'browser_download_url': prefix + 'SHA256SUMS.txt'})
    mapping = {'https://api.github.com/repos/' + updater.REPOSITORY + '/releases/latest': json.dumps(entry).encode(), prefix + updater.ASSET: content, prefix + 'SHA256SUMS.txt': (sha + '  ' + updater.ASSET + '\n').encode()}
    return entry, mapping

class UpdateTests(unittest.TestCase):
    def test_version_works_without_vault(self):
        self.assertEqual(run(['version'], cwd=tempfile.gettempdir())['version'], '1.1.0')

    def test_latest_metadata_and_digest(self):
        _, data = release()
        info = updater.release_info(client=MemoryClient(data))
        self.assertEqual(info['version'], '1.1.0')
        self.assertEqual(len(info['sha256']), 64)

    def test_checksum_fallback(self):
        _, data = release(False)
        self.assertEqual(len(updater.release_info(client=MemoryClient(data))['sha256']), 64)

    def test_draft_prerelease_wrong_host_missing_digest_rejected(self):
        for kind in ('draft', 'prerelease', 'host', 'digest'):
            entry, data = release()
            if kind in ('draft', 'prerelease'):
                entry[kind] = True
            elif kind == 'host':
                entry['assets'][0]['browser_download_url'] = 'https://evil.example/svault.exe'
            else:
                entry['assets'][0].pop('digest')
            key = 'https://api.github.com/repos/' + updater.REPOSITORY + '/releases/latest'
            data[key] = json.dumps(entry).encode()
            with self.assertRaises(VaultError):
                updater.release_info(client=MemoryClient(data))

    def test_bad_semver_and_unsafe_redirect(self):
        for value in ('v1.1.0;script', '../v1.1.0', '1.0', '01.1.0', 'v1.1.0-beta'):
            with self.assertRaises(VaultError):
                updater.semver(value)
        for url in ('http://github.com/file', 'https://evil.example/file', 'https://user:pass@github.com/file'):
            with self.assertRaises(VaultError):
                updater.trusted_url(url)

    def test_download_checksum_failure_preserves_original(self):
        with tempfile.TemporaryDirectory(prefix='svault-update-test-') as folder:
            target = Path(folder) / 'svault.exe'
            target.write_bytes(b'original')
            _, data = release()
            info = updater.release_info(client=MemoryClient(data))
            data[info['url']] = b'MZ' + b'wrong'
            with self.assertRaises(VaultError):
                updater.prepare_update(target, info, MemoryClient(data), force=True)
            self.assertEqual(target.read_bytes(), b'original')

    def test_same_version_no_overwrite_without_force(self):
        with tempfile.TemporaryDirectory(prefix='svault-update-test-') as folder:
            target = Path(folder) / 'svault.exe'
            target.write_bytes(b'original')
            _, data = release()
            info = updater.release_info(client=MemoryClient(data))
            self.assertEqual(updater.prepare_update(target, info, MemoryClient(data))['status'], 'up_to_date')
            self.assertEqual(target.read_bytes(), b'original')

    def test_child_cannot_apply_update(self):
        with patch('svault.cli.main_vault', return_value=Path('main')), patch('svault.cli.Context.resolve') as context:
            context.return_value.require_main.side_effect = VaultError('子库禁止', 'permission_denied')
            with self.assertRaises(VaultError):
                run(['update', 'apply'])

    def test_real_windows_helper_replaces_verified_native_exe(self):
        self.native_helper_success(simulate_frozen=False)

    def test_helper_resets_inherited_frozen_parent_environment(self):
        self.native_helper_success(simulate_frozen=True)

    def native_helper_success(self, simulate_frozen):
        native = os.environ.get('SVAULT_EXE')
        if not native:
            self.skipTest('须构建 exe 后设置 SVAULT_EXE')
        with tempfile.TemporaryDirectory(prefix='svault-native-update-') as folder:
            base = Path(folder).resolve()
            self.assertTrue(base.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            target = base / '含 空格的svault.exe'
            target.write_bytes(Path(native).read_bytes())
            driver = base / 'driver.py'
            driver.write_text('''import sys,json,hashlib,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from svault import updater
binary=Path(sys.argv[2]).read_bytes()
class Client:
    def get(self,url,limit): return binary
info={'version':'1.1.0','repository':updater.REPOSITORY,'sha256':hashlib.sha256(binary).hexdigest(),'size':len(binary),'url':'https://github.com/SuShuheng/scientific-vault-cli/releases/download/v1.1.0/svault-windows-x64.exe'}
result=updater.prepare_update(Path(sys.argv[3]),info,Client(),force=True)
print(json.dumps(result))
''', encoding='utf-8')
            env = dict(os.environ)
            if simulate_frozen:
                env['_PYI_ARCHIVE_FILE'] = str(target)
                env['_PYI_APPLICATION_HOME_DIR'] = str(base / 'already-removed-unpack-directory')
                env['_PYI_PARENT_PROCESS_LEVEL'] = '2'
            process = subprocess.run([sys.executable, str(driver), str(REPO / 'src'), native, str(target)], capture_output=True, encoding='utf-8', timeout=30, env=env)
            self.assertEqual(process.returncode, 0, process.stderr)
            deadline = time.monotonic() + 30
            result = {'status': 'pending'}
            while time.monotonic() < deadline:
                try:
                    result = updater.update_status(target)
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.1)
                    continue
                if result['status'] != 'pending':
                    break
                time.sleep(0.1)
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), hashlib.sha256(Path(native).read_bytes()).hexdigest())
            self.assertTrue(Path(result['backup']).is_file())

    def test_native_helper_rolls_back_failed_post_install_probe(self):
        native = os.environ.get('SVAULT_EXE')
        if not native:
            self.skipTest('须构建 exe 后设置 SVAULT_EXE')
        with tempfile.TemporaryDirectory(prefix='svault-rollback-test-') as folder:
            base = Path(folder).resolve()
            self.assertTrue(base.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            target = base / 'svault.exe'
            original = Path(native).read_bytes()
            target.write_bytes(original)
            driver = base / 'driver.py'
            driver.write_text('''import sys,json,hashlib
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from svault import updater
binary=Path(sys.argv[2]).read_bytes()
class Client:
    def get(self,url,limit): return binary
info={'version':'1.1.1','repository':updater.REPOSITORY,'sha256':hashlib.sha256(binary).hexdigest(),'size':len(binary),'url':'https://github.com/SuShuheng/scientific-vault-cli/releases/download/v1.1.1/svault-windows-x64.exe'}
probe=SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'product':'scientific-vault-cli','version':'1.1.1'}}))
with patch.object(updater.subprocess,'run',return_value=probe):
    print(json.dumps(updater.prepare_update(Path(sys.argv[3]),info,Client())))
''', encoding='utf-8')
            proc = subprocess.run([sys.executable, str(driver), str(REPO / 'src'), native, str(target)], capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            deadline = time.monotonic() + 30
            result = {'status': 'pending'}
            while time.monotonic() < deadline:
                try:
                    result = updater.update_status(target)
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.1)
                    continue
                if result['status'] != 'pending':
                    break
                time.sleep(0.1)
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(Path(result['backup']).exists())

if __name__ == '__main__':
    unittest.main()
