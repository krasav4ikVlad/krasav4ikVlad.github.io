<#
  nodewiki residential probe — установка зонда как Windows-сервиса
  (через Планировщик заданий: автозапуск при загрузке + перезапуск при падении).

  Зонд берёт у чекера ноды, поднимает через них туннель (xray) и проверяет,
  открываются ли YouTube/ChatGPT/Telegram/Instagram ИМЕННО С ЭТОЙ машины.
  Ставьте на сервер/ПК, где иностранные сервисы заблокированы (как у конечного
  пользователя) и где НЕТ активного VPN/прокси.

  Запуск в PowerShell ОТ ИМЕНИ АДМИНИСТРАТОРА:

    $env:AGENT_TOKEN="...тот же, что CHECKER_AGENT_TOKEN на чекере..."
    irm https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy-probe.ps1 | iex

  Токен: на чекере его печатает deploy-checker.sh, либо:
    grep CHECKER_AGENT_TOKEN /opt/nodewiki-checker/nodewiki-checker.env

  Повторный запуск безопасен: обновляет код, токен берёт из сохранённого .cmd.
#>

[CmdletBinding()]
param(
  [string]$AgentToken   = $env:AGENT_TOKEN,
  [string]$CheckerUrl   = $(if ($env:CHECKER_URL) { $env:CHECKER_URL } else { "https://checker.nodewiki.info" }),
  [string]$InstallDir   = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { "C:\nodewiki-probe" }),
  [string]$PollInterval = $(if ($env:POLL_INTERVAL) { $env:POLL_INTERVAL } else { "5" }),
  [string]$RawBase      = $(if ($env:RAW_BASE) { $env:RAW_BASE } else { "https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe" })
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
$TaskName = "nodewiki-probe"
$RunCmd   = Join-Path $InstallDir "run-probe.cmd"

# скачивание с обходом CDN-кеша raw.githubusercontent (иначе подтягивается старьё)
function Fetch ($url, $out) {
  $bust = $(if ($url -like "*`?*") { "&" } else { "?" }) + "cb=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  Invoke-WebRequest -UseBasicParsing -Uri ($url + $bust) -OutFile $out `
    -Headers @{ "Cache-Control" = "no-cache"; "Pragma" = "no-cache" }
}

function Log  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn ($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
# ВАЖНО: не exit — при запуске через `irm | iex` exit закрывает всё окно PowerShell
function Die  ($m) { Write-Host "[x] $m" -ForegroundColor Red; throw $m }

# ---- admin ------------------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Die "Запусти PowerShell от имени Администратора." }

# поиск python.exe и в текущем PATH, и по стандартным путям установки
function Find-Python {
  foreach ($cand in @("py", "python", "python3")) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c) {
      $ver = & $c.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
      if ($LASTEXITCODE -eq 0 -and $ver) { return $c.Source }
    }
  }
  $globs = @("C:\Program Files\Python3*\python.exe",
             "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe")
  foreach ($g in $globs) {
    $hit = Get-Item $g -ErrorAction SilentlyContinue |
           Sort-Object Name -Descending | Select-Object -First 1
    if ($hit) { return $hit.FullName }
  }
  return $null
}

# ---- токен (при повторном запуске берём из существующего run-probe.cmd) ------
if (-not $AgentToken -and (Test-Path $RunCmd)) {
  $m = Select-String -Path $RunCmd -Pattern '^\s*set\s+"AGENT_TOKEN=(.+)"\s*$'
  if ($m) { $AgentToken = $m.Matches[0].Groups[1].Value }
}
if (-not $AgentToken) {
  Die @"
AGENT_TOKEN обязателен (тот же, что CHECKER_AGENT_TOKEN на чекере). Например:
  `$env:AGENT_TOKEN="xxxxxxxx"; irm $RawBase/deploy-probe.ps1 | iex
"@
}

# ---- предупреждение, если канал датацентровый -------------------------------
try {
  $hosting = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 `
              -Uri "http://ip-api.com/line/?fields=hosting").Content.Trim()
  if ($hosting -eq "true") {
    Warn "Похоже, машина в датацентре. Зонд полезен только если с неё реально заблокированы иностранные сервисы (ты это проверил — ок)."
  }
} catch { }

# ---- Python -----------------------------------------------------------------
$py = Find-Python
if (-not $py) {
  $wg = Get-Command winget -ErrorAction SilentlyContinue
  if ($wg) {
    Log "Python не найден — пробую поставить через winget…"
    try {
      winget install -e --id Python.Python.3.12 --silent `
        --accept-package-agreements --accept-source-agreements | Out-Null
    } catch { Warn "winget не справился — поставлю напрямую с python.org." }
    $py = Find-Python
  }
}
if (-not $py) {
  # Windows Server: winget обычно нет — ставим официальный инсталлятор тихо
  Log "Python не найден — скачиваю установщик с python.org (тихая установка)…"
  $pyExe = Join-Path $env:TEMP "python-installer.exe"
  Invoke-WebRequest -UseBasicParsing `
    -Uri "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" `
    -OutFile $pyExe
  Start-Process -FilePath $pyExe -Wait `
    -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1"
  Remove-Item $pyExe -Force -ErrorAction SilentlyContinue
  $py = Find-Python
}
if (-not $py) {
  Die "Python 3 так и не нашёлся. Установи вручную (https://www.python.org/downloads/ — отметь 'Add to PATH'), открой НОВОЕ окно PowerShell и запусти скрипт снова."
}
Log "Python: $py"

# ---- каталог + код ----------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Log "Скачиваю probe_agent.py…"
Fetch "$RawBase/probe_agent.py" (Join-Path $InstallDir "probe_agent.py")
& $py -c "compile(open(r'$InstallDir\probe_agent.py',encoding='utf-8').read(),'p','exec')"
if ($LASTEXITCODE -ne 0) { Die "probe_agent.py не парсится." }

# ---- venv + httpx[socks] ----------------------------------------------------
$venv   = Join-Path $InstallDir "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Log "Создаю venv…"; & $py -m venv $venv }
Log "Ставлю зависимости (httpx[socks])…"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet "httpx[socks]"

# ---- xray-core (Windows) ----------------------------------------------------
$xray = Join-Path $InstallDir "xray.exe"
if (-not (Test-Path $xray)) {
  Log "Скачиваю xray-core (Windows)…"
  $zip = Join-Path $env:TEMP "xray-win64.zip"
  Invoke-WebRequest -UseBasicParsing `
    -Uri "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip" `
    -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath $InstallDir -Force
  Remove-Item $zip -Force -ErrorAction SilentlyContinue
  if (-not (Test-Path $xray)) { Die "xray.exe не распаковался — без него зонд не поднимет туннель." }
}

# ---- launcher .cmd (env + запуск, лог в файл) -------------------------------
Log "Пишу run-probe.cmd…"
@"
@echo off
set "CHECKER_URL=$CheckerUrl"
set "AGENT_TOKEN=$AgentToken"
set "XRAY_BIN=$xray"
set "POLL_INTERVAL=$PollInterval"
set "PYTHONIOENCODING=utf-8"
"$venvPy" "$InstallDir\probe_agent.py" >> "$InstallDir\probe.log" 2>&1
"@ | Set-Content -Path $RunCmd -Encoding ASCII

# токен лежит в .cmd в открытом виде — ограничим доступ (только SYSTEM + админы);
# SID вместо имён групп, чтобы работало и на русской локали Windows
icacls $RunCmd /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "Не удалось ужесточить ACL на run-probe.cmd (не критично)." }

# ---- задача планировщика: автозапуск + перезапуск при сбое ------------------
# при переустановке гасим старый экземпляр, чтобы новый код подхватился сразу
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*$InstallDir*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Log "Регистрирую задачу '$TaskName' (автозапуск при загрузке)…"
$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$RunCmd`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -MultipleInstances IgnoreNew -RestartCount 999 `
              -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null

Log "Запускаю зонд…"
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $TaskName).State

Write-Host ""
Log "Зонд установлен."
Write-Host "  Состояние задачи: $state (Running = работает)"
Write-Host "  Чекер:   $CheckerUrl (задачи берутся автоматически)"
Write-Host "  Лог:     Get-Content -Wait -Encoding UTF8 '$InstallDir\probe.log'"
Write-Host "  Стоп:    Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  Старт:   Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Удалить: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "  Напоминание: на этой машине не должно быть активного VPN/прокси."
