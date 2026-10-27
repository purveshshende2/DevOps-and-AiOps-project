"""FastAPI microservice: health, order creation, and a stress endpoint for demos."""
import os
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"api","msg":"%(message)s"}',
)
log = logging.getLogger("api")

app = FastAPI(title="AI Ops API")

MEMORY_HOG: Optional[bytearray] = None


class Order(BaseModel):
    item: str
    qty: int = 1


@app.get("/")
def root():
    log.info("frontend requested root")
    return {"service": "api", "status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "api"}


@app.post("/order")
def create_order(order: Order):
    order_id = str(uuid.uuid4())[:8]
    log.info(f"order created id={order_id} item={order.item} qty={order.qty}")
    return {
        "order_id": order_id,
        "item": order.item,
        "qty": order.qty,
        "status": "created",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/stress")
def stress(mb: int = 256, duration: int = 30):
    """Allocate memory to simulate pressure / possible OOMKilled (demo only)."""
    global MEMORY_HOG
    log.warning(f"stress requested mb={mb} duration={duration}s")
    try:
        MEMORY_HOG = bytearray(mb * 1024 * 1024)
        time.sleep(min(duration, 60))
        return {"status": "stress complete", "mb": mb}
    finally:
        MEMORY_HOG = None


@app.on_event("shutdown")
def shutdown():
    log.info("api shutting down")
