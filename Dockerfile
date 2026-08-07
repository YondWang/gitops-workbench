ARG PYTHON_BASE_IMAGE=docker.m.daocloud.io/python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG DEBIAN_APT_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_APT_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GITOPS_HOST=0.0.0.0 \
    GITOPS_PORT=8765

WORKDIR /app

RUN sed -i \
        -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_APT_SECURITY_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get \
        -o Acquire::http::Timeout=30 \
        -o Acquire::https::Timeout=30 \
        -o Acquire::Retries=3 \
        update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/ /app/

EXPOSE 9910

CMD ["python", "server.py"]
