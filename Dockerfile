FROM python:3.12-slim

# Don't write .pyc files, don't buffer stdout (logs show up immediately)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TVCHARTS_HOST=0.0.0.0 \
    TVCHARTS_PORT=8050

WORKDIR /app

# Install dependencies first so this layer is cached across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tvcharts/ tvcharts/
COPY run.py .

# Run as an unprivileged user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"TVCHARTS_PORT\"]}/', timeout=4)" || exit 1

CMD ["python", "run.py"]
