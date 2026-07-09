# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt requirements-firebase.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-firebase.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY dashboard/backend/ ./dashboard/backend/
COPY extraction/ ./extraction/
COPY scraper/ ./scraper/
COPY config/ ./config/
COPY . .

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# Run FastAPI app on port 8080 (Cloud Run requirement)
CMD ["uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
