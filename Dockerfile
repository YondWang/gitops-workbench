ARG PYTHON_BASE_IMAGE=docker.m.daocloud.io/python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GITOPS_HOST=0.0.0.0 \
    GITOPS_PORT=8765

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/ /app/

EXPOSE 9910

CMD ["python", "server.py"]
