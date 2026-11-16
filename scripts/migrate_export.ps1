# 다른 PC로 옮길 데이터를 종류별 폴더에 모은다. 새 PC에서는 scripts\migrate_restore.ps1이 이 폴더를 되살린다.
# 데이터는 매일 바뀌므로(경기 전 예측, 기사) 옮기기 직전에 다시 실행한다. 서비스가 켜진 상태에서 실행한다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\migrate_export.ps1 [-Out D:\PJT_transfer]
#
# 폴더에는 비밀값(.env)과 회원 이메일(service DB)이 들어 있다. 저장소 안에 두거나 공개된 곳에 올리지 않는다.
# 무엇을 왜 옮기는지(그리고 옮기지 않는지)는 만들어진 폴더의 README.md에 적는다.
param([string]$Out = "D:\PJT_transfer")
$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function EnvValue($name) { ((Get-Content .env | Where-Object { $_ -match "^$name=" } | Select-Object -First 1) -replace "^$name=", "").Trim() }
function Fresh($dir) { if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }; New-Item -ItemType Directory -Force $dir | Out-Null }
$Out = [IO.Path]::GetFullPath($Out)
if (($Out.TrimEnd('\') + '\').StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Use a folder outside the repository so secrets are never committed: $Out"
}
New-Item -ItemType Directory -Force $Out | Out-Null

Step "1. 비밀값 (.env)"
Fresh "$Out\01_secrets"
Copy-Item .env "$Out\01_secrets\.env"

Step "2. PostgreSQL (service, mlflow, airflow)"
Fresh "$Out\02_database"
$user = EnvValue "POSTGRES_USER"
foreach ($db in (EnvValue "SERVICE_DB"), (EnvValue "MLFLOW_DB"), (EnvValue "AIRFLOW_DB")) {
  docker compose exec -T postgres pg_dump -U $user -Fc -d $db -f "/tmp/$db.dump"; Check "pg_dump $db"
  docker compose cp "postgres:/tmp/$db.dump" "$Out\02_database\$db.dump"; Check "copy $db.dump"
  docker compose exec -T postgres rm -f "/tmp/$db.dump"
}

Step "3. MLflow 모델 파일"
Fresh "$Out\03_mlflow_models"
docker compose cp "mlflow:/mlflow/artifacts" "$Out\03_mlflow_models\artifacts"; Check "copy mlflow artifacts"

Step "4. HDFS (/raw 원천 + /curated /features /marts)"
if (Test-Path "$Out\04_hdfs") { Remove-Item "$Out\04_hdfs" -Recurse -Force }
docker compose exec -T namenode bash -c "rm -rf /tmp/hdfs_export && mkdir -p /tmp/hdfs_export && /opt/hadoop/bin/hdfs dfs -get /raw /curated /features /marts /tmp/hdfs_export/"; Check "hdfs get"
docker compose cp "namenode:/tmp/hdfs_export" "$Out\04_hdfs"; Check "copy hdfs export"
docker compose exec -T namenode rm -rf /tmp/hdfs_export

Step "5. 기사 검색 색인 (Elasticsearch articles)"
Fresh "$Out\05_search"
# shortcut: 한 번에 최대 10,000건(ES 기본 한도), 기사가 그보다 많아지면 scroll로 바꾼다
Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:9200/articles/_search?size=10000" -ContentType "application/json" `
  -Body '{"query": {"match_all": {}}, "sort": ["_doc"]}' -OutFile "$Out\05_search\articles.json"

Step "6. Claude 작업 기록 (메모리)과 플러그인 목록"
Fresh "$Out\06_claude"
$slug = $repo.Substring(0, 1).ToLower() + ($repo.Substring(1) -replace '[:\\/]', '-')   # D:\PJT -> d--PJT
$memory = "$env:USERPROFILE\.claude\projects\$slug\memory"
if (Test-Path $memory) { Copy-Item $memory "$Out\06_claude\memory" -Recurse } else { Write-Host "no Claude memory at $memory" }
$plugins = "$env:USERPROFILE\.claude\plugins\installed_plugins.json"
if (Test-Path $plugins) { ((Get-Content $plugins -Raw | ConvertFrom-Json).plugins.PSObject.Properties.Name) | Set-Content "$Out\06_claude\plugins.txt" -Encoding utf8 }

Step "7. README.md (분류표, 크기, 커밋 안 된 변경, 새 PC 복원 순서)"
$size = { param($d) $b = (Get-ChildItem $d -Recurse -File -Force | Measure-Object Length -Sum).Sum; if ($b -ge 1MB) { "{0:N0} MB" -f ($b / 1MB) } else { "{0:N0} KB" -f ($b / 1KB) } }
$enc = [Console]::OutputEncoding; [Console]::OutputEncoding = [Text.Encoding]::UTF8   # git이 내보내는 한글 파일 이름을 그대로 읽기
$dirty = (git -c core.quotepath=false status --short) -join "`n"
[Console]::OutputEncoding = $enc
if (-not $dirty) { $dirty = "(없음)" }
$readme = @"
# 다른 PC로 옮길 데이터

- 만든 시각: $(Get-Date -Format "yyyy-MM-dd HH:mm") / 원본 PC: $env:COMPUTERNAME / 저장소: $repo
- 다시 만들기: 저장소에서 ``powershell -ExecutionPolicy Bypass -File scripts\migrate_export.ps1 -Out $Out``
- **주의:** 비밀값(.env)과 회원 이메일(service DB)이 들어 있다. USB나 개인 드라이브로 옮기고, 공개된 곳(GitHub 등)에 올리지 않는다.

## 분류

| 구분 | 폴더 | 내용 | 크기 | 없으면 |
|---|---|---|---|---|
| 필수 | 01_secrets | ``.env`` (DB 비밀번호, Django 키, API 키) | $(& $size "$Out\01_secrets") | 서비스가 뜨지 않음. 키를 새로 만들면 기존 로그인 토큰이 무효 |
| 필수 | 02_database | PostgreSQL 덤프: ``service``(경기·기록·분석, **경기 전 예측**, 기사, 회원, 품질·보정·알림 기록), ``mlflow``(모델 버전 목록), ``airflow``(DAG 켜짐 상태·실행 이력) | $(& $size "$Out\02_database") | 경기 전 예측과 기사는 되살릴 수 없음(경기가 끝난 뒤 다시 예측하면 사후 예측이 됨). 나머지는 백필로 하루 이상 |
| 필수 | 03_mlflow_models | MLflow 모델 파일(``match-winner`` 각 버전, 선수 유형) | $(& $size "$Out\03_mlflow_models") | 다시 학습할 수 있지만 버전 이력(v1~)이 사라짐 |
| 권장 | 04_hdfs | HDFS ``/raw``(Kafka 원천 그대로), ``/curated``·``/features``·``/marts``(Spark 결과) | $(& $size "$Out\04_hdfs") | KOVO 원천은 다시 받을 수 있음(초당 1회라 하루 정도). 기사·실시간·수집 로그 원천은 되살릴 수 없음 |
| 선택 | 05_search | 기사 검색 색인(Elasticsearch ``articles``) | $(& $size "$Out\05_search") | 옮긴 뒤 새로 들어온 기사만 검색됨 |
| 선택 | 06_claude | Claude Code 작업 기록(메모리)과 설치한 플러그인 목록 | $(& $size "$Out\06_claude") | Claude가 지난 작업 맥락을 모름 |

## 옮기지 않는 것

| 대상 | 이유 |
|---|---|
| 코드 | GitHub(``origin``)로 옮긴다. 아래 "커밋 안 된 변경"을 먼저 커밋·푸시할 것 |
| Kafka 데이터 | 7일만 보관하는 통로이고, 내용은 HDFS ``/raw``에 모두 있음 |
| Elasticsearch 로그·지표·``entities`` | 로그는 다시 쌓이고, 팀·선수 검색 색인은 ``kovo_daily``가 매일 다시 만든다 |
| HDFS ``/tmp``, ``/user``(Spark 임시), ``/flink``(체크포인트) | 실행 중 임시 파일 |
| Airflow 로그, ``node_modules``, KOVO 요청 간격 기록 | 새 PC에서 다시 생김 |

## 커밋 안 된 변경 (만든 시각 기준)

``````
$dirty
``````

## 새 PC에서 되살리는 순서

1. Docker Desktop 설치(메모리 32GB 이상 권장), Git 설치
2. ``git clone https://github.com/ksh1004/volley-insight.git`` (원래처럼 ``D:\PJT``에 두면 Claude 메모리 경로도 그대로 맞음)
3. 이 폴더를 새 PC로 복사한 뒤 저장소에서:
   ``powershell -ExecutionPolicy Bypass -File scripts\migrate_restore.ps1 -From <이 폴더>``
   (.env 복사 → DB 3개 → HDFS → MLflow 모델 → 기사 검색 색인 → Claude 메모리 순서로 되살림)
4. ``powershell -ExecutionPolicy Bypass -File scripts\resume.ps1`` 로 전체 기동
5. Claude Code 플러그인 다시 설치: 06_claude\plugins.txt 목록
6. **옛 PC의 서비스는 끈다**(``docker compose stop``). 두 PC가 함께 수집하면 KOVO 요청 제한(초당 1회)을 넘는다
"@
[IO.File]::WriteAllText("$Out\README.md", $readme, (New-Object Text.UTF8Encoding $false))

Write-Host "`nDone: $Out" -ForegroundColor Green
Get-ChildItem $Out -Directory | ForEach-Object { "{0,-18} {1}" -f $_.Name, (& $size $_.FullName) }
