$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    python -m PyInstaller --onefile --name svault --version-file version_info.txt --add-data "src/svault/assets;svault/assets" --paths src --distpath dist --workpath build --specpath . run_svault.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    & '.\dist\svault.exe' --version
    if ($LASTEXITCODE -ne 0) { throw 'Executable verification failed' }
} finally { Pop-Location }
