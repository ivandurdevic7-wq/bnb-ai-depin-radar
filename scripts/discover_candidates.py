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
# Candidate sectors are thematic labels inferred from public pair names, NOT verified business classifications.
SECTOR_RULES = {
    'AI / DePIN': re.compile(r'(?i)(?:\bai\b|agent|depin|compute|robot|inference|neural|\bgpu\b|artificial intelligence|machine learning|\biot\b|sensor)'),
    'DeFi': re.compile(r'(?i)(?:\bdefi\b|\bdex\b|swap|staking|liquid staking|yield|lending|borrow|liquidity protocol)'),
    'RWA': re.compile(r'(?i)(?:\brwa\b|tokenized real|real.world asset|tokenized treasury|asset tokenization)'),
    'Gaming': re.compile(r'(?i)(?:\bgamefi\b|\bgaming\b|\bgame\b|play.to.earn|\bp2e\b|\bguild\b)'),
    'Infrastructure': re.compile(r'(?i)(?:\boracle\b|cross.chain|interoperab|\bbridge\b|\brollup\b|\blayer.?2\b|\bindexer\b)'),
    'Meme': re.compile(r'(?i)(?:\bmeme\b|\bmemecoin\b|\bdoge\b|\bpepe\b|\bshib\b|\binu\b)'),
}
def sectors_for(text):
    return [name for name,pattern in SECTOR_RULES.items() if pattern.search(str(text))]
keywords = re.compile('|'.join('(?:'+r.pattern.replace('(?i)','')+')' for r in SECTOR_RULES.values()), re.I)

def get(url):
    with urlopen(Request(url,headers=UA),timeout=17) as r:
        return json.load(r)
def norm(address):
    if isinstance(address,str) and re.fullmatch(r'0x[0-9a-fA-F]{40}',address):
        return address.lower()
    return None


def recent_successes(candidate_count, screened):
    # Only successful snapshots are recorded. Missing runs are not evidence of uptime.
    try:
        previous=json.loads(DEST.read_text(encoding='utf-8'))
        history=previous.get('successful_scans',[])
        if not isinstance(history,list):history=[]
    except (OSError,ValueError,TypeError):
        history=[]
    valid=[row for row in history if isinstance(row,dict) and
           isinstance(row.get('at'),str) and isinstance(row.get('candidates'),int)]
    valid.append({'at':now.isoformat(),'candidates':candidate_count,'screened':screened})
    return valid[-60:]

try:
    profiles = get('https://api.dexscreener.com/token-profiles/latest/v1')
    if not isinstance(profiles,list):
        raise ValueError('Unexpected profile format')
except Exception as exc:
    print('Token discovery unavailable; previous snapshot preserved:',exc,file=sys.stderr)
    sys.exit(1)

# Balanced thematic queries. Category must still match the reported BNB pair token name (not independently verified).
SECTOR_SEARCHES = {
    'AI / DePIN': ('AI','DePIN','agent','compute','inference'),
    'DeFi': ('DeFi','swap','staking','lending','yield'),
    'RWA': ('RWA','tokenized','treasury'),
    'Gaming': ('GameFi','gaming','play to earn'),
    'Infrastructure': ('oracle','interoperability','bridge'),
    'Meme': ('meme','doge','pepe'),
}
search_terms=tuple(term for group in SECTOR_SEARCHES.values() for term in group)
MAX_SELECTED=96
MAX_PER_SECTOR=16
selected=[]
seen=set()
sector_counts={name:0 for name in SECTOR_RULES}
search_errors=[]
search_checks={name:0 for name in SECTOR_RULES}
def add_lead(address,meta,sector_labels):
    if address is None or address in seen or len(selected)>=MAX_SELECTED or not sector_labels:
        return False
    # Prevent the busiest sector from using all available pair-check requests.
    allowed=[label for label in sector_labels if sector_counts[label]<MAX_PER_SECTOR]
    if not allowed:return False
    seen.add(address)
    selected.append((address,meta))
    for label in allowed:sector_counts[label]+=1
    return True

for p in profiles:
    if not isinstance(p,dict) or p.get('chainId')!='bsc':continue
    address=norm(p.get('tokenAddress'))
    # Only use actual token name or symbol for category claims, not marketing text.
    labels=sectors_for(str(p.get('tokenName',''))+' '+str(p.get('tokenSymbol',''))+' '+str(p.get('description','')))
    # Description may seed a lead but never determines the published sector label.
    if labels:add_lead(address,p,labels)

# Run every sector's searches, even if another sector has more leads.
# A search result is not a verified project; confirm pair address, chain and name later.
for group,terms in SECTOR_SEARCHES.items():
    for term in terms:
        try:
            response=get('https://api.dexscreener.com/latest/dex/search?q='+quote(term))
            search_checks[group]+=1
            for pair in response.get('pairs',[]):
                if not isinstance(pair,dict) or pair.get('chainId')!='bsc':continue
                base=pair.get('baseToken') or {}
                address=norm(base.get('address'))
                labels=sectors_for(str(base.get('name',''))+' '+str(base.get('symbol','')))
                add_lead(address,{'description':term,'chainId':'bsc','tokenAddress':address},labels)
        except Exception as exc:
            search_errors.append(term+': '+type(exc).__name__)
# Extra DexScreener stream: boosted lists are PROMOTIONAL and carry no safety endorsement.
# Their value is additional contract leads; never treat boost activity as organic quality.
boost_leads=0
boost_errors=[]
for kind in ('latest','top'):
    try:
        feed=get('https://api.dexscreener.com/token-boosts/'+kind+'/v1')
        if not isinstance(feed,list):raise ValueError('Invalid boosted-token feed')
        for record in feed:
            if not isinstance(record,dict) or record.get('chainId')!='bsc':continue
            address=norm(record.get('tokenAddress'))
            labels=sectors_for(str(record.get('description') or ''))
            if add_lead(address,{'chainId':'bsc','tokenAddress':address,
                                 'description':record.get('description',''),
                                 'discovery_source':'DexScreener boosted/promotional'},labels):
                boost_leads+=1
    except Exception as exc:
        boost_errors.append(kind+': '+type(exc).__name__)

# Independent discovery stream: GeckoTerminal BSC new and trending pools.
# Pool listings are only leads, not identity/security validation.
gecko_leads=0
gecko_errors=[]
for kind in ('new_pools','trending_pools'):
    try:
        response=get('https://api.geckoterminal.com/api/v2/networks/bsc/'+kind+'?include=base_token')
        included={}
        for record in response.get('included',[]) if isinstance(response,dict) else []:
            if isinstance(record,dict) and record.get('type')=='token':
                included[record.get('id')]=record.get('attributes') or {}
        for pool in response.get('data',[]) if isinstance(response,dict) else []:
            if not isinstance(pool,dict):continue
            relationship=((pool.get('relationships') or {}).get('base_token') or {}).get('data') or {}
            rel_id=relationship.get('id')
            if not isinstance(rel_id,str) or not rel_id.lower().startswith('bsc_'):
                continue
            token=included.get(rel_id,{})
            address=norm(token.get('address'))
            if not address and isinstance(relationship.get('id'),str) and relationship['id'].lower().startswith('bsc_'):
                address=norm(relationship['id'][4:])
            name=token.get('name','')
            symbol=token.get('symbol','')
            labels=sectors_for(str(name)+' '+str(symbol))
            if add_lead(address,{'description':kind,'chainId':'bsc','tokenAddress':address,'discovery_source':'GeckoTerminal'},labels):
                gecko_leads+=1
    except Exception as exc:
        gecko_errors.append(kind+': '+type(exc).__name__)
    time.sleep(6.5)

if not selected:
    result={'updated_at':now.isoformat(),'source':'DexScreener searches and GeckoTerminal BSC pools',
            'screened_bsc_profiles':0,'candidates':[], 'successful_scans':recent_successes(0,0), 'coverage':'No BNB results from available public feeds',
            'search_errors':search_errors,'search_checks_by_sector':search_checks,
            'geckoterminal_discovery_leads':gecko_leads,'geckoterminal_discovery_errors':gecko_errors,
            'dex_boost_leads':boost_leads,'dex_boost_errors':boost_errors,
            'note':'No candidates found from sampled public feeds; not a complete market census. SKIP.'}
    DEST.parent.mkdir(parents=True,exist_ok=True)
    DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('No BNB candidates from sampled feeds; wrote no-results report')
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
        pair_address=norm(pair.get('pairAddress'))
        if not (isinstance(name,str) and name.strip() and isinstance(symbol,str) and symbol.strip() and pair_address):
            continue  # Unclear pool/token identity: reject rather than invent it.
        sectors=sectors_for(name+' '+symbol)
        if not sectors:
            continue  # Search term or promotional profile text alone cannot assert sector identity
        liquidity=float((pair.get('liquidity') or {}).get('usd') or 0)
        volume=float((pair.get('volume') or {}).get('h24') or 0)
        if not math.isfinite(liquidity) or not math.isfinite(volume): continue
        cap=pair.get('marketCap')
        cap=float(cap) if isinstance(cap,(int,float)) and math.isfinite(cap) else None
        created=pair.get('pairCreatedAt')
        age_days=round((now.timestamp()*1000-created)/86400000,1) if isinstance(created,(int,float)) else None
        if liquidity<10000 or volume<1000 or (cap is not None and cap>5000000) or (age_days is not None and (age_days<0 or age_days>90)):
            continue
        risk={'source':'GoPlus', 'checked':False, 'flags':[], 'status':'NOT_CHECKED', 'sellability':'UNVERIFIED'}
        try:
            sec=get('https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses='+quote(address))
            data=(sec.get('result') or {}).get(address) or (sec.get('result') or {}).get(address.lower())
            if isinstance(data,dict):
                flags=[]
                for key in ('is_honeypot','is_blacklisted','is_mintable','can_take_back_ownership','hidden_owner','owner_change_balance','transfer_pausable','is_proxy','cannot_sell_all','is_anti_whale','trading_cooldown'):
                    if str(data.get(key,''))=='1':flags.append(key)
                for key in ('buy_tax','sell_tax'):
                    value=data.get(key)
                    if value is not None:
                        try:
                            if float(value)>.1:flags.append(key+'>10%')
                        except (ValueError,TypeError):pass
                required=('is_honeypot','is_mintable','hidden_owner','is_open_source','buy_tax','sell_tax','cannot_sell_all')
                missing=[k for k in required if data.get(k) in (None,'')]
                if str(data.get('is_open_source',''))=='0':flags.append('closed_source')
                # GoPlus holder entries are partial snapshots; do not confuse top wallets
                # with beneficial ownership, and do not claim an LP lock from LP holders.
                holders=data.get('holders')
                portions=[]
                if isinstance(holders,list):
                    for holder in holders[:10]:
                        if not isinstance(holder,dict):continue
                        try:
                            value=float(holder.get('percent'))
                            if math.isfinite(value) and 0<=value<=1:portions.append(value)
                        except (TypeError,ValueError):continue
                top10=round(sum(portions)*100,2) if len(portions)==10 else None
                if top10 is not None and top10>60:flags.append('top10_wallets_over_60pct')
                sellability='HIGH_RISK' if 'is_honeypot' in flags or 'cannot_sell_all' in flags else 'UNVERIFIED'
                risk={'source':'GoPlus','checked':not bool(missing),'missing_fields':missing,
                      'flags':flags,'sellability':sellability,
                      'top10_reported_wallets_pct':top10,
                      'top10_is_beneficial_ownership':False,
                      'lp_lock_status':'UNVERIFIED',
                      'status':'FLAGS' if flags else ('PARTIAL_CHECK' if missing else 'CHECKED_FIELDS_ONLY')}
        except Exception as exc:
            risk['error']=type(exc).__name__
        out.append({'name':name,'symbol':symbol,'chain':'bsc','contract':address,'pair':pair_address, 'identity_evidence':{'source':'DexScreener pair','chain':'bsc','base_address_matches_contract':True,'pair_address_present':True,'independent_project_identity_verified':False},
                    'dex_url':pair.get('url'),'liquidity_usd':liquidity,'volume_24h_usd':volume,
                    'market_cap_usd':cap,'pair_age_days':age_days,'discovered_at':now.isoformat(),
                    'sectors':sectors,'sector_classification':'KEYWORD_HEURISTIC_UNVERIFIED',
                    'discovery_source':profile.get('discovery_source','DexScreener search/profile'),
                    'liquidity_to_market_cap':round(liquidity/cap,4) if cap and cap>0 else None,
                    'volume_to_liquidity':round(volume/liquidity,4) if liquidity>0 else None,
                    'security':risk,'project_verified':False,'lp_lock_verified':False,
                    'holders_verified':False,'sellability_verified':False,'unlock_verified':False,
                    'decision':'SKIP','reason':'Incomplete independent security/product verification'})
    except Exception as exc:
        print('Skipped pair',address,type(exc).__name__,file=sys.stderr)
    time.sleep(.12)
out.sort(key=lambda t:(t['security']['checked'], t['liquidity_usd']),reverse=True)
# Secondary provider check is informational only, not a verification of the project.
# Rate-limit public GeckoTerminal lookups; not all candidates can be checked per scan.
def gecko_identity(token):
    address=token['contract']
    result={'provider':'GeckoTerminal','status':'UNAVAILABLE','checked':False,
            'address_match':False,'name_match':False,'symbol_match':False,
            'project_verified':False}
    try:
        data=get('https://api.geckoterminal.com/api/v2/networks/bsc/tokens/'+address+'/info')
        entry=data.get('data') if isinstance(data,dict) else None
        attrs=entry.get('attributes') if isinstance(entry,dict) else None
        if not isinstance(attrs,dict):
            result['status']='NO_DATA'
            return result
        remote_address=norm(attrs.get('address'))
        if remote_address is None:
            token_id=entry.get('id','')
            if isinstance(token_id,str) and token_id.lower().startswith('bsc_'):
                remote_address=norm(token_id[4:])
        result['checked']=True
        result['address_match']=remote_address==address
        result['name_match']=str(attrs.get('name') or '').casefold().strip()==token['name'].casefold().strip()
        result['symbol_match']=str(attrs.get('symbol') or '').casefold().strip()==token['symbol'].casefold().strip()
        result['status']='MATCH' if all((result['address_match'],result['name_match'],result['symbol_match'])) else 'MISMATCH'
        # Indexing or a name match is not proof that a project is legitimate.
        return result
    except Exception as exc:
        result['error']=type(exc).__name__
        return result

for token in out:
    token['secondary_source']={'provider':'GeckoTerminal','status':'NOT_CHECKED',
        'checked':False,'project_verified':False}
# Check a small limited sample. GeckoTerminal public API is rate-limited.
for token in out[:6]:
    token['secondary_source']=gecko_identity(token)
    time.sleep(6.5)

sector_results={sector:sum(sector in t['sectors'] for t in out) for sector in SECTOR_RULES}
source_yield={source:sum(t['discovery_source']==source for t in out) for source in ('DexScreener search/profile','DexScreener boosted/promotional','GeckoTerminal')}

result={'updated_at':now.isoformat(),'source':'DexScreener searches and pair data; GeckoTerminal new/trending BSC pools and limited identity cross-check; optional GoPlus',
        'screened_bsc_profiles':len(selected),'candidates':out[:60], 'search_terms':list(search_terms),
        'successful_scans':recent_successes(min(len(out),60),len(selected)),
        'search_errors':search_errors,'search_checks_by_sector':search_checks,
        'sector_results_before_display_limit':sector_results,'source_yield':source_yield,
        'dex_boost_leads':boost_leads,'dex_boost_errors':boost_errors,
        'geckoterminal_discovery_leads':gecko_leads,
        'geckoterminal_discovery_errors':gecko_errors,
        'coverage':'DexScreener thematic searches and promotional boosts plus GeckoTerminal BSC new/trending pools (may be unavailable). Pair data checked on DexScreener; not exhaustive',
        'note':'All candidates are unverified and SKIP. Boosts are paid promotion, not endorsement. GeckoTerminal indexing does not confirm project legitimacy. Screening can miss projects; no complete audit.'}
DEST.parent.mkdir(parents=True,exist_ok=True)
DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Scanned',len(selected),'BNB profiles, found',len(out),'unverified multi-sector candidates')
