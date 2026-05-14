# Brikko balance scraper — Playwright service.
#
# Base image: official Playwright Python image with chromium pre-installed
# (~500 MB but saves us the apt-get install + playwright install dance).
# Pinned to v1.49.0 to match pyproject; bumping requires testing chromium
# binary compatibility with the playwright python wheel.
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

# Non-root by default — base image ships with `pwuser` (uid 1000).
WORKDIR /app

# System deps already in base image (chromium libs + fonts). We only need
# our Python deps on top.
COPY pyproject.toml README.md ./
COPY scraper ./scraper

# Install via pip (the base image has Python 3.12). Use --no-cache to keep
# the layer small; the pip cache is throwaway here.
RUN pip install --no-cache-dir -e .

# Cookies volume is mounted at runtime by docker-compose.
RUN mkdir -p /encrypted-cookies && chmod 700 /encrypted-cookies

EXPOSE 9100

# Drop to non-root for runtime.
USER pwuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9100/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "scraper.main:app", "--host", "0.0.0.0", "--port", "9100", "--log-level", "info"]
