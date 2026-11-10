# MLflow 추적 서버 이미지(학습 기록은 PostgreSQL, 모델 파일은 볼륨에 저장. 실행 명령은 compose.yaml)
FROM python:3.11-slim-bookworm
# SQLAlchemy 2.1에서 MLflow 3.1이 쓰는 클래스가 없어졌으므로 2.0.x에 머문다.
RUN pip install --no-cache-dir "mlflow==3.1.1" "psycopg2-binary==2.9.10" "sqlalchemy==2.0.41"
EXPOSE 5000
