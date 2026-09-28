FROM python:3.11.14-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/damai-agent
RUN python -m pip install uv==0.12.10

COPY pyproject.toml uv.lock README.md ./
COPY damai_agent ./damai_agent
RUN uv sync --frozen --no-dev --no-editable \
    --extra postgres --extra redis --extra observability

FROM python:3.11.14-slim-bookworm AS runtime

ENV PATH=/opt/damai-agent/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN groupadd --gid 10001 damai-agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin damai-agent

WORKDIR /opt/damai-agent
COPY --from=builder --chown=10001:10001 /opt/damai-agent/.venv ./.venv

USER 10001:10001
EXPOSE 9010

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9010/livez', timeout=2).read()"]

ENTRYPOINT ["damai-agent"]
