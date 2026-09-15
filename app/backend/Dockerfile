FROM python:3.11-slim

ARG BUILD_ID=dev
ARG GIT_SHA=dev
ENV BUILD_ID=$BUILD_ID
ENV GIT_SHA=$GIT_SHA

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    pkg-config \
    libxml2-dev \
    libxmlsec1-dev \
    libxmlsec1-openssl \
    xmlsec1 \
    libreoffice \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi-dev \
    # OCR dependencies for scanned PDFs
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY app/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model for NER name extraction (~12MB)
RUN python -m spacy download en_core_web_sm

# Copy application code
COPY app/ ./app/
RUN rm -rf /app/app/backend/services/wip

# Alembic (migrations run in entrypoint for PostgreSQL)
COPY alembic.ini .
COPY alembic ./alembic

COPY app/backend/scripts/wait_for_ollama.py /app/wait_for_ollama.py
COPY app/backend/scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create data directory for SQLite
RUN mkdir -p /data

# Run as non-root user for security
RUN useradd -m -u 1000 appuser
# Ensure the app directory and any writable paths are owned by appuser
RUN chown -R appuser:appuser /app
RUN chown -R appuser:appuser /data
USER appuser

# Set environment variables
ENV PYTHONPATH=/app
ENV DATABASE_URL=/data/resume_screener.db
ENV OLLAMA_BASE_URL=http://ollama:11434

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
# Single worker default; docker-compose.prod.yml overrides to --workers 4 for the VPS.
CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
