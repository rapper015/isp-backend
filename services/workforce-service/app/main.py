from os import getenv
from fastapi import FastAPI

app = FastAPI(title="Workforce Service", version="0.1.0")
@app.get("/health")
def health(): return {"status": "ok", "service": getenv("SERVICE_NAME", "workforce-service")}
@app.get("/status")
def status(): return {"service": "workforce", "phase": "foundation"}
