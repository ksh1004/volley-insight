# 배구 전력 분석 플랫폼

V-리그 데이터를 수집해 전력 분석(머신러닝)과 경기 일정·정보·기사를 제공하는 웹 서비스다.

- 요구사항: [docs/requirements.md](docs/requirements.md)
- 설계: [docs/design.md](docs/design.md)

## 폴더 구조

| 폴더 | 내용 |
|---|---|
| `backend/` | Django REST API (앱: accounts, league, matches, analytics, live, articles, ops) |
| `frontend/` | Vue 3 + Vite (Pinia, Vue Router, axios) |
| `collectors/` | KOVO·뉴스 수집기 (Kafka로 발행) |
| `airflow/dags/` | Airflow DAG |
| `spark_jobs/` | PySpark 작업 (YARN에서 실행) |
| `flink_jobs/` | PyFlink 작업 |
| `ml/` | 모델 학습·평가 코드 (MLflow 연동) |
| `infra/` | 서비스별 Dockerfile과 설정 (Hadoop, Airflow, MLflow, Flink, Elasticsearch, Filebeat, PostgreSQL, Kafka) |
| `compose.yaml` | 개발 환경 전체 구성 |

## 준비물

- Docker Desktop (WSL2 백엔드), Git
- 로컬 개발 도구: Python 3.11, Node.js 22 이상

### Docker 메모리

전체 구성은 약 26GB가 필요하다. Docker Desktop(WSL2)은 기본으로 PC 메모리의 절반만 쓰므로, 묶음(프로필)별로 나눠 띄운다. 여러 묶음을 동시에 띄우려면 `%USERPROFILE%\.wslconfig`에 아래를 넣고 `wsl --shutdown` 후 Docker Desktop을 다시 시작한다.

```ini
[wsl2]
memory=24GB
```

## 처음 설정

```bash
cp .env.example .env          # 비밀번호와 DJANGO_SECRET_KEY를 바꾼다
docker compose --profile app up -d --build
docker compose exec django-api python manage.py migrate
docker compose exec django-api python manage.py createsuperuser
```

확인: <http://localhost:5173> (Vue) 화면에 "API: ok · DB: ok"가 보이면 된다.

## 묶음(프로필)별 실행

| 명령 | 띄우는 서비스 | 메모리(상한) |
|---|---|---|
| `docker compose up -d` | PostgreSQL, Kafka(+토픽 생성) | 약 2GB |
| `docker compose --profile app up -d` | + Django API, SSE 게이트웨이(`django-sse`), Vue 개발 서버 | 약 4GB |
| `docker compose --profile batch up -d` | + Hadoop(HDFS·YARN), Airflow, MLflow | 약 12GB |
| `docker compose --profile stream up -d` | + HDFS, Flink, 실시간 수집기(`live-collector`) | 약 7GB |
| `docker compose --profile search up -d` | + Elasticsearch(nori), Kibana, Filebeat, Metricbeat, kafka-lag(+ kibana-setup 1회) | 약 4GB |

여러 묶음은 `--profile app --profile search`처럼 함께 쓴다. 끌 때는 `docker compose --profile <이름> down` (데이터 볼륨은 남는다).

## 접속 주소 (모두 이 PC에서만 접근 가능)

| 서비스 | 주소 |
|---|---|
| Vue 개발 서버 | <http://localhost:5173> |
| Django API / 관리자 | <http://localhost:8000/api/health>, <http://localhost:8000/admin/> |
| SSE 게이트웨이 (실시간 스트림) | <http://localhost:8001/api/live/023-6/stream> (사이트에서는 Vite가 이 경로만 넘긴다) |
| PostgreSQL | `localhost:5432` (DB: service, airflow, mlflow) |
| Kafka (호스트에서 접속) | `localhost:29092` (컨테이너끼리는 `kafka:9092`) |
| HDFS NameNode | <http://localhost:9870> |
| YARN ResourceManager | <http://localhost:8088> |
| Airflow | <http://localhost:8080> (개발용: 로그인 없이 관리자) |
| MLflow | <http://localhost:5000> |
| Flink | <http://localhost:8081> |
| Elasticsearch | <http://localhost:9200> |
| Kibana | <http://localhost:5601> · 운영 대시보드 <http://localhost:5601/app/dashboards#/view/volley-ops> |

## 환경 확인 작업

| 확인 대상 | 방법 |
|---|---|
| 웹 → API → DB | <http://localhost:5173> 또는 `curl localhost:8000/api/health` |
| Kafka 토픽 | `docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list` |
| Spark on YARN → HDFS | Airflow에서 `dev_smoke_spark_yarn` DAG 실행 후 HDFS `/tmp/dev-smoke/pi` 확인 |
| Flink + Kafka | `docker compose exec flink-jobmanager flink run -d -py /opt/flink/usrlib/jobs/smoke_job.py` |
| Elasticsearch nori | `curl localhost:9200/_cat/plugins` 에 `analysis-nori` 표시 |

## 데이터 수집 실행

1. 수집: `collectors/README.md` 참고 (예: `python -m collectors games --season 022`). Kafka에 원본이 쌓인다(보관 7일).
2. 영구 보관: `stream` 프로필을 띄우고 Flink `raw-archiver`를 한 번 제출한다. 이후 Kafka에 들어오는 모든 수집 메시지가 HDFS `/raw/topic={토픽}/dt={수집일}/`에 저장된다.

```bash
docker compose --profile stream up -d
docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh raw_archiver.py raw-archiver
docker compose exec namenode hdfs dfs -ls -R /raw      # 저장된 파일 확인
```

`submit.sh`는 같은 이름의 작업이 이미 돌고 있으면 제출하지 않는다. 같은 작업이 두 개 돌면 모든 메시지가 두 번 저장된다.

3. 정제: `batch` 프로필을 띄우고 Spark 작업을 YARN에 제출한다. HDFS 원본을 정리해 `/curated/kovo/`(Parquet)에 쓰고, PostgreSQL 경기·기록 테이블(`match`, `set_score`, `player_match_stat`, `rally_event` 등)에 반영한다. 처리한 시즌만 바뀌고, 다시 실행해도 결과가 같다.

```bash
docker compose --profile batch up -d
docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh curate_kovo.py --seasons 022,023
```

실행 끝에 품질 검사 결과(문자중계 누락, 랠리 점수 불일치 등)가 출력된다. 문제가 있는 경기는 `FINISHED`로 남고, 없으면 `CONFIRMED`가 된다.

4. 분석 마트: 정제된 전체 시즌으로 팀 전력 지수(Elo), 팀·선수 시즌 요약(사이드아웃·브레이크율, 포지션 내 백분위)을 계산한다. Airflow DAG가 정제 다음에 자동으로 실행한다.

```bash
docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh build_marts.py
```

## 자동 실행 (Airflow, <http://localhost:8080>)

위 1~3단계를 Airflow DAG가 이어서 실행한다. `batch`·`stream` 프로필이 떠 있어야 한다(Kafka → HDFS 보관에 Flink `raw-archiver` 필요).

| DAG | 언제 | 하는 일 |
|---|---|---|
| `kovo_daily` | 매일 06:00 | 현재 시즌 일정·결과 갱신, 최근 7일 중 빠진 경기 보충, 정제 |
| `kovo_postgame` | 14:00~23:40, 20분마다 | 끝난 경기의 상세·문자중계 수집과 정제. 시작한 경기가 없으면 KOVO에 요청하지 않고 끝난다 |
| `kovo_backfill` | 수동 | 과거 시즌. 실행 시 `seasons`(예: `001-021`), `limit`(시험용 경기 수)를 입력 |
| `ml_daily_predict` | 매일 07:00 | 피처 갱신 → `champion` 모델로 예정 경기 승리 확률·세트 스코어 예측 → `match_prediction`, 끝난 경기 적중 여부 기록 |
| `ml_weekly_retrain` | 월요일 04:00 | 피처 갱신 → Hyperopt + Spark MLlib 학습 → MLflow `match-winner` 등록(<http://localhost:5000>) → 최근 4시즌 walk-forward 백테스트(`prediction_backtest`, 예측 검증 화면) |
| `news_collect` | 매시 5분 | 스포츠 RSS(+네이버 뉴스 API) 배구 기사 → Kafka `news.article` → HDFS 보관 확인 → Flink `article-enricher` 저장 확인 |
| `live_replay` | 수동 | 끝난 경기를 실시간처럼 재생(`match` 예: `023-6`, `speed` 1=실제 속도) → Flink `live-metrics`가 끝까지 따라왔는지 확인 |

`kovo_daily`는 마트 다음에 Elasticsearch `entities`(팀·선수 검색)와 선수 유형·유사 선수(`spark_jobs/player_types.py`, K-means, MLflow `player-types`)도 다시 만든다.

```bash
docker compose exec airflow airflow dags trigger kovo_backfill --conf '{"seasons": "019-021"}'
docker compose exec airflow airflow dags pause kovo_postgame     # 자동 실행 멈추기
```

DAG 보조 코드 테스트: `.venv/Scripts/python -m pytest airflow/tests`

## 사이트 (<http://localhost:5173>)

| 화면 | 주소 | API |
|---|---|---|
| 홈: 진행 중 경기(20초마다 갱신), 내 관심 팀·선수(로그인 시, 관심 팀 경기는 목록 맨 앞에 별 표시), 오늘·다음 경기, 최근 결과, 남녀부 순위 | `/` | `GET /api/live`, `GET /api/matches?date=` · `?status=finished&order=desc`, `GET /api/standings`, `GET /api/me/feed` |
| 실시간 경기: 점수·서브권·연속 득점, 실시간 승리 확률, 세트별 점수 흐름·지표, 최근 득점 (재생이면 "재생" 표시) | `/matches/023-6/live` | `GET /api/live/{키}/stream` (SSE), `GET /api/live/{키}` |
| 일정·결과 (시즌·남녀부·팀·월) | `/schedule` | `GET /api/matches?season=&gender=&team=&month=YYYY-MM` |
| 경기 상세: 세트 점수, 팀 비교, 선수 기록, 세트별 점수 흐름, 문자중계 | `/matches/022-3` | `GET /api/matches/{시즌-경기번호}`, `GET /api/matches/{키}/rallies?set=` |
| 순위: 팀 순위, 부문별 선수 순위 | `/standings` | `GET /api/standings?season=&gender=`, `GET /api/standings/players?category=` |
| 팀: 시즌 성적, 전력 지수 추이, 강약점 레이더(리그 내 순위), 리그 평균 대비 지표(리시브 후 공격·접전 랠리 포함), 선수 폼 변화, 선수단, 최근·다음 경기 | `/teams/1001` | `GET /api/teams/{팀코드}?season=` |
| 선수: 포지션 내 백분위, 최근 폼, 선수 유형·비슷한 선수(같은 시즌·역대), 시즌별·경기별 기록, 관련 기사 | `/players/1000525` | `GET /api/players/{선수코드}?season=` |
| 예측 검증: 시즌별 적중률(백테스트, 모델 vs Elo), 확신도별 실제 적중률, 이번 시즌 예측 결과 | `/predictions` | `GET /api/predictions?gender=` |
| 기사: 최신순, 팀별 보기 (제목을 누르면 언론사 원문) | `/news?team=1001` | `GET /api/articles?team=&player=&page=` |
| 검색: 팀·선수·기사, 머리글 검색창 자동완성 | `/search?q=대한항공` | `GET /api/search?q=&page=`, `GET /api/search/suggest?q=` |
| 회원가입·로그인 (이메일·닉네임·비밀번호, 1분에 10번까지) | `/signup`, `/login` | `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout` |
| 내 정보: 관심 팀 고르기(각 10개까지), 관심 선수 빼기, 로그아웃, 탈퇴(비밀번호 확인 후 바로 삭제). 팀·선수 화면의 "관심 팀/관심 선수" 버튼으로도 추가 | `/me` | `GET·DELETE /api/me`, `GET·POST /api/me/favorites`, `DELETE /api/me/favorites/{team\|player}/{코드}` |

경기 주소는 내부 id 대신 `시즌-경기번호`라서 DB를 다시 만들어도 바뀌지 않는다. 순위는 V-리그 규칙(승점 → 승수 → 세트 득실률 → 점수 득실률, 승자승 제외)으로 API가 계산한다. 선수 순위 자격(팀 세트의 1/3 이상 출전 등)은 이 사이트의 기준이다.

테스트: `docker compose exec django-api python manage.py test`

### 기사·검색 흐름

```text
news_collect(매시) → Kafka news.article → Flink article-enricher: URL·제목 중복 제거(7일), 팀·선수 태그
                                          → PostgreSQL article(+_team, _player) · Elasticsearch articles
kovo_daily → Spark index_entities.py → Elasticsearch entities (팀·선수 자동완성)
```

- 기사는 제목·요약(160자)·언론사·링크만 저장한다. 본문은 수집 단계에서 잘라 Kafka·HDFS에도 남지 않는다.
- RSS: 연합뉴스·뉴시스·동아일보·한국경제·한겨레 스포츠 면(각 사이트 robots.txt 허용 경로, 2026-11-04 확인). 배구 기사만 남긴다.
- 네이버 뉴스 검색 API는 `.env`에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 넣으면 켜진다(<https://developers.naver.com> → 애플리케이션 등록 → "검색" API). 넣은 뒤 `docker compose --profile batch up -d airflow`.
- `article-enricher`도 `flink-job-keeper`가 지켜서, 멈추면 마지막 체크포인트에서 다시 제출된다.

### 실시간 경기 흐름

```text
live-collector(15초마다, 경기 10분 전~끝) → KOVO 문자중계에서 새 이벤트만 → Kafka kovo.live
경기 재생기(live_replay DAG 또는 명령) ─────────────────────────────────┘
  → Flink live-metrics: 점수·세트·서브권·연속 득점, 세트별 지표, 실시간 승리 확률
  → PostgreSQL live_match_state · live_set_metric · live_rally  +  Kafka live.metrics
  → SSE 게이트웨이(django-sse) → 브라우저 /matches/{키}/live
```

- 오늘 경기는 `live-collector`가 알아서 따라간다. 상태는 `docker compose logs -f live-collector`. KOVO 요청은 Airflow와 같은 잠금 파일(`kovo-rate` 볼륨)로 함께 초당 1회 이하를 지킨다.
- 실제 경기는 경기 상태를 `SCHEDULED → LIVE → FINISHED`로 바꾸고, 공식 기록은 경기 후 `kovo_postgame`이 수집해 `CONFIRMED`로 만든다. 재생은 경기 상태와 기록을 바꾸지 않는다.
- 경기가 없을 때 시연: Airflow에서 `live_replay`를 설정과 함께 실행하거나

```bash
docker compose exec airflow sh -c "cd /opt/airflow/collectors && python -m collectors replay 023-6 --speed 10"
```

  그다음 <http://localhost:5173/matches/023-6/live>를 연다(10배속이면 3세트 경기가 약 9분).
- 승리 확률은 현재 점수·서브권과 두 팀의 이번 시즌 사이드아웃·브레이크 비율로 계산한다(시즌 초에는 지난 시즌 값과 섞는다). 세트별 지표는 문자중계로 바로 센 값이라 경기 후 공식 기록과 다를 수 있다(지난 경기 재생으로 확인한 결과는 일치).
- `live-metrics`도 `flink-job-keeper`가 지킨다. 처음 제출하면 `kovo.live`의 새 메시지부터 읽는다.

### 운영 대시보드 (Kibana)

`search` 프로필을 띄우면 `kibana-setup`이 데이터 뷰와 대시보드 "배구 파이프라인 운영"을 넣는다. 수정은 `infra/kibana/build_dashboard.py` → `python infra/kibana/build_dashboard.py` → `docker compose --profile search up kibana-setup`.

| 패널 | 출처 |
|---|---|
| 수집 성공률·실패 수·수집 건수 | Kafka `ops.collection-log` → Filebeat → `ops-collection-*` |
| Kafka 컨슈머 랙 (raw-archiver, article-enricher, live-metrics, filebeat-ops) | `kafka-lag` 서비스(`collectors/kafka_lag.py`) → `metrics-volley` |
| Airflow DAG 소요·시작 지연, 태스크 대기 | Metricbeat sql 모듈(Airflow DB) → `metrics-volley` |
| 오류 로그(서비스별), 최근 오류 줄 | 컨테이너 로그 → Filebeat → `logs-volley-*` |
| 기사 저장(언론사별) | Elasticsearch `articles` |
| 데이터 품질: 검사별 미해결 건수, 미해결 오류 수 | Metricbeat sql 모듈(서비스 DB `quality_issue`) → `metrics-volley` |

### 운영: 수집 현황, 수동 실행, 데이터 품질과 보정 (Django 관리 화면)

<http://localhost:8000/admin/> → "데이터 운영"

- **품질 검사 결과**: Spark 정제가 매번 검사한 결과(세트 점수 규칙, 문자중계 누락, 기록 범위, 팀·선수 합계 등). KOVO 원천에도 없는 문제는 골라서 "확인함으로 표시"하면 다음 실행에도 유지되고 `kovo_daily`의 `quality_gate`를 실패시키지 않는다.
- **수집 현황**: Airflow 작업별 최근 실행(성공·실패 점), 일정, 7일 실패 수와 수집기 실행 결과(성공·실패·중간에 멈춤, 보낸 메시지 수), 그 아래 작업 실패 내역. 실패는 재시도를 모두 쓴 뒤에만 올라오고, 관리 첫 화면에도 "확인하지 않은 작업 실패 N건"이 뜬다. 원인을 보고 "확인함으로 표시"한다.
- **수동 실행**: 예약 작업을 지금 한 번 더 실행(예: 경기 후 기록), 경기·날짜·시즌 재처리(`kovo_reprocess`: KOVO에서 다시 받거나 저장된 원천으로 다시 정제만), 과거 시즌 수집, 경기 재생. 실행한 사람·설정·메모가 기록에 남는다.
- **실패 알림을 메신저로 받기**: `.env`에 `ALERT_WEBHOOK_URL`(Mattermost·Slack·Discord의 incoming webhook 주소)을 넣고 `docker compose up -d airflow`. 비워 두면 관리 화면에만 쌓인다.
- **데이터 보정**: 잘못된 값 한 항목을 근거와 함께 고친다(예: 대상 "선수", 키 `0001328`, 항목 `name`). 저장하면 사이트에 바로 반영되고, 이후 정제에도 계속 적용된다. 되돌리려면 지우지 말고 끈다.
- **모델 관리** ("Analytics" 아래): MLflow `match-winner` 버전별 시험 성적(Elo 기준선 포함)과 실전 적중률을 비교하고, 사유를 적어 서비스 모델(`champion`)을 바꾼다. 바꾸면 다가오는 경기 예측을 바로 다시 만든다(Airflow `ml_daily_predict`). `batch` 프로필이 떠 있어야 한다.

관리자 계정은 `docker compose exec django-api python manage.py createsuperuser`로 만든다(관리 화면은 이 PC에서만 열린다).

Windows의 Docker Desktop은 연결 폴더의 파일 변경을 컨테이너에 알리지 않아서, 프론트엔드 컨테이너는 `VITE_USE_POLLING=true`로 변경을 확인한다.

## PC를 껐다 켠 뒤 이어서 하기

> Docker 데이터 위치: `D:\Docker\wsl\disk\docker_data.vhdx`. C드라이브 공간이 부족해서 `C:\Users\SSAFY\AppData\Local\Docker\wsl\disk` 폴더를 D드라이브로 연결(junction)해 두었다. Docker Desktop 4.88.1의 "Disk image location" 이동 기능은 복사 후 실패로 처리되므로 쓰지 않는다. 연결 확인: `Get-Item C:\Users\SSAFY\AppData\Local\Docker\wsl\disk | Select LinkType, Target`

데이터(Kafka, HDFS, PostgreSQL, MLflow, Elasticsearch)는 Docker 볼륨에 남고, 컨테이너는 Docker Desktop이 켜지면 다시 뜬다. Flink `raw-archiver`·`article-enricher`·`live-metrics`는 `flink-job-keeper` 서비스가 1분마다 확인해 없으면 마지막 체크포인트에서 다시 제출한다.

1. Docker Desktop을 실행한다. (설정 → General → "Start Docker Desktop when you sign in"을 켜 두면 생략 가능)
2. 상태 확인과 재시작:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\resume.ps1            # 전체 서비스 기동 + 보관 작업·DAG·DB 상태
powershell -ExecutionPolicy Bypass -File scripts\resume.ps1 -Backfill  # 끝나지 않은 백필이 있으면 다시 실행
```

백필을 다시 실행하면 이미 `CONFIRMED`인 경기는 건너뛰고 남은 경기만 수집한다. 중단 시점에 돌던 Airflow 태스크는 재시작 후 자동으로 재시도(최대 3회)되므로, 실행이 아직 `running`이면 그대로 둔다.

## 로컬 개발 (컨테이너 밖)

```bash
py -3.11 -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt
cd frontend && npm install && npm run dev   # /api 요청은 localhost:8000으로 전달된다
```

로컬에서 Django를 실행할 때는 `.env` 값을 환경 변수로 넣고 `POSTGRES_HOST=localhost`로 둔다.

## 버전

| 구성 요소 | 버전 |
|---|---|
| Python (Django, Airflow, Spark 드라이버·실행기, MLflow) | 3.11 |
| Django / DRF | 5.2.4 / 3.16.0 |
| Vue / Vite / Pinia | 3.5 / 6 / 3 |
| PostgreSQL | 16 |
| Kafka (KRaft) | 3.9.0 |
| Hadoop (HDFS, YARN) | 3.4.1, Java 11 |
| Spark (PySpark) | 3.5.5 |
| Airflow | 3.0.6 (LocalExecutor, standalone) |
| MLflow | 3.1.1 |
| Flink (PyFlink) | 1.20.1, Java 17, Python 3.10 |
| Elasticsearch / Kibana / Filebeat | 8.17.4 |
