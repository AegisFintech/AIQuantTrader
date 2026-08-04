FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/aiquanttrader/.venv

WORKDIR /build

RUN python -m pip install --no-cache-dir uv==0.11.29

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM builder AS research-builder

RUN uv sync --frozen --no-dev --no-editable --extra research

FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime-base

ENV PATH="/opt/aiquanttrader/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 65532 aiquanttrader \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin aiquanttrader \
    && mkdir -p /etc/aiquanttrader-native /opt/aiquanttrader/release /var/lib/aiquanttrader/data /var/lib/aiquanttrader/state \
    && chown -R 65532:65532 /var/lib/aiquanttrader

COPY --chown=65532:65532 configs /etc/aiquanttrader-native
COPY --from=builder --chown=65532:65532 /build/uv.lock /opt/aiquanttrader/release/uv.lock

USER 65532:65532
WORKDIR /var/lib/aiquanttrader

FROM runtime-base AS research

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends --yes libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=research-builder --chown=65532:65532 /opt/aiquanttrader/.venv /opt/aiquanttrader/.venv

USER 65532:65532
ENTRYPOINT ["aqt-research"]
CMD ["--help"]

FROM runtime-base AS runtime

COPY --from=builder --chown=65532:65532 /opt/aiquanttrader/.venv /opt/aiquanttrader/.venv

EXPOSE 9108 9109 9110 9111 9112 9113

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["aqt-native", "healthcheck", "--url", "http://127.0.0.1:9108/health/ready"]

ENTRYPOINT ["aqt-native"]
CMD ["serve-health", "--config-dir", "/etc/aiquanttrader-native", "--environment", "paper"]
