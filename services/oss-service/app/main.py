from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import Order, Subscriber
@asynccontextmanager
async def lifespan(_app): Base.metadata.create_all(bind=engine); yield
app=FastAPI(title='OSS Service',version='0.1.0',lifespan=lifespan)
def db():
 s=SessionLocal()
 try: yield s
 finally: s.close()
class SubscriberCreate(BaseModel):
 subscriber_code:str; customer_id:UUID; plan_id:UUID; username:str; password_hash:str; installation_address:str; service_type:str='pppoe'
class SubscriberResponse(SubscriberCreate):
 model_config=ConfigDict(from_attributes=True)
 id:UUID; status:str
class OrderCreate(BaseModel): order_number:str; order_type:str; customer_id:UUID; subscriber_id:UUID|None=None; plan_id:UUID|None=None
class OrderResponse(OrderCreate):
 model_config=ConfigDict(from_attributes=True)
 id:UUID; status:str
@app.get('/health')
def health(): return {'status':'ok','service':getenv('SERVICE_NAME','oss-service')}
@app.get('/status')
def service_status(): return {'service':'oss','phase':'subscriber-order-api'}
@app.post('/subscribers',response_model=SubscriberResponse,status_code=status.HTTP_201_CREATED)
def create_subscriber(p:SubscriberCreate,s:Session=Depends(db)):
 x=Subscriber(**p.model_dump()); s.add(x)
 try:s.commit()
 except Exception as e:s.rollback();raise HTTPException(409,'subscriber code or username exists') from e
 s.refresh(x);return x
@app.get('/subscribers',response_model=list[SubscriberResponse])
def list_subscribers(s:Session=Depends(db)):return list(s.scalars(select(Subscriber)))
@app.post('/orders',response_model=OrderResponse,status_code=status.HTTP_201_CREATED)
def create_order(p:OrderCreate,s:Session=Depends(db)):
 x=Order(**p.model_dump());s.add(x);s.commit();s.refresh(x);return x
@app.get('/orders',response_model=list[OrderResponse])
def list_orders(s:Session=Depends(db)):return list(s.scalars(select(Order)))
