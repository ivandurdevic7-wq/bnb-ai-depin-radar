#!/usr/bin/env python3
"""BNB token discovery: public DexScreener + optional GoPlus risk checks.
Discovery is not investment validation; every candidate is SKIP.
"""
import json, re, sys, time, math
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'data' / 'candidates.json'
UA = {'Accept':'application/json', 'User-Agent':'BNB-AI-DePIN-Radar/3.0'}
now = datetime.now(timezone.utc)
keywords = re.compile(r'(?i)(?:\bai\b|agent|depin|compute|robot|inference|neural|gpu|data network|artificial intelligence|machine learning)')
def get(url):
    with urlopen(Request(url,headers=UA),timeout=17) as r:
        return json.load(r)
def norm(address):
    if isinstance(address,str) and re.fullmatch(r'0x[0-9a-fA-F]{40}',address):
        return address.lower()
    return None

try:
    profiles = get('https://api.dexscreener.com/token-profiles/latest/v1')
    if not isinstance(profiles,list):
        raise ValueError('Unexpected profile format')
except Exception as exc:
    print('Token discovery unavailable; previous snapshot preserved:',exc,file=sys.stderr)
    sys.exit(1)

selected=[]
seen=set()
for p in profiles:
    if not isinstance(p,dict) or p.get('chainId') != 'bsc':continue
    address=norm(p.get('tokenAddress'))
    if address is None or address in seen:continue
    seen.add(address)
    selected.append((address,p))
    if len(selected)>=40:break
if not selected:
    # Latest profile feed often contains only other chains. Search is a secondary
    # discovery source, never a substitute for verifying the actual BNB contract.
    for term in ('AI','DePIN','agent'):
        try:
            response=get('https://api.dexscreener.com/latest/dex/search?q='+quote(term))
            for pair in response.get('pairs',[]):
                if not isinstance(pair,dict) or pair.get('chainId')!='bsc':continue
                base=pair.get('baseToken') or {}
                address=norm(base.get('address'))
                if not address or address in seen:continue
                if not keywords.search(str(base.get('name',''))+' '+str(base.get('symbol',''))):continue
                selected.append((address,{'description':term,'chainId':'bsc','tokenAddress':address}))
                seen.add(address)
                if len(selected)>=25:break
            if len(selected)>=25:break
        except Exception as exc:
            print('Search fallback unavailable:',type(exc).__name__,file=sys.stderr)
if not selected:
    result={'updated_at':now.isoformat(),'source':'DexScreener profiles and search',
            'screened_bsc_profiles':0,'candidates':[], 'coverage':'No BNB results from available public feeds',
            'note':'No new candidates found. Do not interpret as a complete absence of AI tokens. All trades remain SKIP.'}
    DEST.parent.mkdir(parents=True,exist_ok=True)
    DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('No BNB candidates in public feeds; recorded empty, explicitly unverified snapshot')
    sys.exit(0)

out=[]
for address,profile in selected:
    try:
        pairs=get('https://api.dexscreener.com/token-pairs/v1/bsc/'+address)
        if not isinstance(pairs,list):continue
        eligible=[p for p in pairs if isinstance(p,dict) and p.get('chainId')=='bsc'
                  and norm((p.get('baseToken') or {}).get('address'))==address]
        if not eligible:continue
        pair=max(eligible,key=lambda p:float((p.get('liquidity') or {}).get('usd') or 0))
        name=(pair.get('baseToken') or {}).get('name','')
        symbol=(pair.get('baseToken') or {}).get('symbol','')
        about=str(profile.get('description') or '')
        if not keywords.search(str(name)+' '+str(symbol)+' '+about):
            continue
        liquidity=float((pair.get('liquidity') or {}).get('usd') or 0)
        volume=float((pair.get('volume') or {}).get('h24') or 0)
        if not math.isfinite(liquidity) or not math.isfinite(volume): continue
        cap=pair.get('marketCap')
        cap=float(cap) if isinstance(cap,(int,float)) and math.isfinite(cap) else None
        created=pair.get('pairCreatedAt')
        age_days=round((now.timestamp()*1000-created)/86400000,1) if isinstance(created,(int,float)) else None
        if liquidity<10000 or volume<1000 or (cap is not None and cap>5000000) or (age_days is not None and (age_days<0 or age_days>90)):
            continue
        risk={'source':'GoPlus', 'checked':False, 'flags':[], 'status':'NOT_CHECKED'}
        try:
            sec=get('https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses='+quote(address))
            data=(sec.get('result') or {}).get(address) or (sec.get('result') or {}).get(address.lower())
            if isinstance(data,dict):
                flags=[]
                for key in ('is_honeypot','is_blacklisted','is_mintable','can_take_back_ownership','hidden_owner','owner_change_balance','transfer_pausable','is_proxy'):
                    if str(data.get(key,''))=='1':flags.append(key)
                for key in ('buy_tax','sell_tax'):
                    value=data.get(key)
                    if value is not None:
                        try:
                            if float(value)>.1:flags.append(key+'>10%')
                        except (ValueError,TypeError):pass
                required=('is_honeypot','is_mintable','hidden_owner','is_open_source','buy_tax','sell_tax')
                missing=[k for k in required if data.get(k) in (None,'')]
                if str(data.get('is_open_source',''))=='0':flags.append('closed_source')
                risk={'source':'GoPlus','checked':not bool(missing),'missing_fields':missing,
                      'flags':flags,'status':'FLAGS' if flags else ('PARTIAL_CHECK' if missing else 'CHECKED_FIELDS_ONLY')}
        except Exception as exc:
            risk['error']=type(exc).__name__
        out.append({'name':name,'symbol':symbol,'chain':'bsc','contract':address,'pair':pair.get('pairAddress'),
                    'dex_url':pair.get('url'),'liquidity_usd':liquidity,'volume_24h_usd':volume,
                    'market_cap_usd':cap,'pair_age_days':age_days,'discovered_at':now.isoformat(),
                    'security':risk,'project_verified':False,'lp_lock_verified':False,
                    'holders_verified':False,'sellability_verified':False,'unlock_verified':False,
                    'decision':'SKIP','reason':'Incomplete independent security/product verification'})
    except Exception as exc:
        print('Skipped pair',address,type(exc).__name__,file=sys.stderr)
    time.sleep(.12)
out.sort(key=lambda t:(t['security']['checked'], t['liquidity_usd']),reverse=True)
result={'updated_at':now.isoformat(),'source':'DexScreener public token profiles + pool data; optional GoPlus',
        'screened_bsc_profiles':len(selected),'candidates':out[:20],
        'coverage':'Recent publicly listed DexScreener profiles only; not an exhaustive BNB listing',
        'note':'All candidates are unverified and SKIP. Screening can miss projects; no complete audit.'}
DEST.parent.mkdir(parents=True,exist_ok=True)
DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Scanned',len(selected),'BNB profiles, found',len(out),'unverified AI-ish candidates')
