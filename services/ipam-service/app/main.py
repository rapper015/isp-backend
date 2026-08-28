from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
from fastapi import Depends,FastAPI,HTTPException
from pydantic import BaseModel,ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import Base,SessionLocal,engine
from .models import IPAddress,IPPool
@asynccontextmanager
async def lifespan(_):Base.metadata.create_all(bind=engine);yield
app=FastAPI(title='IPAM Service',version='0.1.0',lifespan=lifespan)
def db():
 s=SessionLocal()
 try:yield s
 finally:s.close()
class PoolIn(BaseModel):pool_code:str;network_cidr:str
class PoolOut(PoolIn):model_config=ConfigDict(from_attributes=True);id:UUID
class AddressIn(BaseModel):pool_id:UUID;address:str
class AddressOut(AddressIn):model_config=ConfigDict(from_attributes=True);id:UUID;status:str;subscriber_id:UUID|None
@app.get('/health')
def health():return {'status':'ok','service':getenv('SERVICE_NAME','ipam-service')}
@app.get('/status')
def service_status():return {'service':'ipam','phase':'allocation-api'}
@app.post('/pools',response_model=PoolOut)
def create_pool(p:PoolIn,s:Session=Depends(db)):
 x=IPPool(**p.model_dump());s.add(x);s.commit();s.refresh(x);return x
@app.post('/addresses',response_model=AddressOut)
def add_address(p:AddressIn,s:Session=Depends(db)):
 if not s.get(IPPool,p.pool_id):raise HTTPException(404,'pool not found')
 x=IPAddress(**p.model_dump());s.add(x);s.commit();s.refresh(x);return x
@app.post('/addresses/{address_id}/allocate',response_model=AddressOut)
def allocate(address_id:UUID,subscriber_id:UUID,s:Session=Depends(db)):
 x=s.get(IPAddress,address_id)
 if not x:raise HTTPException(404,'address not found')
 if x.status!='available':raise HTTPException(409,'address not available')
 x.status='allocated';x.subscriber_id=subscriber_id;s.commit();s.refresh(x);return x
