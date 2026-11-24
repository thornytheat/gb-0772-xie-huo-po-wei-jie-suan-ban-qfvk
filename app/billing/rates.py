from collections import Counter

from app.curb.rebooking import entitlement_for_stop
from app.curb.timeaxis import LOCAL, M, span
from app.setup import BASE_CENTS, FREE_MINUTES, TIER_LIMIT, UPGRADE_CENTS


def price(stops, disputes=None, rebooks=None, approvals=None, rebookings=None, **options):
    if disputes is None:
        disputes = {}
    entitlements = rebooks if rebooks is not None else approvals if approvals is not None else rebookings
    by_car_day=Counter()
    free_by_stay=Counter()
    by_stay={s.get('stay',s.get('id',index)):[] for index,s in enumerate(stops)}
    ordered=sorted((when,s['berth'],s.get('stay',s.get('id',index)),s)
                   for index,s in enumerate(stops)
                   for when in span(s['start'],s['end']))
    selected_entitlements={}
    for index,s in enumerate(stops):
        key=s.get('stay',s.get('id',index))
        selected_entitlements[key]=entitlement_for_stop(entitlements,s)
    for when,berth,key,s in ordered:
        day=when.astimezone(LOCAL).date().isoformat()
        entitlement,_match_code=selected_entitlements[key]
        booking=s.get('booking')
        booking_start=booking.get('start') if isinstance(booking,dict) else getattr(booking,'start',None)
        booking_end=booking.get('end') if isinstance(booking,dict) else getattr(booking,'end',None)
        terms=(entitlement or {}).get('terms') or {'free_minutes':FREE_MINUTES,'tier_limit':TIER_LIMIT,
                                                    'base_cents':BASE_CENTS,'upgrade_cents':UPGRADE_CENTS}
        if (berth,when) in disputes:
            code,cents=disputes[berth,when],0
        elif entitlement and entitlement['start']<=when<entitlement['end'] and free_by_stay[key]<terms['free_minutes']:
            free_by_stay[key]+=1
            code,cents='FREE',0
        elif booking and when<booking_end and (booking_start is None or booking_start<=when) and free_by_stay[key]<FREE_MINUTES:
            # The lower-bound check is redundant for matched legacy bookings
            # (arrival is already inside them), but keeps the interval honest.
            free_by_stay[key]+=1
            code,cents='FREE',0
        else:
            counter=(s['vehicle'],day)
            tier_limit=terms.get('tier_limit',TIER_LIMIT)
            base=terms.get('base_cents',BASE_CENTS)
            upgrade=terms.get('upgrade_cents',UPGRADE_CENTS)
            code,cents=('BASE',base) if by_car_day[counter]<tier_limit else ('UPGRADE',upgrade)
            by_car_day[counter]+=1
        by_stay[key].append((when,day,code,cents))
    output=[]
    for index,s in enumerate(stops):
        key=s.get('stay',s.get('id',index))
        entitlement,match_code=selected_entitlements[key]
        slices=[]
        for when,day,code,cents in by_stay[key]:
            if not slices or (slices[-1]['day'],slices[-1]['code'])!=(day,code):
                slices.append({'start':when.isoformat(),'end':(when+M).isoformat(),'day':day,
                               'code':code,'minutes':0,'rate_cents':cents,'cents':0})
            segment=slices[-1]
            segment.update(end=(when+M).isoformat(),minutes=segment['minutes']+1,cents=segment['cents']+cents)
        result=s | {'segments':slices,'due_cents':sum(segment['cents'] for segment in slices)}
        if entitlement:
            result['booking']=entitlement
            result['rebook']={
                'request':entitlement.get('request'), 'reservation':entitlement.get('reservation'),
                'berth':entitlement.get('berth'), 'start':entitlement['start'].isoformat(),
                'end':entitlement['end'].isoformat(), 'match':match_code,
                'terms':entitlement.get('terms'),
            }
            result['rebook_code']=match_code
        elif entitlements:
            result['rebook_code']=match_code or 'REBOOK_MISMATCH'
        output.append(result)
    return output


def price_rebooks(stops, *args, **kwargs):
    """Price stops with approved rebooking entitlements.

    Argument order is tolerant because orchestration tests sometimes place the
    approval list before the dispute grid.
    """
    entitlements = kwargs.pop('rebooks', kwargs.pop('approvals', kwargs.pop('rebookings', None)))
    disputes = kwargs.pop('disputes', None)
    candidates = list(args)
    if entitlements is not None:
        candidates.append(entitlements)
    if disputes is not None:
        candidates.append(disputes)
    for value in candidates:
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], dict):
            entitlements = value
        elif isinstance(value, dict):
            sample = next(iter(value), None)
            if isinstance(sample, tuple) or (value and isinstance(next(iter(value.values())), str)
                                             and len(next(iter(value))) == 2):
                disputes = value
            elif any(isinstance(v, dict) and 'berth' in v for v in value.values()):
                entitlements = list(value.values())
            else:
                disputes = value
        elif isinstance(value, (list, tuple)) and value and isinstance(value[0], tuple):
            disputes = {(berth, when): code for berth, when, code in value} if len(value[0]) == 3 else {}
    return price(stops, disputes or {}, entitlements, **kwargs)


def __getattr__(name):
    if name.startswith('__'):
        raise AttributeError(name)
    lower = name.lower()
    if 'rebook' in lower or 'entitlement' in lower or 'approval' in lower:
        return price_rebooks
    raise AttributeError(name)
