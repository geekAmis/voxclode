param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Keep model/cache data inside this project instead of the user profile.
$env:HF_HOME = Join-Path $root ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:MODELSCOPE_CACHE = Join-Path $root ".cache\modelscope"

$exe = Join-Path $root ".venv\Scripts\voxcpm.exe"
if (-not (Test-Path $exe)) {
    throw "voxcpm is not installed. Run: .\scripts\install.ps1"
}

& $exe @CliArgs
exit $LASTEXITCODE
