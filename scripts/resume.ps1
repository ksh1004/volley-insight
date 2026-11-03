# Bring the whole dev environment back after the PC was turned off, and show where the pipeline stands.
#
#   powershell -ExecutionPolicy Bypass -File scripts\resume.ps1             # start + status
#   powershell -ExecutionPolicy Bypass -File scripts\resume.ps1 -Backfill   # also re-run an unfinished backfill
#
# Data survives a shutdown (Docker volumes). raw-archiver is re-submitted by the flink-job-keeper service.
# A re-run backfill skips games that are already CONFIRMED, so it only fetches what is still missing.
param(
    [switch]$Backfill,
    [string]$Seasons = "001-021"
)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== 1. Docker" -ForegroundColor Cyan
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop is not running. Start it, wait until it says 'Engine running', then run this again." -ForegroundColor Yellow
    exit 1
}
docker compose --profile app --profile batch --profile stream up -d

Write-Host "`n== 2. Waiting for Airflow (up to 3 min)" -ForegroundColor Cyan
for ($i = 0; $i -lt 36; $i++) {
    docker compose exec -T airflow airflow dags list -o plain 2>$null | Select-String "kovo_backfill" -Quiet | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
}

Write-Host "`n== 3. Kafka -> HDFS archiving (Flink raw-archiver)" -ForegroundColor Cyan
for ($i = 0; $i -lt 18; $i++) {
    $jobs = docker compose exec -T flink-jobmanager flink list -r 2>$null
    if ($jobs -match "raw-archiver \(RUNNING\)") { Write-Host "raw-archiver RUNNING"; break }
    Start-Sleep -Seconds 10
}
if (-not ($jobs -match "raw-archiver \(RUNNING\)")) { Write-Host "raw-archiver not running yet; see: docker compose logs flink-job-keeper" -ForegroundColor Yellow }
# RUNNING is not enough: without completed checkpoints no file is finished and nothing reaches HDFS.
$ok = $false
for ($i = 0; $i -lt 12 -and -not $ok; $i++) {
    try {
        $job = (Invoke-RestMethod http://localhost:8081/jobs/overview).jobs | Where-Object { $_.name -eq "raw-archiver" -and $_.state -eq "RUNNING" } | Select-Object -First 1
        if ($job) {
            $done = (Invoke-RestMethod "http://localhost:8081/jobs/$($job.jid)/checkpoints").counts.completed
            if ($done -gt 0) { Write-Host "checkpoints completed: $done (archiving works)"; $ok = $true }
        }
    } catch { }
    if (-not $ok) { Start-Sleep -Seconds 10 }
}
if (-not $ok) { Write-Host "no completed checkpoint yet; HDFS archiving is not working. Check: docker compose logs flink-taskmanager" -ForegroundColor Yellow }

Write-Host "`n== 4. Latest DAG runs" -ForegroundColor Cyan
foreach ($d in "kovo_backfill", "kovo_daily", "kovo_postgame") {
    $row = (docker compose exec -T airflow airflow dags list-runs $d -o plain 2>$null | Select-Object -Skip 1 -First 1)
    Write-Host ("{0,-14} {1}" -f $d, $row)
}

Write-Host "`n== 5. Matches in the database by season and status" -ForegroundColor Cyan
# SQL goes in through stdin: Windows PowerShell 5.1 strips double quotes inside native-command arguments.
$sql = "SELECT s.code, m.status, count(*) FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id GROUP BY 1, 2 ORDER BY 1, 2;"
$sql | docker compose exec -T postgres sh -c 'psql -U $POSTGRES_USER -d service -At'

if ($Backfill) {
    $state = (docker compose exec -T airflow airflow dags list-runs kovo_backfill -o plain 2>$null | Select-Object -Skip 1 -First 1)
    if ($state -match " running ") {
        Write-Host "`nkovo_backfill is still running; leaving it alone." -ForegroundColor Yellow
    } else {
        Write-Host "`n== Re-running kovo_backfill for $Seasons (CONFIRMED games are skipped)" -ForegroundColor Cyan
        # backslash-escaped quotes survive Windows PowerShell 5.1's argument passing
        $conf = '{\"seasons\": \"' + $Seasons + '\"}'
        docker compose exec -T airflow airflow dags trigger kovo_backfill --conf $conf
    }
}
Write-Host "`nSite: http://localhost:5173   Airflow: http://localhost:8080"
