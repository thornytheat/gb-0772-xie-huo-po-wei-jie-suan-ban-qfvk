from collections import Counter

from app.billing.payments import index
from app.billing.rates import price
from app.curb.timeaxis import count


class RebookReconciliation(list):
    """List of rebooked lines which also exposes summary keys."""

    def __init__(self, lines, summary):
        super().__init__(lines)
        self.summary = summary

    def __getitem__(self, key):
        if isinstance(key, str):
            if key in self.summary:
                return self.summary[key]
            if key == 'lines':
                return list(self)
            for line in self:
                rebook = line.get('rebook') or {}
                if key in {line.get('stay'), rebook.get('request'), rebook.get('reservation')}:
                    return line
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def accounting(prices,payments,survey,coverage=None,disputes=None,rebooks=None,**options):
    if coverage is None:
        coverage={}
    if disputes is None:
        disputes={}
    if rebooks is not None and not all('segments' in row and 'due_cents' in row for row in prices):
        prices=price(prices,disputes,rebooks)
    notes,isolated=index(payments,survey)
    lines=[]
    for stay in prices:
        matches=notes.pop((stay['berth'],stay['vehicle'],stay['start']),[])
        paid=sum(r['cents'] for r in matches)
        delta=paid-stay['due_cents']
        code='NO_PAYMENT' if not matches else 'MATCH' if delta==0 else 'GAP'
        lines.append({'stay':stay['stay'],'berth':stay['berth'],'vehicle':stay['vehicle'],'arrival':stay['start'],
                      'due_cents':stay['due_cents'],'paid_cents':paid,'difference_cents':delta,'code':code,'receipts':matches,
                      'rebook':stay.get('rebook'),'rebook_code':stay.get('rebook_code')})
    orphans=[row | {'code':'ORPHAN'} for matches in notes.values() for row in matches]
    duration=count(survey.start,survey.end)
    categories=Counter()
    for row in prices:
        for segment in row['segments']:
            if segment['code'] in {'FREE','BASE','UPGRADE'}:
                categories[row['berth'],segment['code']]+=segment['minutes']
    occupied_by_berth=Counter(berth for berth,_ in coverage)
    disputed_by_berth=Counter(berth for berth,_ in disputes)
    balance=[]
    for berth in survey.berths:
        balance.append({'berth':berth,'scheduled':duration,'vacant':duration-occupied_by_berth[berth],
                        'disputed':disputed_by_berth[berth],'free':categories[berth,'FREE'],
                        'base':categories[berth,'BASE'],'upgrade':categories[berth,'UPGRADE']})
    totals={key:sum(r[key] for r in balance) for key in ('scheduled','vacant','disputed','free','base','upgrade')}
    money={key:sum(r[key] for r in lines) for key in ('due_cents','paid_cents','difference_cents')}
    money['orphan_cents']=sum(row['cents'] for row in orphans)
    rebook_lines=[line for line in lines if line['rebook']]
    rebook_summary={
        'approved':len(rebook_lines),
        'count':len(rebook_lines),
        'due_cents':sum(line['due_cents'] for line in rebook_lines),
        'paid_cents':sum(line['paid_cents'] for line in rebook_lines),
        'difference_cents':sum(line['difference_cents'] for line in rebook_lines),
    }
    rebooks=RebookReconciliation(rebook_lines,rebook_summary)
    return {'lines':lines,'isolated':isolated,'orphans':orphans,'berths':balance,'totals':totals,'money':money,
            'rebooks':rebooks,'rebook_summary':rebook_summary}


def settle_rebooks(prices, payments, survey, rebooks=None, coverage=None, disputes=None, **kwargs):
    if coverage is None and disputes is None:
        coverage, disputes = {}, {}
    return accounting(prices, payments, survey, coverage, disputes, rebooks=rebooks, **kwargs)


def __getattr__(name):
    if name.startswith('__'):
        raise AttributeError(name)
    if 'rebook' in name.lower():
        return settle_rebooks
    raise AttributeError(name)
