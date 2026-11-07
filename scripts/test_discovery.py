#!/usr/bin/env python3
"""Offline regression tests; never call live APIs or modify candidate snapshots."""
import io
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

SCRIPT=Path(__file__).resolve().with_name('discover_candidates.py')
ADDR='0x'+'a'*40
PAIR='0x'+'b'*40
PROFILE={'chainId':'bsc','tokenAddress':ADDR,'description':'An AI compute network'}
PAIR_RESPONSE=[{'chainId':'bsc','baseToken':{'address':ADDR,'name':'Demo AI Compute','symbol':'DEMO'},
 'liquidity':{'usd':25000},'volume':{'h24':5000},'marketCap':125000,'pairCreatedAt':None,
 'pairAddress':PAIR,'url':'https://dexscreener.com/bsc/'+PAIR}]
SECURITY={'result':{ADDR:{'is_honeypot':'1','is_mintable':'0','hidden_owner':'0',
 'is_open_source':'1','buy_tax':'0','sell_tax':'0'}}}

class FakeResponse(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class DiscoveryTests(unittest.TestCase):
    def simulate(self, replies):
        saved=[]
        def fake_urlopen(req,timeout=0):
            url=req.full_url
            for needle,response in replies.items():
                if needle in url:
                    if isinstance(response,Exception):raise response
                    return FakeResponse(json.dumps(response).encode())
            raise AssertionError('Unexpected endpoint: '+url)
        with patch('urllib.request.urlopen',side_effect=fake_urlopen), patch('time.sleep'),\
             patch.object(Path,'write_text',autospec=True,side_effect=lambda path,data,**kw:saved.append(json.loads(data))):
            try:runpy.run_path(str(SCRIPT),run_name='__main__')
            except SystemExit as e:return saved,e.code
        return saved,0

    def test_gecko_new_pool_discovery_requires_bsc_contract_and_dex_pair(self):
        newcomer='0x'+'c'*40
        newpair=json.loads(json.dumps(PAIR_RESPONSE))
        newpair[0]['baseToken']={'address':newcomer,'name':'New GameFi Arena','symbol':'GAME'}
        newpair[0]['pairAddress']='0x'+'d'*40
        gecko={'data':[{'relationships':{'base_token':{'data':{'id':'bsc_'+newcomer,'type':'token'}}}}],
               'included':[{'id':'bsc_'+newcomer,'type':'token','attributes':{
                   'address':newcomer,'name':'New GameFi Arena','symbol':'GAME'}}]}
        responses={'token-profiles/latest':[],
                   'latest/dex/search':{'pairs':[]},
                   '/new_pools?include=base_token':gecko,
                   '/trending_pools?include=base_token':{'data':[],'included':[]},
                   'token-pairs/v1/bsc/'+newcomer:newpair,
                   'token_security/56':SECURITY,
                   '/tokens/'+newcomer+'/info':URLError('not indexed')}
        saved,code=self.simulate(responses)
        self.assertEqual(code,0)
        self.assertEqual(len(saved[0]['candidates']),1)
        self.assertEqual(saved[0]['candidates'][0]['sectors'],['Gaming'])
        self.assertEqual(saved[0]['candidates'][0]['decision'],'SKIP')
        self.assertEqual(saved[0]['geckoterminal_discovery_leads'],1)

    def test_gecko_pool_with_wrong_chain_identity_is_not_discovered(self):
        address='0x'+'c'*40
        bad={'data':[{'relationships':{'base_token':{'data':{'id':'eth_'+address,'type':'token'}}}}],
             'included':[{'id':'eth_'+address,'type':'token','attributes':{
                 'address':address,'name':'New Meme','symbol':'MEME'}}]}
        saved,code=self.simulate({'token-profiles/latest':[],
           'latest/dex/search':{'pairs':[]},
           '/new_pools?include=base_token':bad,
           '/trending_pools?include=base_token':{'data':[],'included':[]}})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])

    def test_promotional_boost_is_only_a_lead_and_stays_skip(self):
        boosts=[{'chainId':'bsc','tokenAddress':ADDR,'description':'AI token promotion'}]
        saved,code=self.simulate({
            'token-profiles/latest':[],
            'latest/dex/search':{'pairs':[]},
            'token-boosts/':boosts,
            '/new_pools?include=base_token':{'data':[],'included':[]},
            '/trending_pools?include=base_token':{'data':[],'included':[]},
            'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':SECURITY,
            'geckoterminal.com/api/v2/networks/bsc/tokens/':URLError('offline')})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['dex_boost_leads'],1)
        self.assertEqual(len(saved[0]['candidates']),1)
        self.assertEqual(saved[0]['candidates'][0]['decision'],'SKIP')
        self.assertFalse(saved[0]['candidates'][0]['project_verified'])

    def test_profile_api_failure_preserves_previous_data(self):
        saved,code=self.simulate({'token-profiles/latest':URLError('offline')})
        self.assertNotEqual(code,0)
        self.assertEqual(saved,[])

    def test_wrong_chain_records_empty_skip_snapshot(self):
        saved,code=self.simulate({'token-profiles/latest':[{'chainId':'ethereum','tokenAddress':ADDR}],
             'latest/dex/search':{'pairs':[]}})
        self.assertEqual(code,0)
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['candidates'],[])
        self.assertEqual(saved[0]['screened_bsc_profiles'],0)

    def test_search_fallback_bnb_candidate_is_skip(self):
        saved,code=self.simulate({'token-profiles/latest':[],
             'latest/dex/search':{'pairs':PAIR_RESPONSE},
             'token-pairs/v1/bsc/':PAIR_RESPONSE,
             'token_security/56':SECURITY})
        self.assertEqual(code,0)
        self.assertEqual(len(saved[0]['candidates']),1)
        self.assertEqual(saved[0]['candidates'][0]['decision'],'SKIP')

    def test_flagged_token_always_skip(self):
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
                    'token-pairs/v1/bsc/':PAIR_RESPONSE,'token_security/56':SECURITY})
        self.assertEqual(code,0)
        self.assertEqual(len(saved),1)
        candidates=saved[0]['candidates']
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['decision'],'SKIP')
        self.assertIn('is_honeypot',candidates[0]['security']['flags'])

    def test_security_api_down_still_skip(self):
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
                    'token-pairs/v1/bsc/':PAIR_RESPONSE,'token_security/56':URLError('offline')})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'][0]['decision'],'SKIP')
        self.assertFalse(saved[0]['candidates'][0]['security']['checked'])

    def test_mismatched_base_contract_is_rejected(self):
        wrong=json.loads(json.dumps(PAIR_RESPONSE))
        wrong[0]['baseToken']['address']='0x'+'f'*40
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':wrong})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])

    def test_geckoterminal_matching_index_is_not_project_approval(self):
        secondary={'data':{'id':'bsc_'+ADDR,'attributes':{
            'name':'Demo AI Compute','symbol':'DEMO'}}}
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':SECURITY,'geckoterminal.com':secondary})
        self.assertEqual(code,0)
        candidate=saved[0]['candidates'][0]
        self.assertEqual(candidate['secondary_source']['status'],'MATCH')
        self.assertFalse(candidate['secondary_source']['project_verified'])
        self.assertEqual(candidate['decision'],'SKIP')

    def test_geckoterminal_mismatched_identity_remains_skip(self):
        secondary={'data':{'id':'bsc_'+ADDR,'attributes':{
            'name':'Impersonated Token','symbol':'FAKE'}}}
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':SECURITY,'geckoterminal.com':secondary})
        self.assertEqual(code,0)
        candidate=saved[0]['candidates'][0]
        self.assertEqual(candidate['secondary_source']['status'],'MISMATCH')
        self.assertEqual(candidate['decision'],'SKIP')

    def test_secondary_provider_outage_is_unknown_not_safe(self):
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':SECURITY,
            'geckoterminal.com':URLError('offline')})
        self.assertEqual(code,0)
        candidate=saved[0]['candidates'][0]
        self.assertEqual(candidate['secondary_source']['status'],'UNAVAILABLE')
        self.assertFalse(candidate['secondary_source']['checked'])
        self.assertEqual(candidate['decision'],'SKIP')

    def test_missing_pool_address_is_rejected(self):
        invalid=json.loads(json.dumps(PAIR_RESPONSE))
        invalid[0]['pairAddress']=None
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':invalid})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])

    def test_sector_label_requires_token_name_or_symbol(self):
        disguised=json.loads(json.dumps(PAIR_RESPONSE))
        disguised[0]['baseToken']['name']='Unrelated Asset'
        disguised[0]['baseToken']['symbol']='UA'
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':disguised})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])

    def test_identity_evidence_is_not_independent_verification(self):
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':SECURITY})
        self.assertEqual(code,0)
        token=saved[0]['candidates'][0]
        self.assertTrue(token['identity_evidence']['base_address_matches_contract'])
        self.assertFalse(token['identity_evidence']['independent_project_identity_verified'])
        self.assertEqual(token['decision'],'SKIP')

    def test_low_liquidity_is_excluded(self):
        bad=json.loads(json.dumps(PAIR_RESPONSE))
        bad[0]['liquidity']['usd']=200
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
                                   'token-pairs/v1/bsc/':bad})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])


    def test_search_also_runs_when_profiles_exist(self):
        other='0x'+'c'*40
        extra=json.loads(json.dumps(PAIR_RESPONSE))
        extra[0]['baseToken']['address']=other
        extra[0]['baseToken']['name']='DePIN Compute'
        extra[0]['baseToken']['symbol']='DEPIN'
        responses={'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':extra},
            'token-pairs/v1/bsc/'+other:extra,
            'token-pairs/v1/bsc/'+ADDR:PAIR_RESPONSE,
            'token_security/56':SECURITY}
        saved,code=self.simulate(responses)
        self.assertEqual(code,0)
        self.assertEqual(len(saved[0]['candidates']),2)
        self.assertEqual(set(v['contract'] for v in saved[0]['candidates']),{ADDR,other})

    def test_cannot_sell_all_high_risk(self):
        sec=json.loads(json.dumps(SECURITY))
        sec['result'][ADDR]['is_honeypot']='0'
        sec['result'][ADDR]['cannot_sell_all']='1'
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},
            'token-pairs/v1/bsc/':PAIR_RESPONSE,
            'token_security/56':sec})
        self.assertEqual(code,0)
        item=saved[0]['candidates'][0]
        self.assertEqual(item['decision'],'SKIP')
        self.assertIn('cannot_sell_all',item['security']['flags'])
        self.assertEqual(item['security']['sellability'],'HIGH_RISK')


    def test_top_wallet_concentration_requires_ten_valid_wallets(self):
        sec=json.loads(json.dumps(SECURITY))
        sec['result'][ADDR]['is_honeypot']='0'
        sec['result'][ADDR]['holders']=[{'percent':'0.07'} for _ in range(10)]
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},
            'token-pairs/v1/bsc/':PAIR_RESPONSE,'token_security/56':sec})
        self.assertEqual(code,0)
        token=saved[0]['candidates'][0]
        self.assertEqual(token['security']['top10_reported_wallets_pct'],70)
        self.assertIn('top10_wallets_over_60pct',token['security']['flags'])
        self.assertFalse(token['holders_verified'])
        self.assertEqual(token['decision'],'SKIP')

    def test_incomplete_holders_remain_unknown(self):
        sec=json.loads(json.dumps(SECURITY))
        sec['result'][ADDR]['holders']=[{'percent':'0.99'}]
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
            'latest/dex/search':{'pairs':[]},
            'token-pairs/v1/bsc/':PAIR_RESPONSE,'token_security/56':sec})
        self.assertEqual(code,0)
        token=saved[0]['candidates'][0]
        self.assertIsNone(token['security']['top10_reported_wallets_pct'])
        self.assertNotIn('top10_wallets_over_60pct',token['security']['flags'])
        self.assertFalse(token['lp_lock_verified'])
        self.assertEqual(token['decision'],'SKIP')

if __name__=='__main__':unittest.main()
