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
