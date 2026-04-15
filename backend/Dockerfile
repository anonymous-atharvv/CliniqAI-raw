# CliniQAI Backend — Production Dockerfile
# Railway auto-detects this via railway.json
FROM python:3.11-slim-bookworm

LABEL maintainer="CliniQAI Engineering"

# Security: non-root user
RUN groupadd -r cliniqai && useradd -r -g cliniqai cliniqai

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (Docker layer cache)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Railway sets PORT env var dynamically
ENV PORT=8000
USER cliniqai

# Railway uses this CMD (can also be overridden by railway.json)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --loop uvloop --log-level warning
