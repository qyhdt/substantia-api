# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# docker CLI（静态二进制，仅客户端不跑 daemon）：admin 网页登录订阅账号要
# `docker run -it ... claude auth login`，经挂载的 /var/run/docker.sock 在 host 上起
# 兄弟容器（services/claude/login.py 用 subprocess/execvp 调 docker CLI；缺它则报
# [Errno 2] No such file or directory: 'docker'）。docker SDK 走 socket，另装 CLI。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz \
    && rm -rf /var/lib/apt/lists/*

# 单独装依赖，利用缓存
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

ENV PORT=9999
EXPOSE 9999

# 单进程 + uvicorn；如需多 worker：
#   gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:9999 main:app
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
