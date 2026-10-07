"""Public GitHub Releases updates with verified downloads and Windows handoff."""
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from . import __version__
from .context import VaultError
from .storage import atomic, digest, json_write, stamp

REPOSITORY = 'SuShuheng/scientific-vault-cli'
PRODUCT = 'scientific-vault-cli'
ASSET = 'svault-windows-x64.exe'
MAX_EXE = 100 * 1024 * 1024
STATUS_NAME = '.svault-update.json'
HOSTS = {'api.github.com', 'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com'}

def semver(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value)
    if not match:
        raise VaultError('只支持 x.y.z 或 vx.y.z 稳定版本号', 'invalid_release')
    return tuple(map(int, match.groups()))

def trusted_url(url):
    value = urlparse(url)
    if value.scheme != 'https' or value.hostname not in HOSTS or value.username or value.password or value.port not in (None, 443):
        raise VaultError('Release 下载地址不是受支持的 GitHub HTTPS 地址', 'invalid_release')

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class GitHubClient:
    def get(self, url, limit):
        trusted_url(url)
        request = Request(url, headers={'User-Agent': 'svault/' + __version__, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with build_opener(SafeRedirect()).open(request, timeout=30) as response:
                trusted_url(response.geturl())
                result = response.read(limit + 1)
        except HTTPError as exc:
            message = '未找到公开仓库的指定 Release' if exc.code == 404 else 'GitHub 请求失败，HTTP ' + str(exc.code)
            raise VaultError(message, 'github_error')
        except URLError:
            raise VaultError('无法连接 GitHub，请检查网络后重试', 'github_error')
        if len(result) > limit:
            raise VaultError('下载文件超出大小限制', 'invalid_release')
        return result

def release_info(repository=REPOSITORY, tag=None, client=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise VaultError('仓库格式应为 owner/repo')
    if tag:
        semver(tag)
    client = client or GitHubClient()
    endpoint = 'tags/' + quote(tag, safe='') if tag else 'latest'
    data = json.loads(client.get(f'https://api.github.com/repos/{repository}/releases/{endpoint}', 2 * 1024 * 1024))
    if data.get('draft') or data.get('prerelease'):
        raise VaultError('不安装草稿或预发布版本', 'invalid_release')
    version = data.get('tag_name', '')
    semver(version)
    assets = [a for a in data.get('assets', []) if a.get('name') == ASSET]
    if len(assets) != 1:
        raise VaultError('Release 必须包含唯一的 ' + ASSET, 'invalid_release')
    asset = assets[0]
    url = asset.get('browser_download_url', '')
    trusted_url(url)
    prefix = f'https://github.com/{repository}/releases/download/{version}/'
    if not url.casefold().startswith(prefix.casefold()) or urlparse(url).path.rsplit('/', 1)[-1] != ASSET:
        raise VaultError('exe 资产与指定仓库和 Release 不匹配', 'invalid_release')
    checksum = asset.get('digest', '')
    match = re.fullmatch(r'sha256:([0-9a-fA-F]{64})', checksum or '')
    if not match:
        sums = [a for a in data.get('assets', []) if a.get('name') == 'SHA256SUMS.txt']
        if len(sums) != 1:
            raise VaultError('Release 缺少 SHA256 校验值', 'invalid_release')
        sums_url = sums[0].get('browser_download_url', '')
        trusted_url(sums_url)
        if not sums_url.casefold().startswith(prefix.casefold()):
            raise VaultError('校验文件与 Release 不匹配', 'invalid_release')
        text = client.get(sums_url, 1024 * 1024).decode('utf-8')
        entries = [m for m in re.finditer(r'^([0-9a-fA-F]{64})\s+\*?([^\r\n]+)$', text, re.M) if m.group(2).strip() == ASSET]
        if len(entries) != 1:
            raise VaultError('校验文件未包含唯一的 exe 校验值', 'invalid_release')
        expected = entries[0].group(1).lower()
    else:
        expected = match.group(1).lower()
    size = asset.get('size')
    if not isinstance(size, int) or not (0 < size <= MAX_EXE):
        raise VaultError('Release exe 大小无效', 'invalid_release')
    return {'repository': repository, 'version': version.lstrip('v'), 'tag': version, 'url': url, 'sha256': expected, 'size': size, 'release_url': f'https://github.com/{repository}/releases/tag/{version}'}

def executable():
    return Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None

def version_info():
    return {'product': PRODUCT, 'version': __version__, 'repository': REPOSITORY, 'platform': sys.platform, 'executable': str(executable()) if executable() else None}

def check_update(repository=REPOSITORY, tag=None, client=None):
    info = release_info(repository, tag, client)
    return {**info, 'current_version': __version__, 'update_available': semver(info['version']) > semver(__version__)}

def update_status(target=None):
    target = target or executable()
    if not target:
        return {'status': 'source_mode', 'message': '源码模式没有原生 exe 升级状态'}
    path = target.parent / STATUS_NAME
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status': 'none'}

HELPER = r'''param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference = 'Stop'
$c = [System.IO.File]::ReadAllText($Config, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
function File-Sha256([string]$path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Save-State([string]$status, [string]$message) {
    $record = @{status=$status; message=$message; version=$c.version; repository=$c.repository; target=$c.target; backup=$c.backup; sha256=$c.sha256; updated=(Get-Date).ToUniversalTime().ToString('o')}
    [System.IO.File]::WriteAllText($c.status_path, ($record | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
}
$replaced = $false
try {
    $deadline = (Get-Date).AddSeconds(45)
    while (Get-Process -Id $c.parent_pid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw '原进程尚未退出，未替换 exe' }
        Start-Sleep -Milliseconds 200
    }
    $target = [System.IO.Path]::GetFullPath($c.target)
    $source = [System.IO.Path]::GetFullPath($c.source)
    $backup = [System.IO.Path]::GetFullPath($c.backup)
    if ([System.IO.Path]::GetDirectoryName($source) -ne [System.IO.Path]::GetDirectoryName($target) -or [System.IO.Path]::GetDirectoryName($backup) -ne [System.IO.Path]::GetDirectoryName($target)) { throw '升级路径边界无效' }
    if ((File-Sha256 $source) -ne $c.sha256) { throw '下载文件在校验后发生变化' }
    if ((File-Sha256 $target) -ne $c.old_hash) { throw '当前 exe 已被其他操作修改' }
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        try { [System.IO.File]::Replace($source, $target, $backup, $true); $replaced=$true; break }
        catch { if ($attempt -eq 59) { throw }; Start-Sleep -Milliseconds 250 }
    }
    $output = & $target version
    if ($LASTEXITCODE -ne 0) { throw '新 exe 启动验证失败' }
    $probe = ($output -join "`n") | ConvertFrom-Json
    if (-not $probe.ok -or $probe.result.version -ne $c.version -or $probe.result.product -ne 'scientific-vault-cli') { throw '新 exe 的产品或版本不匹配' }
    Save-State 'complete' 'exe 已完成覆盖式升级，旧版备份已保留'
} catch {
    if ($replaced -and [System.IO.File]::Exists($c.backup)) {
        try { [System.IO.File]::Copy($c.backup, $c.target, $true) }
        catch { Save-State 'rollback_failed' '升级失败，旧版备份仍保留，请手动恢复'; exit 1 }
    }
    Save-State 'failed' $_.Exception.Message
    exit 1
}
'''

def prepare_update(target, info, client=None, force=False, parent_pid=None):
    """Validate the candidate and start a hidden helper; the caller must exit."""
    if sys.platform != 'win32':
        raise VaultError('当前原生覆盖式升级仅支持 Windows')
    target = Path(target).resolve()
    if not target.is_file() or target.suffix.lower() != '.exe':
        raise VaultError('当前 exe 路径无效')
    comparison = semver(info['version'])
    if comparison < semver(__version__):
        raise VaultError('禁止通过升级命令降级', 'invalid_release')
    if comparison == semver(__version__) and not force:
        return {'status': 'up_to_date', 'version': __version__}
    client = client or GitHubClient()
    content = client.get(info['url'], MAX_EXE)
    if len(content) != info['size'] or hashlib.sha256(content).hexdigest() != info['sha256']:
        raise VaultError('Release exe 的大小或 SHA256 校验失败，原程序未修改', 'checksum_mismatch')
    if len(content) < 64 or not content.startswith(b'MZ'):
        raise VaultError('下载内容不是 Windows exe', 'invalid_release')
    ticket = uuid.uuid4().hex
    source = target.parent / ('.svault-download-' + ticket + '.exe')
    atomic(source, content)
    try:
        probe = subprocess.run([str(source), 'version'], cwd=tempfile.gettempdir(), capture_output=True, text=True, encoding='utf-8', timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = json.loads(probe.stdout)
        if probe.returncode or not result.get('ok') or result.get('result', {}).get('product') != PRODUCT or result.get('result', {}).get('version') != info['version']:
            raise VaultError('新 exe 的产品或版本验证失败，原程序未修改', 'invalid_release')
    except Exception:
        source.unlink(missing_ok=True)
        raise
    work = Path(tempfile.mkdtemp(prefix='svault-update-'))
    script = work / 'apply.ps1'
    script.write_text(HELPER, encoding='utf-8-sig')
    backup = target.parent / (target.name + '.backup-' + __version__ + '-' + ticket[:8])
    status_path = target.parent / STATUS_NAME
    config = {'target': str(target), 'source': str(source), 'backup': str(backup), 'status_path': str(status_path), 'old_hash': digest(target), 'sha256': info['sha256'], 'version': info['version'], 'repository': info['repository'], 'parent_pid': parent_pid or os.getpid()}
    json_write(work / 'config.json', config)
    json_write(status_path, {'status': 'pending', 'version': info['version'], 'created': stamp(), 'target': str(target), 'backup': str(backup)})
    try:
        helper_env = dict(os.environ)
        # The helper outlives the frozen parent; its probe must unpack a fresh app.
        helper_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', str(script), '-Config', str(work / 'config.json')], cwd=work, env=helper_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        source.unlink(missing_ok=True)
        json_write(status_path, {'status': 'failed', 'message': '无法启动覆盖升级辅助进程，原程序未修改'})
        raise
    return {'status': 'pending', 'version': info['version'], 'helper_pid': process.pid, 'status_path': str(status_path), 'message': '命令退出后辅助进程将覆盖 exe；稍后运行 update status 核实结果'}

def apply_update(repository=REPOSITORY, tag=None, force=False):
    target = executable()
    if not target:
        raise VaultError('源码模式不能覆盖 exe；请使用打包后的程序运行 update apply')
    info = release_info(repository, tag)
    return prepare_update(target, info, force=force)
