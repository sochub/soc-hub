FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for compilation (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    pkg-config \
    libxml2-dev \
    libxslt1-dev \
    libxmlsec1-dev \
    libxmlsec1-openssl \
    xmlsec1 \
    && rm -rf /var/lib/apt/lists/*

# WeasyPrint runtime libraries (incident report PDF export)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 shared-mime-info \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade "pip>=25.3" "setuptools>=83"
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. It owns only the attachments dir (and its HOME for
# font/cache files); the application code stays root-owned and read-only to it.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/attachments \
 && chown app:app /data/attachments \
 && chmod 0700 /data/attachments
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
