#!/usr/bin/env python3
"""Forward-only, non-trading evaluation of illustrative stress scenarios.
The archive never backfills forecasts or uses a future price when issuing one.
"""
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
AUDIT=DATA/'scenario_audit.json'
PRICES=DATA/'prices.json'
HORIZONS={30:(.5,1.5),90:(.2,2.5)}
MAX_AGE=timedelta(hours=6)
OBSERVE_WINDOW=timedelta(hours=48)
KEEP_DAYS=180

def timestamp(value):
    if not isinstance(value,str):return None
    try:
        obj=datetime.fromisoformat(value.replace('Z','+00:00'))
        return obj.astimezone(timezone.utc) if obj.tzinfo else None
    except (ValueError,OverflowError):return None

def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>0

def process(snapshot, archive, now):
    now=now.astimezone(timezone.utc)
    stamp=timestamp(snapshot.get('updated_at'))
    if not stamp or stamp>now+timedelta(minutes=5) or now-stamp>MAX_AGE:
        raise ValueError('Market snapshot not fresh: no forward test written')
    rows=snapshot.get('coins')
    if not isinstance(rows,list) or len(rows)<3:
        raise ValueError('Not enough market coins')
    market={c['id']:c for c in rows if isinstance(c,dict) and
            isinstance(c.get('id'),str) and number(c.get('current_price'))}
    if len(market)<3:raise ValueError('Not enough positive market prices')
    existing=archive.get('records',[]) if isinstance(archive,dict) else []
    if not isinstance(existing,list):raise ValueError('Invalid previous archive')
    # Keep records at least 90 days until outcomes become eligible; never revise resolved entries.
    keep_after=now-timedelta(days=KEEP_DAYS)
    records=[]
    seen=set()
    for original in existing:
        if not isinstance(original,dict):continue
        issued=timestamp(original.get('issued_at'))
        ident=original.get('id')
        horizon=original.get('horizon_days')
        if not issued or not isinstance(ident,str) or horizon not in HORIZONS:continue
        if issued<keep_after:continue
        key=(issued.date().isoformat(),ident,horizon)
        if key in seen:continue
        seen.add(key)
        row=dict(original)
        if row.get('status')=='PENDING':
            due=issued+timedelta(days=horizon)
            if now>=due and now<=due+OBSERVE_WINDOW:
                actual=market.get(ident,{}).get('current_price')
                if number(actual):
                    lower=row.get('lower_usd')
                    upper=row.get('upper_usd')
                    start=row.get('start_usd')
                    if all(number(x) for x in (lower,upper,start)):
                        row['status']='EVALUATED'
                        row['observed_at']=stamp.isoformat()
                        row['actual_usd']=actual
                        row['in_range']=lower<=actual<=upper
                        row['realized_return_pct']=round((actual/start-1)*100,3)
            elif now>due+OBSERVE_WINDOW:
                row['status']='MISSED'
                row['note']='No timely market observation in due window'
        records.append(row)
    # Issue at most one non-retroactive record per token/horizon per UTC Monday.
    # The first ever run seeds an initial cohort regardless of weekday.
    make_new=now.weekday()==0 or not records
    if make_new:
        for ident,c in market.items():
            for horizon,(lo,hi) in HORIZONS.items():
                key=(stamp.date().isoformat(),ident,horizon)
                if key in seen:continue
                start=c['current_price']
                records.append({'id':ident,'symbol':c.get('symbol',''),
                                'issued_at':stamp.isoformat(),'horizon_days':horizon,
                                'model':'illustrative_stress_v1','start_usd':start,
                                'lower_usd':round(start*lo,15),
                                'upper_usd':round(start*hi,15),
                                'status':'PENDING','security_decision':'SKIP'})
                seen.add(key)
    evaluated=[r for r in records if r.get('status')=='EVALUATED']
    misses=sum(r.get('status')=='MISSED' for r in records)
    covered=sum(r.get('in_range') is True for r in evaluated)
    return {'updated_at':now.isoformat(),'source':'CoinGecko stored market snapshots',
            'method':'Forward-only illustrative ranges; not calibrated probability forecasts',
            'records':records,'summary':{'issued':len(records),
            'pending':sum(r.get('status')=='PENDING' for r in records),
            'evaluated':len(evaluated),'inside_range':covered,'missed':misses,
            'coverage_of_evaluated_pct':round(100*covered/len(evaluated),1) if evaluated else None},
            'note':'Outcomes are evaluated only at 30/90-day maturity using fresh data. No trades.'}

def main():
    now=datetime.now(timezone.utc)
    snapshot=json.loads(PRICES.read_text(encoding='utf-8'))
    if AUDIT.exists():
        archive=json.loads(AUDIT.read_text(encoding='utf-8'))
    else:archive={'records':[]}
    result=process(snapshot,archive,now)
    DATA.mkdir(exist_ok=True)
    AUDIT.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    print('Scenario audit: ',result['summary'])

if __name__=='__main__':main()
