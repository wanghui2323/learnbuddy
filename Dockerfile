ARG PYTHON_VERSION=3.11.13
FROM python:${PYTHON_VERSION}-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 learnbuddy \
    && useradd --system --uid 10001 --gid learnbuddy --home-dir /app learnbuddy \
    && mkdir -p /app/data/instances \
    && chown -R learnbuddy:learnbuddy /app/data

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=learnbuddy:learnbuddy core ./core
COPY --chown=learnbuddy:learnbuddy web ./web
COPY --chown=learnbuddy:learnbuddy scripts/init_cloud_admin.py ./scripts/init_cloud_admin.py
COPY --chown=learnbuddy:learnbuddy server.py mcp_server.py ./

USER learnbuddy

EXPOSE 8000

# /healthz 只证明 Web 进程存活；数据库与运行版本边界由 /readyz 检查。
# LLM、飞书与 OpenClaw 需要各自的真实请求/消息往返验收。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).read(1)"]

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
