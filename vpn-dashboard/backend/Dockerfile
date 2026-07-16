FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE 8000

# Single worker: APScheduler and the change-stream watcher must run exactly once.
# --proxy-headers: real client IPs from nginx (login throttle would otherwise
# lump every visitor into the proxy's bucket). Safe: only nginx reaches this
# container inside the compose network.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
