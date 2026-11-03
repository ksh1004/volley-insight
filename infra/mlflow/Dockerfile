FROM python:3.11-slim-bookworm
# SQLAlchemy 2.1 removed classes MLflow 3.1 imports, so stay on 2.0.x.
RUN pip install --no-cache-dir "mlflow==3.1.1" "psycopg2-binary==2.9.10" "sqlalchemy==2.0.41"
EXPOSE 5000
