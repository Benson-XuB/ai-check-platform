# AI PR Review — 构建镜像即完成依赖安装，无需在容器内手动 pip
# Trivy：直链 tar.gz（勿用 install.sh；见 ARG TRIVY_VERSION 与 GitHub releases）
FROM python:3.11-slim

WORKDIR /app

# 证书、git、curl、Node/npm（npm audit）、WeasyPrint 运行时库（PDF）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# 先复制依赖清单，利用层缓存（镜像构建阶段以 root 执行 pip 属正常，忽略 root 警告）
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# Prelaunch：gitleaks + trivy 二进制（linux/amd64；Railway 默认 amd64）
# Trivy 不用官方 install.sh，避免 raw.githubusercontent.com / GitHub API 在 CI 里超时或限流
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.69.3
RUN curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks \
    && curl -sSfL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    | tar -xz -C /usr/local/bin trivy \
    && chmod +x /usr/local/bin/trivy

COPY app/ app/
COPY static/ static/
COPY templates/ templates/

# 非 root 运行
RUN useradd --create-home --uid 1000 --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# 可选：运行时通过 -e / compose env_file 注入（见 .env.example）
# GITEE_TOKEN, DASHSCOPE_API_KEY, KIMI_API_KEY, GITEE_WEBHOOK_SECRET, DATABASE_URL 等

# Railway 等会注入 PORT；未设置时默认 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,urllib.request;p=os.environ.get('PORT','8000');urllib.request.urlopen(f'http://127.0.0.1:{p}/',timeout=4).read(16)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
