# 새 PC에서 scripts\migrate_export.ps1이 만든 폴더를 되살린다(git clone과 Docker Desktop 설치 뒤, 저장소에서 실행).
# 순서: .env → PostgreSQL 3개 DB → HDFS → MLflow 모델 → 기사 검색 색인 → Claude 메모리. 끝나면 scripts\resume.ps1로 전체를 띄운다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\migrate_restore.ps1 -From E:\PJT_transfer
#
# DB는 덤프 내용으로 통째로 바꾼다(pg_restore --clean). 그래서 시작할 때 postgres 말고는 모두 멈춘다
# (Django·Airflow가 복원 중에 쓰지 않게). 이미 쓰던 PC에서 실행하면 그 PC의 DB가 덤프 시점으로 돌아간다.
param([Parameter(Mandatory = $true)][string]$From)
$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function EnvValue($name) { ((Get-Content .env | Where-Object { $_ -match "^$name=" } | Select-Object -First 1) -replace "^$name=", "").Trim() }
function WaitFor($what, [scriptblock]$ok, $tries = 60) {
  for ($i = 0; $i -lt $tries; $i++) { if (& $ok) { return }; Start-Sleep -Seconds 5 }
  throw "$what did not become ready"
}
if (-not (Test-Path "$From\02_database")) { throw "Not an export folder: $From" }
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running" }
$profiles = "--profile", "app", "--profile", "batch", "--profile", "stream", "--profile", "search"

Step "1. .env"
if (Test-Path .env) { Write-Host ".env already exists: kept as is" } else { Copy-Item "$From\01_secrets\.env" .env }
$user = EnvValue "POSTGRES_USER"

Step "2. PostgreSQL"
docker compose @profiles stop
docker compose up -d postgres; Check "start postgres"
WaitFor "postgres" { docker compose exec -T postgres pg_isready -U $user *> $null; $LASTEXITCODE -eq 0 }
foreach ($db in (EnvValue "SERVICE_DB"), (EnvValue "MLFLOW_DB"), (EnvValue "AIRFLOW_DB")) {
  docker compose cp "$From\02_database\$db.dump" "postgres:/tmp/$db.dump"; Check "copy $db.dump"
  docker compose exec -T postgres pg_restore -U $user --clean --if-exists --no-owner -d $db "/tmp/$db.dump"
  if ($LASTEXITCODE -ne 0) { Write-Host "pg_restore $db reported warnings (exit $LASTEXITCODE); check the lines above" -ForegroundColor Yellow }
  docker compose exec -T postgres rm -f "/tmp/$db.dump"
}

Step "3. HDFS"
if (Test-Path "$From\04_hdfs") {
  docker compose --profile stream up -d namenode datanode; Check "start hdfs"
  WaitFor "hdfs" { docker compose exec -T namenode /opt/hadoop/bin/hdfs dfsadmin -safemode get 2>$null | Select-String "OFF" -Quiet }
  docker compose exec -T namenode rm -rf /tmp/hdfs_import
  docker compose cp "$From\04_hdfs" "namenode:/tmp/hdfs_import"; Check "copy hdfs"
  docker compose exec -T namenode bash -c 'cd /tmp/hdfs_import && for d in *; do /opt/hadoop/bin/hdfs dfs -put -f $d / || exit 1; done'; Check "hdfs put"
  docker compose exec -T namenode rm -rf /tmp/hdfs_import
}

Step "4. MLflow models"
docker compose --profile batch up -d mlflow; Check "start mlflow"
docker compose cp "$From\03_mlflow_models\artifacts\." "mlflow:/mlflow/artifacts/"; Check "copy mlflow artifacts"

Step "5. Article search index"
if (Test-Path "$From\05_search\articles.json") {
  docker compose --profile search up -d elasticsearch; Check "start elasticsearch"
  WaitFor "elasticsearch" { try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s").StatusCode -eq 200 } catch { $false } }
  # Flink article-enricher와 같은 이름·설정으로 만든다(articles-v2 + 별칭 articles)
  $exists = try { (Invoke-WebRequest -UseBasicParsing -Method Head "http://127.0.0.1:9200/articles-v2").StatusCode -eq 200 } catch { $false }
  if (-not $exists) {
    $mapping = [IO.File]::ReadAllBytes("$repo\flink_jobs\es\articles.json")
    Invoke-RestMethod -Method Put "http://127.0.0.1:9200/articles-v2" -ContentType "application/json; charset=utf-8" -Body $mapping | Out-Null
    Invoke-RestMethod -Method Put "http://127.0.0.1:9200/articles-v2/_alias/articles" | Out-Null
  }
  Add-Type -AssemblyName System.Web.Extensions
  $json = New-Object System.Web.Script.Serialization.JavaScriptSerializer
  $json.MaxJsonLength = [int]::MaxValue
  $hits = $json.DeserializeObject([IO.File]::ReadAllText("$From\05_search\articles.json", [Text.Encoding]::UTF8))["hits"]["hits"]
  $lines = foreach ($h in $hits) { $json.Serialize(@{ index = @{ _index = "articles-v2"; _id = $h["_id"] } }); $json.Serialize($h["_source"]) }
  if ($lines) {
    $bulk = "$env:TEMP\articles-bulk.ndjson"
    [IO.File]::WriteAllText($bulk, (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
    $r = $json.DeserializeObject((curl.exe -s -H "Content-Type: application/x-ndjson" -X POST "http://127.0.0.1:9200/_bulk?refresh=true" --data-binary "@$bulk") -join "")
    Remove-Item $bulk
    Write-Host ("{0} articles indexed, errors: {1}" -f $hits.Count, $r["errors"])
  }
}

Step "6. Claude memory"
$slug = $repo.Substring(0, 1).ToLower() + ($repo.Substring(1) -replace '[:\\/]', '-')   # D:\PJT -> d--PJT
$memory = "$env:USERPROFILE\.claude\projects\$slug\memory"
if (-not (Test-Path "$From\06_claude\memory")) { Write-Host "no memory in the export" }
elseif ((Test-Path $memory) -and (Get-ChildItem $memory).Count) { Write-Host "memory already exists at ${memory}: kept as is" }
else { New-Item -ItemType Directory -Force (Split-Path $memory) | Out-Null; Copy-Item "$From\06_claude\memory" $memory -Recurse; Write-Host "memory -> $memory" }

Write-Host "`nDone. Next: powershell -ExecutionPolicy Bypass -File scripts\resume.ps1" -ForegroundColor Green
Write-Host "Then stop the services on the old PC (both PCs collecting would exceed the KOVO 1 request/s limit)." -ForegroundColor Yellow
