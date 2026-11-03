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
| `docker compose --profile app up -d` | + Django API, Vue 개발 서버 | 약 3.5GB |
| `docker compose --profile batch up -d` | + Hadoop(HDFS·YARN), Airflow, MLflow | 약 12GB |
| `docker compose --profile stream up -d` | + HDFS, Flink | 약 7GB |
| `docker compose --profile search up -d` | + Elasticsearch(nori), Kibana, Filebeat | 약 5.5GB |

여러 묶음은 `--profile app --profile search`처럼 함께 쓴다. 끌 때는 `docker compose --profile <이름> down` (데이터 볼륨은 남는다).

## 접속 주소 (모두 이 PC에서만 접근 가능)

| 서비스 | 주소 |
|---|---|
| Vue 개발 서버 | <http://localhost:5173> |
| Django API / 관리자 | <http://localhost:8000/api/health>, <http://localhost:8000/admin/> |
| PostgreSQL | `localhost:5432` (DB: service, airflow, mlflow) |
| Kafka (호스트에서 접속) | `localhost:29092` (컨테이너끼리는 `kafka:9092`) |
| HDFS NameNode | <http://localhost:9870> |
| YARN ResourceManager | <http://localhost:8088> |
| Airflow | <http://localhost:8080> (개발용: 로그인 없이 관리자) |
| MLflow | <http://localhost:5000> |
| Flink | <http://localhost:8081> |
| Elasticsearch | <http://localhost:9200> |
| Kibana | <http://localhost:5601> |

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

```bash
docker compose exec airflow airflow dags trigger kovo_backfill --conf '{"seasons": "019-021"}'
docker compose exec airflow airflow dags pause kovo_postgame     # 자동 실행 멈추기
```

DAG 보조 코드 테스트: `.venv/Scripts/python -m pytest airflow/tests`

## 사이트 (<http://localhost:5173>)

| 화면 | 주소 | API |
|---|---|---|
| 홈: 오늘·다음 경기, 최근 결과, 남녀부 순위 | `/` | `GET /api/matches?date=` · `?status=finished&order=desc`, `GET /api/standings` |
| 일정·결과 (시즌·남녀부·팀·월) | `/schedule` | `GET /api/matches?season=&gender=&team=&month=YYYY-MM` |
| 경기 상세: 세트 점수, 팀 비교, 선수 기록, 세트별 점수 흐름, 문자중계 | `/matches/022-3` | `GET /api/matches/{시즌-경기번호}`, `GET /api/matches/{키}/rallies?set=` |
| 순위: 팀 순위, 부문별 선수 순위 | `/standings` | `GET /api/standings?season=&gender=`, `GET /api/standings/players?category=` |
| 팀: 시즌 성적, 리그 평균 대비 지표, 선수단, 최근·다음 경기 | `/teams/1001` | `GET /api/teams/{팀코드}?season=` |
| 선수: 시즌별·경기별 기록 | `/players/1000525` | `GET /api/players/{선수코드}?season=` |

경기 주소는 내부 id 대신 `시즌-경기번호`라서 DB를 다시 만들어도 바뀌지 않는다. 순위는 V-리그 규칙(승점 → 승수 → 세트 득실률 → 점수 득실률, 승자승 제외)으로 API가 계산한다. 선수 순위 자격(팀 세트의 1/3 이상 출전 등)은 이 사이트의 기준이다.

테스트: `docker compose exec django-api python manage.py test league matches`

Windows의 Docker Desktop은 연결 폴더의 파일 변경을 컨테이너에 알리지 않아서, 프론트엔드 컨테이너는 `VITE_USE_POLLING=true`로 변경을 확인한다.

## PC를 껐다 켠 뒤 이어서 하기

> Docker 데이터 위치: `D:\Docker\wsl\disk\docker_data.vhdx`. C드라이브 공간이 부족해서 `C:\Users\SSAFY\AppData\Local\Docker\wsl\disk` 폴더를 D드라이브로 연결(junction)해 두었다. Docker Desktop 4.88.1의 "Disk image location" 이동 기능은 복사 후 실패로 처리되므로 쓰지 않는다. 연결 확인: `Get-Item C:\Users\SSAFY\AppData\Local\Docker\wsl\disk | Select LinkType, Target`

데이터(Kafka, HDFS, PostgreSQL, MLflow)는 Docker 볼륨에 남고, 컨테이너는 Docker Desktop이 켜지면 다시 뜬다. Flink `raw-archiver`는 `flink-job-keeper` 서비스가 1분마다 확인해 없으면 다시 제출한다(Kafka에 커밋된 위치부터 이어서 저장).

1. Docker Desktop을 실행한다. (설정 → General → "Start Docker Desktop when you sign in"을 켜 두면 생략 가능)
2. 상태 확인과 재시작:

```powershell
powershell -ExecutionPolicy Bypass -File scripts
esume.ps1            # 전체 서비스 기동 + 보관 작업·DAG·DB 상태
powershell -ExecutionPolicy Bypass -File scripts
esume.ps1 -Backfill  # 끝나지 않은 백필이 있으면 다시 실행
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
