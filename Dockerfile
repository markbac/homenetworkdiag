# MeshLens server image: engine + web UI, no tray. Runs on the host network so it can
# sweep the LAN, hear mDNS/SSDP and ESP32 probe announcements, and ping like the Windows app.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping iproute2 tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY meshlens ./meshlens
RUN pip install --no-cache-dir .

ENV MESHLENS_HOME=/data \
    MESHLENS_SECRET_STORE=file \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8787), 3)" || exit 1

CMD ["meshlens"]
