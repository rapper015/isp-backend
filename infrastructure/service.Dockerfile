FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

ARG SERVICE_DIRECTORY
WORKDIR /app
COPY shared/runtime/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services /app/services
COPY shared/python /app/shared/python
ENV PYTHONPATH=/app/shared/python

WORKDIR /app/services/${SERVICE_DIRECTORY}
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000"]
