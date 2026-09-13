"""Optional private identity patterns, never embedded in code or public reports."""
import json
import hashlib
import re
from pathlib import Path


class IdentityGuard:
    def __init__(self,path):
        path=Path(path)
        if not path.is_file():raise ValueError('Required private identity-pattern file is missing; publication blocked')
        self.revision=hashlib.sha256(path.read_bytes()).hexdigest()
        lines=path.read_text(encoding='utf-8-sig').splitlines()
        try:self.patterns=[re.compile(line.strip(),re.I) for line in lines if line.strip() and not line.lstrip().startswith('#')]
        except re.error:raise ValueError('Private identity-pattern file is invalid; publication blocked') from None
        if not self.patterns:raise ValueError('Private identity-pattern file is empty; publication blocked')
    def check(self,value):
        text=json.dumps(value,ensure_ascii=False,separators=(',',':'))
        if any(pattern.search(text) for pattern in self.patterns):
            raise ValueError('Private identity-pattern match; public output blocked (matching identity is not logged)')


def configured(root):
    config=Path(root)/'publication-config.json'
    if not config.exists():return None
    settings=json.loads(config.read_text(encoding='utf-8-sig'))
    path=settings.get('identity_patterns_file')
    if not path:raise ValueError('Publication config requires a private identity-pattern file')
    return IdentityGuard(path)
