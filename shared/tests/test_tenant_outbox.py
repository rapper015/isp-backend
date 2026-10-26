import asyncio
import json
import unittest
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import Column, Uuid, String, Integer, JSON, DateTime, ForeignKey, create_engine, select, delete
from sqlalchemy.orm import declarative_base, Session
from isp_shared.tenancy import scope_session, validate_tenant_scope
from isp_shared.outbox import flush, envelope

Base = declarative_base()


class Record(Base):
    __tablename__ = 'records'
    id = Column(Uuid, primary_key=True, default=uuid4)
    tenant_id = Column(Uuid, nullable=False)
    name = Column(String)


class Child(Base):
    __tablename__ = 'children'
    id = Column(Uuid, primary_key=True, default=uuid4)
    record_id = Column(Uuid, ForeignKey('records.id'))


class Event(Base):
    __tablename__ = 'events'
    id = Column(Uuid, primary_key=True, default=uuid4)
    tenant_id = Column(Uuid)
    event_type = Column(String, default='test.created')
    payload = Column(JSON, default=dict)
    attempts = Column(Integer, default=0)
    published_at = Column(DateTime)


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.a, self.b = uuid4(), uuid4()
        with Session(self.engine) as db:
            a, b = Record(tenant_id=self.a), Record(tenant_id=self.b)
            db.add_all([a, b]); db.flush()
            self.a_id, self.b_id = a.id, b.id
            db.add_all([Child(record_id=a.id), Child(record_id=b.id)])
            db.commit()

    def scoped(self):
        db = Session(self.engine)
        scope_session(db, Base, {'allowed_tenant_ids': {self.a}})
        return db

    def test_reads_and_children_are_scoped(self):
        with self.scoped() as db:
            self.assertEqual(len(db.scalars(select(Record)).all()), 1)
            self.assertIsNone(db.get(Record, self.b_id))
            self.assertEqual(len(db.scalars(select(Child)).all()), 1)

    def test_cross_tenant_insert_and_reference_rejected(self):
        with self.scoped() as db:
            db.add(Record(tenant_id=self.b))
            with self.assertRaises(HTTPException): db.flush()
        with self.scoped() as db:
            db.add(Child(record_id=self.b_id))
            with self.assertRaises(HTTPException): db.flush()

    def test_bulk_delete_does_not_delete_other_tenant(self):
        with self.scoped() as db:
            db.execute(delete(Child)); db.commit()
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalars(select(Child)).all()), 1)

    def test_query_body_mismatch_and_wildcard_tenant(self):
        async def check(query, body):
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(body).encode()}
            req = Request({'type': 'http', 'query_string': query.encode(),
                           'headers': [(b'content-type', b'application/json')]}, receive)
            return await validate_tenant_scope(req, {'tenant_id': str(self.a), 'permissions': ['*']})
        with self.assertRaises(HTTPException):
            asyncio.run(check(f'tenant_id={self.a}', {'tenant_id': str(self.b)}))
        with self.assertRaises(HTTPException):
            asyncio.run(check(f'tenant_id={self.b}', {}))
        self.assertEqual(asyncio.run(check('', {})), {self.a})

    def test_failed_publication_retries_same_id(self):
        with Session(self.engine) as db:
            row = Event(tenant_id=self.a)
            db.add(row); db.commit()
            event_id = envelope(row, 'test')['event_id']
            with self.assertLogs('isp.outbox', level='ERROR'):
                self.assertEqual(flush(db, Event, 'test', publisher=lambda r: None), [])
            self.assertIsNone(row.published_at)
            self.assertEqual(row.attempts, 1)
            seen = []
            def publish(r):
                seen.append(envelope(r, 'test')['event_id'])
                return True
            self.assertEqual(flush(db, Event, 'test', publisher=publish), ['test.created'])
            self.assertEqual(seen, [event_id])
            self.assertIsNotNone(row.published_at)
            self.assertEqual(flush(db, Event, 'test', publisher=publish), [])
