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

    def test_low_liquidity_is_excluded(self):
        bad=json.loads(json.dumps(PAIR_RESPONSE))
        bad[0]['liquidity']['usd']=200
        saved,code=self.simulate({'token-profiles/latest':[PROFILE],
                                   'token-pairs/v1/bsc/':bad})
        self.assertEqual(code,0)
        self.assertEqual(saved[0]['candidates'],[])

if __name__=='__main__':unittest.main()
