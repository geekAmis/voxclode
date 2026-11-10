param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venv = Join-Path $root ".venv"
$bundled = Join-Path $root "runtime\python\python.exe"

if ($Force -and (Test-Path $venv)) {
    Remove-Item -Recurse -Force $venv
}

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    if (Test-Path $bundled) {
        Write-Host "Использую локальный Python: $bundled"
        & $bundled -m venv $venv
    } else {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($py) {
            Write-Host "Создаю .venv через py -3.12"
            & py -3.12 -m venv $venv
        } else {
            $python = Get-Command python3.12 -ErrorAction SilentlyContinue
            if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
            if (-not $python) {
                throw "Python 3.10–3.12 не найден. Установите Python 3.12 и повторите."
            }
            Write-Host "Создаю .venv через $($python.Source)"
            & $python.Source -m venv $venv
        }
    }
}

$pythonExe = Join-Path $venv "Scripts\python.exe"
$version = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -notin @("3.10", "3.11", "3.12")) {
    throw "Найден Python $version, а VoxCPM требует 3.10–3.12. Установите Python 3.12 и пересоздайте .venv."
}
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $root "requirements.txt")
Write-Host "Готово. Запуск: .\run-voxclode.ps1"
