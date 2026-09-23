FROM python:3.12-slim

# ffmpeg нужен, чтобы конвертировать FLAC/M4A/WAV и т.п. в MP3 перед загрузкой в Яндекс Музыку
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

ENV DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "bot"]
