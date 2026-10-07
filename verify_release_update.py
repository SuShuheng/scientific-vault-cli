"""Verify a native parent downloading a public Release, in an isolated directory."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time

p = argparse.ArgumentParser()
p.add_argument('--exe', type=Path, required=True)
args = p.parse_args()
with tempfile.TemporaryDirectory(prefix='svault-real-release-') as folder:
    root = Path(folder).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
    target = root / 'svault.exe'
    shutil.copy2(args.exe, target)
    result = subprocess.run([str(target), 'update', 'apply', '--force'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr)
    receipt = json.loads(result.stdout)['result']
    deadline = time.monotonic() + 50
    status = {'status': 'pending'}
    while time.monotonic() < deadline:
        try:
            status = json.loads(Path(receipt['status_path']).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if status['status'] != 'pending':
            break
        time.sleep(0.1)
    assert status['status'] == 'complete', status
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    assert actual == status['sha256']
    assert Path(status['backup']).is_file()
    probe = subprocess.run([str(target), 'version'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert probe.returncode == 0, probe.stderr
    print(json.dumps({'real_github_update': status['status'], 'sha256': actual, 'version': json.loads(probe.stdout)['result']['version']}, ensure_ascii=False))
