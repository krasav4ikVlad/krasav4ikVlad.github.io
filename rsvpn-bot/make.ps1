# Те же команды, что в Makefile, но для Windows PowerShell.
#
#   .\make.ps1 install    установить зависимости
#   .\make.ps1 check      проверить .env, базу и платёжки
#   .\make.ps1 migrate    индексы, тарифы, перенос коллекций
#   .\make.ps1 test       прогнать тесты
#   .\make.ps1 run        запустить бота
#   .\make.ps1 api        запустить приём вебхуков
#
# Если PowerShell откажется запускать скрипт, разрешите локальные скрипты:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'check', 'migrate', 'test', 'run', 'api', 'lint', 'fmt')]
    [string]$Task = 'check'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

switch ($Task) {
    'install' { pip install -e ".[dev]" }
    'check'   { python -m scripts.check_setup }
    'migrate' { python -m migrations.runner }
    'test'    { pytest -q }
    'run'     { python -m app.main_bot }
    'api'     {
        $port = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
        uvicorn app.main_api:app --host 0.0.0.0 --port $port
    }
    'lint'    { ruff check app tests }
    'fmt'     { ruff check --fix app tests }
}
