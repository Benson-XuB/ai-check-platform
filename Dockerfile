# AI PR Review — 构建镜像即完成依赖安装，无需在容器内手动 pip
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
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 先复制依赖清单，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Prelaunch：gitleaks + trivy 二进制（linux/amd64；其他架构请自建镜像）
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.58.2
RUN curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks \
    && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin v${TRIVY_VERSION}

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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).read(16)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
