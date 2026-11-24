from datetime import UTC,datetime
from time import perf_counter

from app.archive.canonical import digest
from app.clearing import Clearing
from app.setup import POLICY
from app.source.packet import decode,fingerprint


def run(docket, payload):
    started=perf_counter()
    if isinstance(payload,dict) and any(key in payload for key in (
            'closures','closed','outages','closure_lifted','reopen','clear_closures')):
        docket.rerun_rebooks(payload)
    original,source=decode(payload)
    hashes=fingerprint(original)
    rebooks = payload.get('rebooks') if isinstance(payload,dict) else None
    if rebooks is None and isinstance(payload,dict) and payload.get('use_confirmed_rebooks', True):
        try:
            rebooks=docket.rebook_store.result().get('approvals')
        except AttributeError:
            rebooks=None
    key_input={'files':hashes,'policy':POLICY}
    if rebooks:
        key_input['rebooks']=digest(rebooks)
    key=digest(key_input)
    existing=docket.find(key)
    if existing:
        return existing,True
    clearing=Clearing(original,docket.secret,rebooks=rebooks or [])
    report=clearing.report()
    if rebooks:
        report['rebooking']={'approvals':rebooks if isinstance(rebooks,list) else [],
                             'due_cents':report['pricing']['due_cents']}
    manifest={'key':key,'source':source,'rule':POLICY,'inputs':hashes,'evidence_sha256':digest(report),
              'survey':vars(clearing.survey),'created_utc':datetime.now(UTC).isoformat(timespec='seconds'),
              'elapsed_ms':round((perf_counter()-started)*1000)}
    return docket.publish(key,{'manifest':manifest,'evidence':report})
