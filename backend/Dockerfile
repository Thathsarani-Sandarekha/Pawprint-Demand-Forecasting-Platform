FROM python:3.11-slim
WORKDIR /app/backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements-integrations.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-integrations.txt
COPY backend/ /app/backend/
RUN mkdir -p /app/data /app/models /app/reports /app/artifacts
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
