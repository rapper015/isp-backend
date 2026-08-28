from os import getenv
from fastapi import FastAPI

app = FastAPI(title="AIOps Service", version="0.1.0")
@app.get("/health")
def health(): return {"status": "ok", "service": getenv("SERVICE_NAME", "aiops-service")}
@app.get("/status")
def status(): return {"service": "aiops", "phase": "foundation"}
