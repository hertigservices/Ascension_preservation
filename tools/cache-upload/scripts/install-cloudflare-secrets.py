"""Install deployment secrets without command-line or log exposure."""
import argparse, getpass, subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--private-dir',default='C:/AscensionArchive/upload-private')
a=p.parse_args()
root=Path(__file__).resolve().parents[1]
if not (root/'wrangler.production.json').exists(): p.error('Run prepare-cloudflare.py first')
values={'COLLECTOR_TOKEN':(Path(a.private_dir)/'collector-token').read_text().strip(),'RATE_SECRET':(Path(a.private_dir)/'rate-secret').read_text().strip(),'TURNSTILE_SECRET':getpass.getpass('Turnstile secret (hidden): ').strip()}
if any(len(v)<20 for v in values.values()): p.error('A secret is missing or too short')
for name,value in values.items():
    subprocess.run(['node',str(root/'node_modules/wrangler/bin/wrangler.js'),'secret','put',name,'--config',str(root/'wrangler.production.json')],cwd=root,input=value+'\n',text=True,check=True)
print('Worker secrets installed. Collection is still controlled by UPLOADS_ENABLED.')
