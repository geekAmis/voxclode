param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$env:HF_HOME = Join-Path $root ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:MODELSCOPE_CACHE = Join-Path $root ".cache\modelscope"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Окружение не найдено: $python. Сначала запустите .\scripts\install.ps1"
}

& $python (Join-Path $root "voxclode.py") @CliArgs
exit $LASTEXITCODE
