FROM python:3.10-slim

RUN apt-get update && apt-get install -y libasound2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Используем конструкцию shell для подстановки переменных в CMD
CMD gunicorn main:app \
    -w ${WORKERS:-4} \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT:-8000} \
    --timeout ${TIMEOUT:-60}