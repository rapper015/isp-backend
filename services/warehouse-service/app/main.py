from os import getenv
from fastapi import FastAPI

app = FastAPI(title="Data Warehouse Service", version="0.1.0")
@app.get("/health")
def health(): return {"status": "ok", "service": getenv("SERVICE_NAME", "warehouse-service")}
@app.get("/status")
def status(): return {"service": "warehouse", "phase": "foundation"}
