# syntax=docker/dockerfile:1.7
#
# KiroCrew + OpenCode appliance image.
#
# Build from the repository root:
#   docker build -f deploy/opencode/Dockerfile \
#     --build-arg OPENCODE_VERSION=2.2.2 \
#     -t kirocrew-opencode:local .
#
# This is a self-contained image: Python 3.12, the KiroCrew wheel built from
# THIS checkout (the local WhatsApp/document fixes live there), the OpenCode
# ACP harness, WhatsApp (neonize), Telegram support, local STT and ffmpeg.
# It deliberately does not need kiro-cli/KAS: the ACP backend is OpenCode.
#
# Nothing personal is copied. dist/ carries one wheel, deploy/opencode carries
# only the seed and entrypoint scripts, and .dockerignore excludes everything
# else. The first-run seed writes the user's config and secrets into the
# mounted home volume, never into an image layer.

ARG PYTHON_BASE=python:3.12-slim-trixie
FROM ${PYTHON_BASE}

ARG TARGETARCH
ARG KIROCREW_VERSION=dev
ARG OPENCODE_VERSION=2.2.2
# sha512 of the pinned npm tarballs (registry.npmjs.org), converted from the
# registry's dist.integrity. Bump together with OPENCODE_VERSION.
ARG OPENCODE_SHA512_X64=c1ad289751d468a3434ea8dfa89c2a9aade7e2d95e8c1cb6513e3cc15442ca94541b2f566c8a5778bf719479a58c0a1d403e8771947676a8896054e50cca8854
ARG OPENCODE_SHA512_ARM64=732a7a97548b14657b3715dde458012094fc5799908418ecf85f6f878a5149a9bd9111a6bff9d412f80f31729f36b65db2595394ba0fbf38075b3419cf67dfd4
ARG SOURCE_REPO_URL="https://github.com/studio300-code/KiroCrew"

LABEL org.opencontainers.image.title="KiroCrew + OpenCode" \
      org.opencontainers.image.description="KiroCrew gateway with the OpenCode ACP harness, WhatsApp, Telegram and local STT" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${KIROCREW_VERSION}" \
      org.opencontainers.image.source="${SOURCE_REPO_URL}" \
      dev.kirocrew.stack="opencode" \
      dev.kirocrew.opencode.version="${OPENCODE_VERSION}"

# Runtime tools the native environment uses:
#   ffmpeg/ffprobe  — voice conversion and video frame sampling
#   libgomp1        — pywhispercpp OpenMP runtime
#   libmagic1       — attachment type sniffing
#   sqlite3         — the WhatsApp archive skill reads wa.db with it
#   jq              — agent shell work and diagnostics
#   tini            — PID 1, reaps agent subprocess trees
#   procps          — ps for process diagnostics
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        jq \
        libgomp1 \
        libmagic1 \
        procps \
        ripgrep \
        sqlite3 \
        tini \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# OpenCode, pinned and verified. npm is the vendor's official distribution
# channel for the CLI; the tarball contains package/bin/opencode.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) PKG="cli-linux-x64"; SHA="${OPENCODE_SHA512_X64}" ;; \
        arm64) PKG="cli-linux-arm64"; SHA="${OPENCODE_SHA512_ARM64}" ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://registry.npmjs.org/@opencode%2f${PKG}/-/${PKG}-${OPENCODE_VERSION}.tgz"; \
    curl --proto '=https' --tlsv1.2 -fsSL "$URL" -o /tmp/opencode.tgz; \
    echo "${SHA}  /tmp/opencode.tgz" | sha512sum -c -; \
    mkdir -p /tmp/opencode; \
    tar -xzf /tmp/opencode.tgz -C /tmp/opencode; \
    install -m 0755 /tmp/opencode/package/bin/opencode /usr/local/bin/opencode; \
    rm -rf /tmp/opencode /tmp/opencode.tgz; \
    opencode --version

# The KiroCrew wheel built from this checkout. The wheel is bind-mounted, never
# COPYed, so the ~100 MB artifact does not become a layer of its own.
RUN --mount=type=bind,source=dist,target=/tmp/wheels \
    set -eux; \
    WHEEL_COUNT=$(ls /tmp/wheels/*.whl | wc -l); \
    [ "${WHEEL_COUNT}" -eq 1 ] || { echo "expected exactly one wheel, found ${WHEEL_COUNT}" >&2; exit 1; }; \
    pip install --no-cache-dir /tmp/wheels/*.whl; \
    kirocrew --version; \
    python -c 'import kiro_crew.cli'

# WhatsApp and local STT are optional extras in Kiro Crew; the appliance wants
# both. neonize ships a manylinux Go shared object for amd64/arm64.
RUN pip install --no-cache-dir \
        'neonize==0.4.3.post0' \
        'pywhispercpp>=1.5,<2' \
        'PyJWT[crypto]==2.15.0' \
        'lark-oapi>=1.4,<2'

# Keep the image's provenance honest: it runs a wheel, but it is the Docker
# channel, not the pip channel.
RUN set -eux; \
    PKG_DIR="$(python -c 'import kiro_crew, pathlib; print(pathlib.Path(kiro_crew.__file__).parent)')"; \
    printf '%s\n' \
      '"""Build-time provenance. GENERATED -- do not edit."""' \
      '' \
      'from __future__ import annotations' \
      '' \
      'DISTRIBUTION = "docker"' \
      > "${PKG_DIR}/_build_info.py"; \
    python -c 'from kiro_crew import beacon; assert beacon.distribution() == "docker"'

# Non-root runtime user. HOME holds ALL persistent state; mount a named volume
# at /home/kirocrew (named volumes inherit the image's ownership on first use).
RUN useradd --create-home --uid 1000 --shell /bin/bash kirocrew

COPY --chmod=755 deploy/opencode/entrypoint.sh /usr/local/bin/kirocrew-entrypoint
COPY --chmod=755 deploy/opencode/seed.py /usr/local/bin/kirocrew-opencode-seed

# Container networking: published ports map to the bridge interface, so the
# gateway must bind beyond loopback INSIDE the container namespace.
ENV HOME=/home/kirocrew \
    KIROCREW_BIND=0.0.0.0 \
    OPENCODE_BIN=/usr/local/bin/opencode \
    KIROCREW_OPENCODE_STACK=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

USER kirocrew
WORKDIR /home/kirocrew

EXPOSE 5476

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${KIROCREW_PORT:-5476}/api/health" || exit 1

ENTRYPOINT ["kirocrew-entrypoint"]
CMD ["gateway"]
