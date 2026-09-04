FROM python:3.13-slim AS archive

WORKDIR /src
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY scripts/build_system_archive.py ./scripts/build_system_archive.py
ENV PYTHONPATH=/src/backend
RUN pip install --no-cache-dir . \
    && python scripts/build_system_archive.py --output /tmp/official-system.zip

FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable \
    && corepack prepare pnpm@latest --activate \
    && pip install --no-cache-dir uv

WORKDIR /opt/n4x
COPY pyproject.toml README.md ./
COPY backend/n4x/__init__.py backend/n4x/__init__.py
COPY backend/n4x/main.py backend/n4x/main.py
COPY backend/n4x/host backend/n4x/host
COPY backend/n4x/graph backend/n4x/graph
COPY backend/n4x/secrets backend/n4x/secrets
COPY backend/n4x/source_store backend/n4x/source_store
COPY backend/n4x/kernel backend/n4x/kernel
COPY backend/n4x/contracts backend/n4x/contracts
COPY backend/n4x/cli backend/n4x/cli
COPY --from=archive /tmp/official-system.zip /opt/n4x/cache/official-system.zip
RUN uv pip install --system .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin n4x \
    && mkdir -p /var/lib/n4x \
    && chown -R n4x:n4x /var/lib/n4x /opt/n4x

USER n4x
ENV N4X_RUNTIME_ROOT=/var/lib/n4x \
    N4X_HTTP_MODE=production \
    N4X_OFFICIAL_SYSTEM_ARCHIVE=/opt/n4x/cache/official-system.zip \
    PYTHONUNBUFFERED=1

EXPOSE 7744
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7744/health')"

# Bind all interfaces inside the container. compose publishes 127.0.0.1:7744 only.
# `docker run -p 7744:7744` would expose an unauthenticated personal runtime.
CMD ["n4x", "serve", "--neo4j", "--mode", "production", "--host", "0.0.0.0", "--port", "7744"]
