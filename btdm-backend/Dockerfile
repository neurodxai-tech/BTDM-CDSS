# Two images from one Dockerfile:
#   target "api"    — FastAPI web API, no PyTorch (small, starts fast)
#   target "worker" — inference worker with PyTorch; owns the model
FROM python:3.11-slim AS api
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY app ./app
RUN mkdir -p /app/weights /app/object-store && useradd --uid 10001 --no-create-home btdm && chown -R btdm /app
USER btdm
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

FROM api AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# CPU wheels by default (≈200 MB instead of ≈2 GB). For an NVIDIA GPU server
# build with: --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url ${TORCH_INDEX} -r requirements.txt
RUN mkdir -p /app/model-cache && chown -R btdm /app/model-cache
USER btdm
HEALTHCHECK NONE
CMD ["python", "-m", "app.worker"]
