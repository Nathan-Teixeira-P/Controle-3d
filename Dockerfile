FROM python:3.12-slim
ENV TZ=America/Sao_Paulo PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
CMD ["sh", "-c", "alembic -c app/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
