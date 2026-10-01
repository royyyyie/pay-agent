FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/damai-agent
COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY damai_agent ./damai_agent
RUN uv sync --frozen --no-dev --no-editable \
    --extra postgres --extra redis --extra observability

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS runtime

ENV PATH=/opt/damai-agent/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN rm -rf /usr/local/lib/python3.11/site-packages/pip* \
        /usr/local/lib/python3.11/site-packages/setuptools* \
        /usr/local/lib/python3.11/site-packages/wheel* \
    && groupadd --gid 10001 damai-agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin damai-agent

WORKDIR /opt/damai-agent
COPY --from=builder --chown=10001:10001 /opt/damai-agent/.venv ./.venv

USER 10001:10001
EXPOSE 9010

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9010/livez', timeout=2).read()"]

ENTRYPOINT ["damai-agent"]
