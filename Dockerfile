# ExactRoom — どの無料 PaaS でも動く最小構成のイメージ
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# 非 root ユーザで動かす
RUN useradd --create-home --uid 10001 exactroom \
 && mkdir -p /app/data \
 && chown -R exactroom:exactroom /app
USER exactroom

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/healthz', timeout=4).status==200 else 1)"

# シェル形式にして $PORT を展開する (PaaS が動的にポートを渡すため)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
