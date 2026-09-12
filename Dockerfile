FROM python:3.12-slim

# Install system utilities and libraries needed by headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Install dependencies ONLY first, as a layer that is cacheable independent of source
# changes. Root cause (H-12): pyproject.toml declares `readme = "README.md"`, so
# installing the local project itself here -- before the source tree (including
# README.md) is copied in -- fails with only the lockfile inputs present.
# `--no-install-project` defers installing the "tandem" project itself to the step
# below, once its build inputs actually exist.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Install Playwright Chromium browser (also cacheable, independent of source changes)
RUN uv run playwright install chromium --with-deps

# Copy application source (README.md included) and install the project itself
COPY . .
RUN uv sync --frozen

# Expose Tandem and Simulator ports (8001 Institution Alpha, 8002 Institution Beta)
EXPOSE 8000 8001 8002 8003 8004

# Default command starts all simulators and the operator console
CMD ["uv", "run", "tandem"]
