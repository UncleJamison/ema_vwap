# ==============================================================================
# Production Multi-Stage Dockerfile for EMA + VWAP Trading Suite Daemon
# ==============================================================================

FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment in /opt/venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final Runtime Image
FROM python:3.13-slim AS runner

WORKDIR /app

# Install runtime utilities (curl for healthcheck)
# + WeasyPrint dependencies: GTK/Cairo/Pango libraries for PDF generation
# + PostgreSQL client (psql) for debugging/management
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    libgobject-2.0-0 \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create persistent directories and set permissions
RUN mkdir -p /app/data /app/logs && \
    chmod 777 /app/data /app/logs

# Copy application source code and entrypoint
COPY src/ /app/src/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Default environment variables
# PostgreSQL connection via environment variables (set in docker-compose.yml)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    HOST=0.0.0.0

EXPOSE 8080
STOPSIGNAL SIGTERM

# Healthcheck to verify trading engine and web UI are responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/ && curl -f http://localhost:8080/api/paper/engine/status || exit 1

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
CMD ["python", "-m", "src.app"]
