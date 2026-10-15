FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    OPSFUSION_DATA_ROOT=/data XDG_CACHE_HOME=/data/cache YOLO_CONFIG_DIR=/data/cache/ultralytics \
    OPSFUSION_YOLO_WEIGHTS=/app/weights/yolov8n.pt
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home opsfusion \
    && mkdir -p /data /app/weights && chown -R 10001:10001 /data
COPY requirements-*.txt ./
RUN python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r requirements-app.txt -r requirements-sensor.txt -r requirements-audio.txt -r requirements-vision.txt
RUN python -c "import urllib.request,hashlib; p='/app/weights/yolov8n.pt'; urllib.request.urlretrieve('https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt',p); assert hashlib.sha256(open(p,'rb').read()).hexdigest()=='f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36'"
COPY opsfusion /app/opsfusion
COPY dashboard.py /app/dashboard.py
COPY data/incident-demo /app/data/incident-demo
COPY data/segmentation-regression /app/data/segmentation-regression
COPY data/sensor-demo /app/data/sensor-demo
COPY data/audio-demo /app/data/audio-demo
COPY data/events.jsonl data/ground_truth.json /app/data/
COPY config/zone.example.json /app/config/zone.example.json
USER 10001:10001
WORKDIR /data
HEALTHCHECK --interval=20s --timeout=8s --start-period=60s --retries=3 CMD ["python","-m","opsfusion.service","--healthcheck"]
ENV PYTHONPATH=/app
CMD ["python","-m","opsfusion.service","--bind","0.0.0.0"]
