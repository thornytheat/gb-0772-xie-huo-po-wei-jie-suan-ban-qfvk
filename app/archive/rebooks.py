from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, UTC
from pathlib import Path

from app.archive.canonical import serialize
from app.billing.rates import price
from app.curb.rebooking import normalize_request, parse_time, plan_rebooks
from app.problems import DocketError


class RebookStore:
    """Durable rebooking applications and confirmations.

    A single JSON state row is deliberately sufficient for this workflow.
    ``BEGIN IMMEDIATE`` serializes concurrent submissions so two cars can never
    confirm the same overlapping berth-minute.
    """

    def __init__(self, path=None):
        path = path or os.getenv('CURB_DOCKET', 'var/curb.sqlite3')
        self.path = Path(path) if path != ':memory:' else path
        if path != ':memory:':
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._memory_db = None
        if path == ':memory:':
            self._memory_db = sqlite3.connect(':memory:', check_same_thread=False, timeout=15)
            self._memory_db.row_factory = sqlite3.Row
        with self.connect() as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS rebook_state(
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    revision INTEGER NOT NULL,
                    content TEXT NOT NULL
                )
            ''')
            db.execute('INSERT OR IGNORE INTO rebook_state(id,revision,content) VALUES(1,0,?)',
                       (serialize(self._empty()),))

    @contextmanager
    def connect(self):
        with self._lock:
            if self._memory_db is not None:
                database = self._memory_db
                try:
                    database.execute('BEGIN IMMEDIATE')
                    yield database
                    database.commit()
                except Exception:
                    database.rollback()
                    raise
            else:
                with closing(sqlite3.connect(self.path, timeout=15)) as database:
                    database.row_factory = sqlite3.Row
                    database.execute('BEGIN IMMEDIATE')
                    try:
                        yield database
                        database.commit()
                    except Exception:
                        database.rollback()
                        raise

    @staticmethod
    def _empty():
        return {'requests': [], 'bookings': [], 'closures': [], 'decisions': {}, 'updated_utc': None}

    def _read(self, db):
        row = db.execute('SELECT revision,content FROM rebook_state WHERE id=1').fetchone()
        state = json.loads(row['content'])
        return row['revision'], state

    def _write(self, db, revision, state):
        state['updated_utc'] = datetime.now(UTC).isoformat(timespec='seconds')
        db.execute('UPDATE rebook_state SET revision=?,content=? WHERE id=1',
                   (revision + 1, serialize(state)))

    def snapshot(self):
        with self.connect() as db:
            revision, state = self._read(db)
        state['revision'] = revision
        return state

    @staticmethod
    def _payload_lists(payload):
        payload = payload if isinstance(payload, dict) else {'requests': payload}
        requests = payload.get('requests', payload.get('applications', payload.get('rebooks', payload.get('cars', []))))
        if isinstance(requests, dict):
            requests = list(requests.values())
        if not isinstance(requests, list):
            requests = [requests]
        if not requests and any(key in payload for key in (
                'choices', 'options', 'alternatives', 'candidates', 'request_id', 'application_id')):
            requests = [payload]
        bookings = payload.get('bookings', payload.get('reservations', payload.get('existing_bookings', []))) or []
        closures = payload.get('closures', payload.get('closed', payload.get('outages', []))) or []
        if not closures and any(key in payload for key in ('closure', 'closed_from', 'reopen_at', 'closed_until')):
            closures = [payload]
        return payload, requests, bookings or [], closures or []

    immutable_keys=('id','confirmation','confirmed_at','terms','fulfilled','fulfilled_at','actual',
                    'agreed_due_cents','due_cents','agreed_segments')

    @classmethod
    def _merge_durable(cls, decision, old, reuse=None):
        if not old:
            return decision
        if old.get('fulfilled') or old.get('status') == 'FULFILLED':
            # Delivered arrangements and their agreed pricing are frozen.
            decision.update(old)
            return decision
        for key in cls.immutable_keys:
            if key in old:
                decision[key] = old[key]
        old_signature=(old.get('berth'),old.get('start'),old.get('end'))
        new_signature=(decision.get('berth'),decision.get('start'),decision.get('end'))
        if old_signature != new_signature and old.get('berth'):
            decision['previous_assignment']={key:old.get(key) for key in ('berth','start','end')}
            decision['revised']=True
        decision['reused'] = old.get('reused', False) if reuse is None else bool(reuse)
        return decision

    def submit(self, payload):
        _payload, new_requests, new_bookings, new_closures = self._payload_lists(payload)
        with self.connect() as db:
            revision, state = self._read(db)
            known_ids = {str(r.get('id') or r.get('request_id')) for r in state['requests']}
            known_ids.update(state['decisions'])
            added = []
            for index, item in enumerate(new_requests):
                if not isinstance(item, dict):
                    item = {'value': item}
                stable_id = normalize_request(item, index)['id']
                rid = str(item.get('id') or item.get('request_id') or item.get('application_id') or stable_id)
                item['id'] = rid
                if rid in known_ids:
                    continue
                state['requests'].append(item)
                known_ids.add(rid)
                added.append(item)
            state['bookings'].extend(new_bookings)
            state['closures'].extend(new_closures)
            plan = self._plan_state(state)
            for decision in plan['decisions']:
                rid = decision['request']
                old = state['decisions'].get(rid)
                if old:
                    self._merge_durable(decision, old, reuse=True)
                state['decisions'][rid] = _jsonable(decision)
            self._write(db, revision, state)
        return self.result() | {'added': len(added)}

    def rerun(self, payload=None):
        payload = payload or {}
        with self.connect() as db:
            revision, state = self._read(db)
            if payload.get('closures') is not None or payload.get('closed') is not None:
                state['closures'] = payload.get('closures', payload.get('closed')) or []
            if payload.get('reopen') or payload.get('closure_lifted') or payload.get('clear_closures'):
                state['closures'] = []
            if payload.get('bookings') is not None:
                state['bookings'] = payload['bookings']
            plan = self._plan_state(state)
            for decision in plan['decisions']:
                rid = decision['request']
                old = state['decisions'].get(rid)
                if old:
                    self._merge_durable(decision, old, reuse=True)
                state['decisions'][rid] = _jsonable(decision)
            self._write(db, revision, state)
        return self.result()

    @staticmethod
    def _plan_state(state):
        return plan_rebooks(state['requests'], state['bookings'], state['closures'],
                            list(state['decisions'].values()))

    def plan(self, payload=None):
        if payload:
            return plan_rebooks(**_payload_kwargs(payload))
        return self._plan_state(self.snapshot())

    def confirm(self, payload=None):
        return self.submit(payload) if payload is not None else self.rerun({})

    apply = create = add = submit
    replay = rerun

    def result(self):
        state = self.snapshot()
        plan = self._plan_state(state)
        # Durable records are authoritative after terms/fulfilment merge.
        durable = state['decisions']
        decisions = []
        for item in plan['decisions']:
            old = durable.get(item['request'])
            if old:
                self._merge_durable(item, old)
            decisions.append(item)
        approvals = [d for d in decisions if d.get('berth') and d.get('status') != 'WAITING']
        waiting = [d for d in decisions if d.get('status') == 'WAITING']
        reused = [d['request'] for d in decisions if d.get('reused')]
        return {'revision': state.get('revision'), 'requests': state['requests'], 'decisions': decisions,
                'approvals': approvals, 'waiting': waiting, 'reused': reused,
                'created': [d['request'] for d in approvals if not d.get('reused')],
                'assignments': {d['request']: d for d in decisions},
                'counts': {'requests': len(state['requests']), 'approved': len(approvals),
                           'waiting': len(waiting), 'reused': len(reused),
                           'fulfilled': sum(bool(d.get('fulfilled')) for d in decisions)},
                'closures': state['closures']}

    def get(self, request_id):
        return self.snapshot()['decisions'].get(str(request_id))

    def fulfill(self, request_id, actual=None):
        request_id = str(request_id)
        with self.connect() as db:
            revision, state = self._read(db)
            decision = state['decisions'].get(request_id)
            if not decision:
                raise DocketError('REBOOK_MISSING', '换泊确认不存在', 404)
            if decision.get('status') == 'WAITING' or not decision.get('berth'):
                raise DocketError('REBOOK_NOT_APPROVED', '车辆仍在等待可停泊位', 409)
            if not decision.get('fulfilled'):
                decision['fulfilled'] = True
                decision['status'] = 'FULFILLED'
                decision['state'] = 'FULFILLED'
                decision['confirmed'] = True
                decision['fulfilled_at'] = datetime.now(UTC).isoformat(timespec='seconds')
                actual_record = actual if actual is not None else {}
                if actual_record:
                    decision['actual'] = _jsonable(actual_record)
                stop = {
                    'stay': decision.get('request'),
                    'vehicle': decision.get('vehicle'),
                    'berth': (actual_record or {}).get('berth', decision.get('berth')),
                    'start': parse_time((actual_record or {}).get('start', decision.get('start'))),
                    'end': parse_time((actual_record or {}).get('end', decision.get('end'))),
                }
                if stop['start'] and stop['end']:
                    priced = price([stop], {}, [decision])[0]
                    decision['agreed_due_cents'] = priced['due_cents']
                    decision['due_cents'] = priced['due_cents']
                    decision['agreed_segments'] = _jsonable(priced['segments'])
                elif isinstance((actual_record or {}).get('due_cents'), int):
                    decision['agreed_due_cents'] = actual_record['due_cents']
                    decision['due_cents'] = actual_record['due_cents']
                state['decisions'][request_id] = decision
                self._write(db, revision, state)
        return self.get(request_id)

    redeem = complete = fulfill
    all = result


def _jsonable(value):
    return json.loads(serialize(value))


def _payload_kwargs(payload):
    if isinstance(payload, list):
        return {'requests': payload}
    return payload if isinstance(payload, dict) else {}
