# code-sama OS — single-container distribution
#
# This image bundles everything that the Win95-themed agent OS needs:
#   • Python + uvicorn + LangGraph stack (the host UI server)
#   • Playwright Chromium (the in-OS browser app)
#   • Xvfb + xdotool + x11-utils (so real Linux GUI programs can run
#     headless and we can stream + drive them from the agent)
#   • A dev toolchain (gcc/g++, python3, node, git, make, vim) so
#     code-sama can actually build and run native software
#   • Several demo X11 apps (xclock, xeyes, xterm, xcalc, xpaint)
#
# Build / run:
#   docker compose up --build
#   open http://localhost:8765
#
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# ─── system layer ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        # X stack used to host real Linux GUI apps inside the container
        xvfb x11-utils xdotool imagemagick fluxbox \
        # ffmpeg: preferred frame-capture backend for linux_broker
        # (much cheaper per-frame than ImageMagick's `import`)
        ffmpeg \
        # openbox: a tiny WM used by the broker's shared-display mode
        # (lets several Linux apps share one Xvfb and see each other)
        openbox \
        # AT-SPI stack: lets the loader's element resolver read widget
        # geometry from GTK/Qt/Electron apps so clicks land exactly on
        # the right button instead of guessed pixels.
        at-spi2-core python3-gi gir1.2-atspi-2.0 \
        # demo GUI apps so we can show off the wiring out of the box
        x11-apps xterm xcalc \
        # general dev toolchain available to code-sama
        build-essential git make cmake pkg-config \
        curl wget jq vim nano \
        nodejs npm \
        # Playwright runtime deps (Chromium)
        ca-certificates fonts-liberation libnss3 libatk-bridge2.0-0 \
        libatk1.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libxkbcommon0 \
        libpango-1.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# ImageMagick by default disallows screen grabs of X; relax that here
# because the only X server reachable from inside the container is the
# one we ourselves run.
RUN sed -i 's|<policy domain="coder" rights="none" pattern="X[BCMP]*M*" />|<!-- &-->|g' \
        /etc/ImageMagick-6/policy.xml || true

WORKDIR /opt/code-sama

# ─── python layer ────────────────────────────────────────────────────
COPY requirements.txt /opt/code-sama/requirements.txt
RUN pip install -r /opt/code-sama/requirements.txt \
 && python -m playwright install --with-deps chromium

# ─── project files ───────────────────────────────────────────────────
COPY server /opt/code-sama/server
COPY web    /opt/code-sama/web
COPY tools  /opt/code-sama/tools
COPY apps   /opt/code-sama/apps
COPY HARNESS.md /opt/code-sama/HARNESS.md
COPY .env.example /opt/code-sama/.env.example

# Persistent workspace for code-sama's own projects + installed app
# manifests. The loader writes generated manifests under
# /workspace/apps/generated/ so they survive container rebuilds.
RUN mkdir -p /workspace/apps && chmod -R 0777 /workspace

EXPOSE 8765
ENV HOST=0.0.0.0 \
    PORT=8765 \
    BROWSER_HEADLESS=true \
    WORKSPACE_DIR=/workspace \
    XAUTHORITY=/tmp/.Xauth

# Each Linux app gets its own Xvfb on a high display id, managed by
# server/linux_broker.py at runtime. We do NOT start one here.
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8765"]
