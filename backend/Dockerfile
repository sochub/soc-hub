# Builder: compilers and -dev headers, used only to build the virtualenv (xmlsec for python3-saml
# compiles against libxml2/libxmlsec1). Nothing from this stage ships except /opt/venv.
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libxml2-dev \
    libxslt1-dev \
    libxmlsec1-dev \
    libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt requirements-overrides.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --upgrade "pip>=26.1.2" "setuptools>=84.1.0"
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps -r requirements-overrides.txt
# Test tooling only for local dev images (docker-compose.yml sets INSTALL_DEV=true); release images skip it.
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

# Runtime: shared libraries only, no compilers or headers.
FROM python:3.11-slim

# libxmlsec1-openssl (pulls libxmlsec1, libxml2, libxslt1.1): the xmlsec binding behind SAML.
# pango/harfbuzz/shared-mime-info: WeasyPrint (incident report PDF export).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxmlsec1-openssl \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY . .

# Run as an unprivileged user. It owns only the attachments dir (and its HOME for
# font/cache files); the application code stays root-owned and read-only to it.
# With a read-only root filesystem, mount /tmp (Celery beat schedule, spooled upload temp files)
# and /home/app (fontconfig cache) writable.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/attachments \
 && chown app:app /data/attachments \
 && chmod 0700 /data/attachments
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
