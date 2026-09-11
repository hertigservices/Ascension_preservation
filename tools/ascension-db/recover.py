"""Bounded public Wayback inventory; downloaded code is never executed."""
import argparse, gzip, hashlib, io, json, re, time, urllib.request, urllib.parse
from pathlib import Path
from html.parser import HTMLParser
class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.title=[]; self.text=[]; self.hidden=0; self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag=='title': self.in_title=True
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
        if tag=='title': self.in_title=False
    def handle_data(self,data):
        if self.in_title: self.title.append(data.strip())
        if not self.hidden and data.strip(): self.text.append(data.strip())
def fetch(url,limit=6000000):
    req=urllib.request.Request(url,headers={'User-Agent':'Ascension-preservation-recovery/1.0 (public archive research)','Accept-Encoding':'identity'})
    with urllib.request.urlopen(req,timeout=35) as r:
        data=r.read(limit+1); mime=r.headers.get('Content-Type','')
    if len(data)>limit: raise ValueError('Response exceeds recovery limit')
    if data[:2]==b'\x1f\x8b':
        data=gzip.GzipFile(fileobj=io.BytesIO(data)).read(limit+1)
        if len(data)>limit: raise ValueError('Decoded response exceeds limit')
    return data,mime

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--seed',type=Path,required=True);ap.add_argument('--limit',type=int,default=35);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    seed=json.loads((a.seed/'recovery.json').read_text(encoding='utf-8'))
    report={'inventory_limit':5000,'retrieval_limit':a.limit,'complete_mirror':False,'results':[],'records':[]}
    query='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode({'url':'db.ascension.gg/*','output':'json','filter':'statuscode:200','collapse':'urlkey','from':'2025','limit':'5000','fl':'timestamp,original,mimetype,digest,statuscode'})
    try:
        data,_=fetch(query);rows=json.loads(data);report['inventory']=[dict(zip(rows[0],r)) for r in rows[1:]];report['inventory_truncated_possible']=len(rows)-1>=5000
    except Exception as e: report['inventory_error']=str(e);report['inventory']=[]
    candidates=[('20260806235722','https://db.ascension.gg/')]
    refs=[urllib.parse.urljoin('https://db.ascension.gg/',u) for u in seed['asset_references']]
    # Prioritize guide/detail and original core assets over third-party libraries.
    refs=sorted(set(refs),key=lambda x:(0 if '?guide=' in x else 1 if re.search(r'\?(item|spell|quest)=',x) else 2 if any(k in x for k in ('global.css','aowow.css','basic.css','home.css','power.js','global.js','basic.js')) else 3,x))
    candidates += [('20260806235722',u) for u in refs if urllib.parse.urlparse(u).hostname=='db.ascension.gg' and re.search(r'(\?(guide|spell|item|quest)=|\.(css|js)(\?|$))',u)]
    for r in report['inventory']:
        if r['mimetype']=='text/html' and re.search(r'\?(guide|spell|item|quest|skill)=',r['original']): candidates.append((r['timestamp'],r['original']))
    seen=set()
    for ts,url in candidates:
        if url in seen: continue
        if len(seen)>=a.limit: break
        seen.add(url);snap=f'https://web.archive.org/web/{ts}id_/{url}';r={'source':url,'snapshot':snap}
        try:
            if url=='https://db.ascension.gg/': data=(a.seed/'homepage.html').read_bytes();mime='text/html'
            else: data,mime=fetch(snap)
            if not data.strip(): raise ValueError('Empty archived response')
            sha=hashlib.sha256(data).hexdigest();r.update(sha256=sha,bytes=len(data),content_type=mime,status='recovered')
            # Store raw evidence outside the deployed page; never execute it.
            (a.out/(sha+'.evidence')).write_bytes(data)
            if 'html' in mime:
                page=Page();page.feed(data.decode('utf-8',errors='replace'))
                title=' '.join(page.title)
                if any(x in title.lower() for x in ('wayback machine','page not found','just a moment')): raise ValueError('Archive wrapper/error rather than source page')
                report['records'].append({'name':title or url,'key':url,'type':'guide' if '?guide=' in url else 'recovered-page','url':url,'archive_url':snap,'sha256':sha,'text':'\n'.join(page.text)})
        except Exception as e: r.update(status='unavailable',error=str(e))
        report['results'].append(r)
        (a.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(r['status'],url,flush=True)
        if '429' in r.get('error',''): break
        time.sleep(1.2)
    print('Recovered',len(report['records']),'readable pages; inventory',len(report['inventory']),flush=True)
if __name__=='__main__': main()
