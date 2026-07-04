# Backend image: FastAPI + in-process scheduler + DuckDB + LightGBM model.
# Runs the whole always-on backend in one container (one DuckDB owner).
#
# Build:  docker build -t stock-monitor-api .
# Run:    docker run -p 8137:8137 --env-file .env \
#           -v $PWD/data:/app/data -v $PWD/models:/app/models stock-monitor-api
#
# First run needs a trained model — either mount a models/ volume that already has
# one, or exec `stock-monitor-train` inside the container once.
#
# FinBERT (torch, large) is NOT installed here to keep the image lean; the default
# VADER sentiment backend works out of the box. To use FinBERT, install the extra
# in a derived image and set SENTIMENT_BACKEND=finbert.

FROM python:3.12-slim

# LightGBM needs the OpenMP runtime on Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Persistent data + model live in mounted volumes.
ENV RUN_SCHEDULER=1
EXPOSE 8137

CMD ["uvicorn", "stock_monitor.api.app:app", "--host", "0.0.0.0", "--port", "8137"]
