param(
    [string]$OutputPath = "",
    [string]$TextFile = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $OutputPath) { $OutputPath = Join-Path $root "audio\reference_ru.wav" }
if (-not $TextFile)   { $TextFile   = Join-Path $root "audio\reference_text_ru.txt" }

$text = (Get-Content -Path $TextFile -Raw -Encoding UTF8).Trim()
if (-not $text) { throw "Reference text file is empty: $TextFile" }

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

# Prefer a Russian voice when the machine has one installed.
$russian = $synth.GetInstalledVoices() |
    Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like "ru-*" } |
    Select-Object -First 1
if ($russian) {
    $synth.SelectVoice($russian.VoiceInfo.Name)
    Write-Host "Using voice: $($russian.VoiceInfo.Name) ($($russian.VoiceInfo.Culture))"
} else {
    Write-Warning "No Russian SAPI voice found; using the system default voice."
}

# VoxCPM expects a short 16 kHz mono reference clip.
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)

$parent = Split-Path -Parent $OutputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

$synth.SetOutputToWaveFile($OutputPath, $format)
$synth.Speak($text)
$synth.SetOutputToNull()
$synth.Dispose()

$file = Get-Item $OutputPath
Write-Host ("Wrote {0} ({1:N0} bytes)" -f $file.FullName, $file.Length)
