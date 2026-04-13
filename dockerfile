FROM python:3.10-slim

# Устанавливаем системные зависимости, необходимые для Piper и ONNX
RUN apt-get update && apt-get install -y \
    libasound2 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Проверьте, что в .env WORKERS=48, а не больше, чем ядер
CMD gunicorn main:app \
    -w ${WORKERS:-4} \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT:-8000} \
    --timeout ${TIMEOUT:-120} \
    --preload
