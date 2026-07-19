$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

if (-not (Test-Path -LiteralPath '.env')) {
    python manage.py init
    Write-Host "请编辑 $PSScriptRoot\.env，填写 New API、SMTP 和域名配置，然后重新运行 .\install.ps1"
    exit 0
}

python manage.py doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose pull docker-proxy
docker compose build monitor
docker compose up -d --remove-orphans
docker compose ps
