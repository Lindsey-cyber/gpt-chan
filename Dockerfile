FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 bot && mkdir -p /app/data && chown bot:bot /app/data
COPY app ./app
COPY personas ./personas
USER bot
CMD ["python", "-m", "app.main"]
