# PC를 껐다 켠 뒤 개발 환경 전체를 다시 띄우고, 파이프라인이 어디까지 와 있는지 보여 준다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\resume.ps1             # 시작 + 상태 확인
#   powershell -ExecutionPolicy Bypass -File scripts\resume.ps1 -Backfill   # 끝나지 않은 백필도 다시 실행
#
# 데이터는 꺼도 남는다(Docker 볼륨). raw-archiver, article-enricher, live-metrics는 flink-job-keeper가 다시 제출하고,
# live-collector는 오늘 경기를 알아서 찾는다.
# 다시 돌리는 백필은 이미 CONFIRMED인 경기를 건너뛰므로 아직 빠진 것만 받는다.
# 순서: 1 Docker 확인·전체 기동 → 2 Airflow 대기 → 3 Flink 보관 작업·체크포인트 확인 → 4 최근 DAG 실행
#       → 5 시즌·상태별 경기 수 → 6 기사·검색 색인 → 7 실시간 경기 → (선택) 백필
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
docker compose --profile app --profile batch --profile stream --profile search up -d

Write-Host "`n== 2. Waiting for Airflow (up to 3 min)" -ForegroundColor Cyan
for ($i = 0; $i -lt 36; $i++) {
    docker compose exec -T airflow airflow dags list -o plain 2>$null | Select-String "kovo_backfill" -Quiet | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
}

Write-Host "`n== 3. Kafka -> HDFS archiving (Flink raw-archiver)" -ForegroundColor Cyan
for ($i = 0; $i -lt 18; $i++) {
    $jobs = docker compose exec -T flink-jobmanager flink list -r 2>$null
    if (($jobs -match "raw-archiver \(RUNNING\)") -and ($jobs -match "article-enricher \(RUNNING\)") -and ($jobs -match "live-metrics \(RUNNING\)")) {
        Write-Host "raw-archiver, article-enricher, live-metrics RUNNING"; break
    }
    Start-Sleep -Seconds 10
}
if (-not ($jobs -match "raw-archiver \(RUNNING\)")) { Write-Host "raw-archiver not running yet; see: docker compose logs flink-job-keeper" -ForegroundColor Yellow }
# RUNNING만으로는 부족하다: 완료된 체크포인트가 없으면 파일이 마감되지 않아 HDFS에 아무것도 남지 않는다.
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
foreach ($d in "kovo_backfill", "kovo_daily", "kovo_postgame", "news_collect", "live_replay") {
    $row = (docker compose exec -T airflow airflow dags list-runs $d -o plain 2>$null | Select-Object -Skip 1 -First 1)
    Write-Host ("{0,-14} {1}" -f $d, $row)
}

Write-Host "`n== 5. Matches in the database by season and status" -ForegroundColor Cyan
# SQL은 표준 입력으로 넘긴다: Windows PowerShell 5.1은 외부 명령 인자 안의 큰따옴표를 지워 버린다.
$sql = "SELECT s.code, m.status, count(*) FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id GROUP BY 1, 2 ORDER BY 1, 2;"
$sql | docker compose exec -T postgres sh -c 'psql -U $POSTGRES_USER -d service -At'

Write-Host "`n== 6. Articles and search" -ForegroundColor Cyan
"SELECT 'articles in DB', count(*) FROM article;" | docker compose exec -T postgres sh -c 'psql -U $POSTGRES_USER -d service -At'
foreach ($i in "articles", "entities") {
    try { Write-Host ("Elasticsearch {0}: {1}" -f $i, (Invoke-RestMethod "http://localhost:9200/$i/_count").count) }
    catch { Write-Host "Elasticsearch $i not reachable yet" -ForegroundColor Yellow }
}

Write-Host "`n== 7. Live games" -ForegroundColor Cyan
docker compose logs --tail 3 live-collector
"SELECT m.status, count(*) FROM match m WHERE m.scheduled_at::date = current_date GROUP BY 1;" | docker compose exec -T postgres sh -c 'psql -U $POSTGRES_USER -d service -At'
try {
    $live = (Invoke-RestMethod http://localhost:8000/api/live).results
    Write-Host ("followed now: {0}" -f (($live | ForEach-Object { "$($_.match.key) $($_.state.mode) $($_.state.status)" }) -join ", "))
} catch { Write-Host "live API not reachable yet" -ForegroundColor Yellow }

if ($Backfill) {
    $state = (docker compose exec -T airflow airflow dags list-runs kovo_backfill -o plain 2>$null | Select-Object -Skip 1 -First 1)
    if ($state -match " running ") {
        Write-Host "`nkovo_backfill is still running; leaving it alone." -ForegroundColor Yellow
    } else {
        Write-Host "`n== Re-running kovo_backfill for $Seasons (CONFIRMED games are skipped)" -ForegroundColor Cyan
        # 역슬래시로 감싼 따옴표는 Windows PowerShell 5.1의 인자 전달에서도 살아남는다
        $conf = '{\"seasons\": \"' + $Seasons + '\"}'
        docker compose exec -T airflow airflow dags trigger kovo_backfill --conf $conf
    }
}
Write-Host "`nSite: http://localhost:5173   Airflow: http://localhost:8080"
