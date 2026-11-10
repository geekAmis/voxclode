param(
    [string]$RepoName = "voxclode",
    [string]$Description = "VoxCLode — colourful terminal studio for VoxCPM2 voice cloning",
    [switch]$Private
)

$ErrorActionPreference = "Stop"

$token = $env:GH_TOKEN
if (-not $token) {
    throw "Задайте GH_TOKEN в окружении. Не вставляйте токен в файлы проекта."
}

$headers = @{
    Authorization          = "Bearer $token"
    Accept                 = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent"           = "voxclode-publisher"
}

Write-Host "Проверяю токен и пользователя…"
$user = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers
$owner = $user.login
Write-Host "Пользователь: $owner"

$body = @{
    name             = $RepoName
    description      = $Description
    private          = [bool]$Private
    has_issues       = $true
    has_wiki         = $false
    license_template = "mit"
} | ConvertTo-Json

try {
    $repo = Invoke-RestMethod -Method Post -Uri "https://api.github.com/user/repos" `
        -Headers $headers -Body $body -ContentType "application/json"
    Write-Host "Создан репозиторий: $($repo.html_url)"
} catch {
    Write-Host "Репозиторий уже существует или API вернул ошибку; продолжаю с $owner/$RepoName"
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".git")) {
    git init -b main | Out-Null
}

git add -A
$staged = git diff --cached --name-only
if ($staged) {
    git -c user.name="VoxCLode" -c user.email="voxclode@users.noreply.github.com" `
        commit -m "VoxCLode: initial public release" | Out-Null
}

git branch -M main
$remotes = git remote
if ($remotes -contains "origin") {
    git remote remove origin
}
git remote add origin "https://github.com/$owner/$RepoName.git"

# Токен передаётся только в заголовке текущей команды и не сохраняется в .git/config.
git -c http.extraheader="AUTHORIZATION: bearer $token" push -u origin main

Write-Host "Готово: https://github.com/$owner/$RepoName"
