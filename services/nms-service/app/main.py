from contextlib import asynccontextmanager
from datetime import datetime,timezone
from os import getenv
from uuid import UUID
from fastapi import Depends,FastAPI,HTTPException
from pydantic import BaseModel,ConfigDict
from sqlalchemy.orm import Session
from .database import Base,SessionLocal,engine
from .models import HealthObservation,NasDevice
@asynccontextmanager
async def lifespan(_):Base.metadata.create_all(bind=engine);yield
app=FastAPI(title='NMS Service',version='0.1.0',lifespan=lifespan)
def db():
 s=SessionLocal()
 try:yield s
 finally:s.close()
class NasIn(BaseModel):name:str;host:str
class NasOut(NasIn):model_config=ConfigDict(from_attributes=True);id:UUID;status:str
class ObservationIn(BaseModel):status:str;detail:str|None=None
@app.get('/health')
def health():return {'status':'ok','service':getenv('SERVICE_NAME','nms-service')}
@app.get('/status')
def service_status():return {'service':'nms','phase':'monitoring-api'}
@app.post('/devices',response_model=NasOut)
def create(p:NasIn,s:Session=Depends(db)):
 x=NasDevice(**p.model_dump());s.add(x);s.commit();s.refresh(x);return x
@app.post('/devices/{device_id}/observations')
def observe(device_id:UUID,p:ObservationIn,s:Session=Depends(db)):
 x=s.get(NasDevice,device_id)
 if not x:raise HTTPException(404,'device not found')
 x.status=p.status;x.last_checked_at=datetime.now(timezone.utc);o=HealthObservation(nas_id=x.id,**p.model_dump());s.add(o);s.commit();return {'status':x.status}
