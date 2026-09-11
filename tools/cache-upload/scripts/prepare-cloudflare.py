"""Create LOCAL deployment settings and secrets without printing secret values.
Requires the final origin, real Cloudflare D1 ID, R2 bucket, and Turnstile site key.
Does not deploy, create billable resources, or enable public uploads.
"""
import argparse, json, secrets
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--origin',required=True)
p.add_argument('--database-id',required=True)
p.add_argument('--bucket',default='ascension-contribution-private')
p.add_argument('--site-key',required=True)
p.add_argument('--private-dir',default='C:/AscensionArchive/upload-private')
a=p.parse_args()
from urllib.parse import urlparse
u=urlparse(a.origin)
if u.scheme!='https' or not u.netloc or u.path not in ('','/') or u.query or u.fragment: p.error('Origin must be an HTTPS origin without a path')
import re
if not re.fullmatch(r'[a-fA-F0-9-]{36}',a.database_id): p.error('A real D1 database ID is required')
root=Path(__file__).resolve().parents[1]
private=Path(a.private_dir).resolve();private.mkdir(parents=True,exist_ok=True)
for name in ['collector-token','rate-secret']:
    path=private/name
    if not path.exists(): path.write_text(secrets.token_hex(32),encoding='utf-8')
config=json.loads((root/'collector/config.example.json').read_text())
config.update({'url':a.origin.rstrip('/'),'state':str(private),'token_file':str(private/'collector-token')})
(private/'collector-config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
base=json.loads((root/'dist/server/wrangler.json').read_text())
base.update({'name':'ascension-cache-upload','main':'dist/server/index.js','assets':{'directory':'dist/client'},'observability':{'enabled':False},'d1_databases':[{'binding':'DB','database_name':'ascension-contribution-queue','database_id':a.database_id,'migrations_dir':'drizzle'}],'r2_buckets':[{'binding':'BUCKET','bucket_name':a.bucket}],'vars':{'UPLOADS_ENABLED':'false','ALLOWED_ORIGIN':a.origin.rstrip('/'),'TURNSTILE_SITE_KEY':a.site_key}})
(root/'wrangler.production.json').write_text(json.dumps(base,indent=2),encoding='utf-8')
print('Prepared private collector settings and ignored production configuration. Uploads remain disabled.')
print('Install collector-token and rate-secret as Worker secrets; enter the Turnstile secret privately. See README.md.')
