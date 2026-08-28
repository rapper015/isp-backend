import bcrypt
import jwt
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
from fastapi import Depends,FastAPI,HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import Base,SessionLocal,engine
from .models import AdminUser,Credential
@asynccontextmanager
async def lifespan(_):Base.metadata.create_all(bind=engine);yield
app=FastAPI(title='AAA Service',version='0.1.0',lifespan=lifespan)
def db():
 s=SessionLocal()
 try:yield s
 finally:s.close()
class CredentialIn(BaseModel):subscriber_id:UUID;username:str;password:str
class Login(BaseModel):username:str;password:str
class AdminIn(BaseModel):email:str;password:str;role:str='super_admin'
@app.get('/health')
def health():return {'status':'ok','service':getenv('SERVICE_NAME','aaa-service')}
@app.get('/status')
def service_status():return {'service':'aaa','phase':'authentication-api'}
@app.post('/credentials')
def create(p:CredentialIn,s:Session=Depends(db)):
 x=Credential(subscriber_id=p.subscriber_id,username=p.username,password_hash=bcrypt.hashpw(p.password.encode(),bcrypt.gensalt()).decode());s.add(x);s.commit();return {'id':str(x.id)}
@app.post('/authenticate')
def authenticate(p:Login,s:Session=Depends(db)):
 x=s.scalar(select(Credential).where(Credential.username==p.username))
 if not x or x.status!='active' or not bcrypt.checkpw(p.password.encode(),x.password_hash.encode()):raise HTTPException(401,'Access-Reject')
 return {'result':'Access-Accept','subscriber_id':str(x.subscriber_id)}
@app.post('/admin-users')
def create_admin(p:AdminIn,s:Session=Depends(db)):
 x=AdminUser(email=p.email.lower(),role=p.role,password_hash=bcrypt.hashpw(p.password.encode(),bcrypt.gensalt()).decode());s.add(x);s.commit();return {'id':str(x.id)}
@app.post('/admin-login')
def admin_login(p:AdminIn,s:Session=Depends(db)):
 x=s.scalar(select(AdminUser).where(AdminUser.email==p.email.lower()))
 if not x or not bcrypt.checkpw(p.password.encode(),x.password_hash.encode()):raise HTTPException(401,'invalid credentials')
 token=jwt.encode({'sub':str(x.id),'role':x.role},getenv('JWT_SECRET','change-me-jwt-secret'),algorithm='HS256')
 return {'token':token,'admin':{'id':str(x.id),'email':x.email,'role':x.role}}
