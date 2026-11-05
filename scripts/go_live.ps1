# Put the local Kavrelle site on the internet through a free Cloudflare quick tunnel.
# Starts the database, opens a tunnel, then starts the site with HTTPS-safe settings.
# The public address changes every time; it is shown at the end and saved to
# agentrel-results\public-url.txt. Close this window (or run with -Stop) to go offline.
param([switch]$Stop)

$repo = Split-Path -Parent $PSScriptRoot
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$uv = "$env:USERPROFILE\.local\bin\uv.exe"
$log = Join-Path $repo "agentrel-results\tunnel.log"
$urlFile = Join-Path $repo "agentrel-results\public-url.txt"

function Stop-Site {
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-CimInstance Win32_Process |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'kavrelle(\.exe)?"?\s+serve' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Stop-Site
if ($Stop) { Write-Host "Kavrelle is offline."; return }

Set-Location $repo
& "$repo\.venv\Scripts\python.exe" scripts\local_postgres.py start | Out-Null

Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -WindowStyle Hidden -FilePath $cloudflared -ArgumentList 'tunnel', '--no-autoupdate', '--url', 'http://localhost:8000', '--logfile', $log
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep 2
    $hit = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { $url = [regex]::Match($hit.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value }
}
if (-not $url) { Write-Host "The tunnel did not start. See $log"; exit 1 }

$env:KAVRELLE_PUBLIC_URL = $url
$env:KAVRELLE_SECURE_COOKIES = "1"
$env:KAVRELLE_TRUST_PROXY = "1"
Start-Process -WindowStyle Hidden -FilePath $uv -ArgumentList 'run', '--no-sync', 'kavrelle', 'serve' -WorkingDirectory $repo
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep 2
    try { if ((Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 10).StatusCode -eq 200) { break } } catch { }
}
Set-Content -Path $urlFile -Value $url
Write-Host "`nKavrelle is live at: $url`n(Keep this computer on. Run with -Stop to go offline.)"
Start-Process $url
