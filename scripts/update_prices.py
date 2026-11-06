#!/usr/bin/env python3
"""Spremanje tržišnih snimki. Ne izvršava trgovanje."""
import json,os,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parents[1]
coins=json.loads((ROOT/'data/coins.json').read_text(encoding='utf-8'))
ids=[c['id'] for c in coins]
api='https://api.coingecko.com/api/v3/coins/markets?'+urlencode(dict(vs_currency='usd',ids=','.join(ids),order='market_cap_desc',per_page='100',page='1',sparkline='false',price_change_percentage='7d'))
key=os.getenv('COINGECKO_DEMO_API_KEY','')
headers={'Accept':'application/json','User-Agent':'BNB-AI-DePIN-Radar/2.0'}
if key:headers['x-cg-demo-api-key']=key
try:
 with urlopen(Request(api,headers=headers),timeout=35) as f: data=json.load(f)
except (HTTPError,URLError,TimeoutError,ValueError) as e:
 print('API nije dostupan; prethodni podaci ostaju sacuvani:',str(e),file=sys.stderr);sys.exit(1)
byid={r.get('id'):r for r in data if isinstance(r,dict)}
found=[];missing=[]
for c in coins:
 r=byid.get(c['id'])
 if not isinstance(r,dict) or str(r.get('symbol','')).lower()!=c['symbol'].lower():
  missing.append(c['symbol']);continue
 found.append(r)
if len(found)<3:
 print('Premalo potvrdenih tokena, nema prepisivanja povijesti',file=sys.stderr);sys.exit(2)
now=datetime.now(timezone.utc);root=ROOT/'data';root.mkdir(exist_ok=True)
snapshot={'updated_at':now.isoformat(),'source':'CoinGecko','coins':found,'errors':missing}
(root/'prices.json').write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
path=root/'history.json'
try:hist=json.loads(path.read_text(encoding='utf-8'))
except (OSError,ValueError):hist={}
cutoff=(now-timedelta(days=91)).timestamp()*1000
for r in found:
 p=r.get('current_price');ident=r.get('id')
 if not isinstance(p,(int,float)) or p<=0:continue
 arr=[v for v in hist.get(ident,[]) if isinstance(v,list) and len(v)==2 and isinstance(v[0],(int,float)) and v[0]>=cutoff]
 arr.append([int(now.timestamp()*1000),p]);hist[ident]=arr[-2200:]
path.write_text(json.dumps(hist,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
print('Updated',len(found),'tokens; missing:',','.join(missing))
