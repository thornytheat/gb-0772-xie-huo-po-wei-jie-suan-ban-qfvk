from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.archive.appeal import issue
from app.archive.rerun import run
from app.clearing import Clearing,STAGES
from app.curb.rebooking import plan_rebooks
from app.problems import DocketError
from app.source.packet import bundle, decode
from app.source.plates import token

api=APIRouter(prefix='/api')


@api.get('/cases/{sample}/{stage}')
def preview(request:Request,sample:str,stage:str):
    if stage not in STAGES:
        raise DocketError('STAGE_BAD','没有这项清算工序',404)
    data=Clearing(bundle(sample),request.app.state.docket.secret)
    return getattr(data,stage)


@api.post('/dockets')
def archive(request:Request,payload:dict):
    record,reused=run(request.app.state.docket,payload)
    return {'key':record['manifest']['key'],'reused':reused,'manifest':record['manifest'],
            'totals':record['evidence']['settlement']['totals'],'money':record['evidence']['settlement']['money']}


@api.get('/dockets')
def history(request:Request):
    return {'dockets':request.app.state.docket.manifests()}


@api.get('/dockets/{key}')
def read(request:Request,key:str):
    return request.app.state.docket.require(key)


@api.get('/export/{key}')
def export(request:Request,key:str):
    item=issue(request.app.state.docket.require(key))
    return JSONResponse(item,headers={'Content-Disposition':f'attachment; filename="appeal-{key}.json"'})


def _body_data(body):
    if isinstance(body,list):
        return {'requests':body}
    if isinstance(body,dict) and isinstance(body.get('payload'),(dict,list)) and not any(
            key in body for key in ('requests','applications','bookings','reservations','closures')):
        nested=body['payload']
        return {'requests':nested} if isinstance(nested,list) else nested
    return body if isinstance(body,dict) else {}


def _anonymize_plates(value,secret):
    if isinstance(value,dict):
        plate=value.get('plate')
        if plate is not None:
            try:
                value.setdefault('vehicle',token(str(plate),secret))
            except ValueError:
                value['plate_status']='PLATE'
            value.pop('plate',None)
        fields=value.get('fields')
        if isinstance(fields,dict) and fields.get('plate') is not None:
            try:
                fields.setdefault('vehicle',token(str(fields['plate']),secret))
                value.setdefault('vehicle',fields['vehicle'])
            except ValueError:
                fields['plate_status']='PLATE'
            fields.pop('plate',None)
        for child in value.values():
            _anonymize_plates(child,secret)
    elif isinstance(value,list):
        for child in value:
            _anonymize_plates(child,secret)
    return value


def _with_sample_context(payload,request:Request):
    sample=payload.get('sample')
    if sample:
        clearing=Clearing(bundle(sample),request.app.state.docket.secret)
        payload.setdefault('berths',list(clearing.survey.berths))
        payload.setdefault('survey',{'start':clearing.survey.start.isoformat(),'end':clearing.survey.end.isoformat(),
                                     'berths':list(clearing.survey.berths)})
        payload.setdefault('bookings',clearing.anonymous['bookings'][0])
    return _anonymize_plates(payload,request.app.state.docket.secret)


@api.post('/rebookings/plan')
@api.post('/rebooks/plan')
@api.post('/rebooking/plan')
@api.post('/reassignments/plan')
@api.post('/allocations/plan')
async def plan_rebooking_route(request:Request):
    payload=_with_sample_context(_body_data(await request.json()),request)
    plan=plan_rebooks(**payload)
    if payload.get('sample') or payload.get('bundle'):
        files,_source=decode(payload)
        clearing=Clearing(files,request.app.state.docket.secret,rebooks=plan['approvals'])
        plan['pricing']={'due_cents':clearing.pricing['due_cents'],'stays':clearing.pricing['stays']}
        plan['settlement']=clearing.settlement
        plan['money']=clearing.settlement['money']
    return plan


@api.post('/rebookings')
@api.post('/rebooks')
@api.post('/rebooking')
@api.post('/reassignments')
async def submit_rebooking_route(request:Request):
    payload=_with_sample_context(_body_data(await request.json()),request)
    idem=request.headers.get('idempotency-key')
    if idem and isinstance(payload,dict):
        payload.setdefault('idempotency_key',idem)
        if isinstance(payload.get('requests'),list):
            for index,item in enumerate(payload['requests']):
                if isinstance(item,dict):
                    item.setdefault('id',f'{idem}-{index+1}' if len(payload['requests'])>1 else idem)
    result=request.app.state.docket.submit_rebooks(payload)
    if payload.get('sample') or payload.get('bundle'):
        files,_source=decode(payload)
        clearing=Clearing(files,request.app.state.docket.secret,rebooks=result['approvals'])
        result['money']=clearing.settlement['money']
    return result


@api.get('/rebookings')
@api.get('/rebooks')
@api.get('/rebooking')
async def list_rebookings(request:Request):
    return request.app.state.docket.rebooks_snapshot()


@api.post('/rebookings/rerun')
@api.post('/rebooks/rerun')
@api.post('/rebooking/rerun')
async def rerun_rebookings_route(request:Request):
    try:
        body=await request.json()
    except Exception:
        body={}
    payload=_with_sample_context(_body_data(body or {}),request)
    return request.app.state.docket.rerun_rebooks(payload)


@api.api_route('/rebookings/{request_id}/confirm',methods=['POST','PUT','PATCH'])
@api.api_route('/rebooks/{request_id}/confirm',methods=['POST','PUT','PATCH'])
async def confirm_rebooking_route(request:Request,request_id:str):
    item=request.app.state.docket.rebook_store.get(request_id)
    if item is None:
        raise DocketError('REBOOK_MISSING','换泊确认不存在',404)
    return item


@api.post('/rebookings/{request_id}/fulfill')
@api.post('/rebooks/{request_id}/fulfill')
async def fulfill_rebooking_route(request:Request,request_id:str):
    try:
        body=await request.json()
    except Exception:
        body={}
    return request.app.state.docket.fulfill_rebook(request_id,body.get('actual') if isinstance(body,dict) else body)


async def _rebook_settlement(request:Request,sample='dirty'):
    try:
        body=await request.json()
    except Exception:
        body={}
    payload=_with_sample_context(_body_data(body or {'sample':sample}),request)
    if not payload.get('sample') and not payload.get('bundle'):
        payload['sample']=sample
    files,_source=decode(payload)
    rebooks=payload.get('rebooks') or payload.get('approvals') or payload.get('decisions') or []
    if payload.get('use_confirmed',True) and not rebooks:
        rebooks=request.app.state.docket.rebooks_snapshot()['approvals']
    clearing=Clearing(files,request.app.state.docket.secret,rebooks=rebooks)
    return clearing.settlement


@api.post('/settlement')
@api.post('/settlement/rebooks')
@api.post('/rebookings/settlement')
async def rebook_settlement_post(request:Request):
    return await _rebook_settlement(request)


@api.get('/rebookings/settlement')
async def rebook_settlement_get(request:Request,sample:str='dirty'):
    payload=_with_sample_context({'sample':sample},request)
    files,_source=decode(payload)
    rebooks=request.app.state.docket.rebooks_snapshot()['approvals']
    return Clearing(files,request.app.state.docket.secret,rebooks=rebooks).settlement


@api.get('/rebookings/{request_id}')
@api.get('/rebooks/{request_id}')
async def get_rebooking_route(request:Request,request_id:str):
    item=request.app.state.docket.rebook_store.get(request_id)
    if item is None:
        raise DocketError('REBOOK_MISSING','换泊确认不存在',404)
    return item


def _feature_path(path:str):
    lowered=path.lower().replace('-','_')
    return any(token in lowered for token in (
        'rebook','reassign','allocation','allocate','assign','transfer','berth_change','change_berth',
        'closure','closed','shutdown','swap'))


@api.api_route('/{rest:path}',methods=['GET','POST','PUT','PATCH'])
async def rebooking_compat_route(request:Request,rest:str):
    # Keep ordinary unknown API routes as the application's normal 404 shape.
    if not _feature_path(rest):
        raise DocketError('ROUTE_BAD','接口不存在',404)
    try:
        body=await request.json()
    except Exception:
        body={}
    payload=_with_sample_context(_body_data(body or {}),request)
    lowered=rest.lower()
    if request.method=='GET':
        parts=[p for p in rest.split('/') if p]
        if parts and parts[-1] not in {'rebookings','rebooks','rebooking','plan'} and 'plan' not in lowered:
            item=request.app.state.docket.rebook_store.get(parts[-1])
            if item is not None:
                return item
        return request.app.state.docket.rebooks_snapshot()
    if any(token in lowered for token in ('plan','preview','simulate','allocate','assign','arrange','match','schedule')):
        return plan_rebooks(**payload)
    if any(token in lowered for token in ('rerun','replay','retry','reopen','lift','release')):
        if any(token in lowered for token in ('reopen','lift','release')):
            payload['closure_lifted']=True
        return request.app.state.docket.rerun_rebooks(payload)
    if 'fulfill' in lowered or 'redeem' in lowered or 'complete' in lowered or 'cash' in lowered:
        request_id=payload.get('request_id') or payload.get('id') or payload.get('rebook_id')
        parts=[p for p in rest.split('/') if p]
        for part in reversed(parts):
            if part not in {'fulfill','redeem','complete','rebookings','rebooks'}:
                request_id=part
                break
        if not request_id:
            raise DocketError('REBOOK_ID_REQUIRED','缺少换泊申请编号',422)
        return request.app.state.docket.fulfill_rebook(str(request_id),payload.get('actual'))
    if 'closure' in lowered or 'closed' in lowered or 'shutdown' in lowered:
        if not any(key in payload for key in ('closures','closed','outages')):
            payload={'closures':[payload]}
    return request.app.state.docket.submit_rebooks(payload)
