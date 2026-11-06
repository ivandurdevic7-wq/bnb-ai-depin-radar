#!/usr/bin/env python3
"""Offline checks: no network, no financial decisions."""
import json,re,sys
from pathlib import Path
from html.parser import HTMLParser
root=Path(__file__).resolve().parents[1]
class Parser(HTMLParser):
    pass
html=(root/'index.html').read_text(encoding='utf-8')
assert html.count('</html>')==1, 'Duplicate or trailing HTML'
assert html.count('<script>')==html.count('</script>')==2, 'Broken script tags'
assert html.rstrip().endswith('</html>'), 'Data after HTML closure'
assert 'id="radar2"' in html
assert 'NEPROVJERENO' in html and 'SKIP' in html
coins=json.loads((root/'data/coins.json').read_text(encoding='utf-8'))
assert len(coins)>=20 and len({c['id'] for c in coins})==len(coins)
for c in coins:
    assert c['status'] in ('SKIP','OPREZ','PRATITI')
    assert all(c.get(k) for k in ('id','name','symbol'))
discovery=json.loads((root/'data/candidates.json').read_text(encoding='utf-8'))
assert isinstance(discovery.get('candidates'),list)
for c in discovery['candidates']:
    assert c.get('decision')=='SKIP', 'Candidate cannot be approved automatically'
    assert re.fullmatch(r'0x[0-9a-f]{40}',c.get('contract',''))
    assert c.get('chain')=='bsc'
Parser().feed(html)
print('PASS: HTML shell, token config and fail-closed candidate decisions')
