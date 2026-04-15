# syntax=docker/dockerfile:1.4
# AI PR Review — 构建镜像即完成依赖安装，无需在容器内手动 pip
# Trivy：直链 tar.gz（勿用 install.sh；见 ARG TRIVY_VERSION 与 GitHub releases）
FROM python:3.11-slim

WORKDIR /app

ARG USE_CN_APT_MIRROR=
RUN set -eux; \
    if [ "${USE_CN_APT_MIRROR}" = "1" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' "$f"; \
        sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' "$f"; \
      done; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
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
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DEFAULT_TIMEOUT=300

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

# 先复制依赖清单，利用层缓存；pip 用 BuildKit 缓存加速重复构建
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    if [ -n "${PIP_INDEX_URL}" ] && [ -n "${PIP_TRUSTED_HOST}" ]; then \
      pip install --root-user-action=ignore --default-timeout=300 --retries 10 \
        -i "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" -r requirements.txt; \
    else \
      pip install --root-user-action=ignore --default-timeout=300 --retries 10 -r requirements.txt; \
    fi

# Prelaunch：gitleaks + trivy 二进制（linux/amd64；Railway 默认 amd64）
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

RUN useradd --create-home --uid 1000 --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,urllib.request;p=os.environ.get('PORT','8000');urllib.request.urlopen(f'http://127.0.0.1:{p}/',timeout=4).read(16)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
