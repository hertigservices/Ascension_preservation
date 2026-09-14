"""Preserve independent remote updates without overwriting generated datasets."""
import subprocess

def synchronize(repo, branch):
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    def checked(*args):
        p=git(*args)
        if p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    try:
        checked('check-ref-format', 'refs/heads/'+branch)
        checked('fetch', 'origin', 'refs/heads/'+branch)
        remote=checked('rev-parse','FETCH_HEAD')
        if git('merge-base','--is-ancestor',remote,'HEAD').returncode==0:return True
        if checked('status','--porcelain','--untracked-files=no'):
            raise RuntimeError('Remote updates need integration, but the tracked working tree is not clean')
        base=checked('merge-base','HEAD',remote)
        paths=[p for p in checked('diff','--name-only','-z',base,remote).split('\0') if p]
        # Concurrent generated output or a storage-mode cutover requires review.
        protected=('cachedata/', 'data-packs/', '.ascension-data.json', '.gitattributes', '.lfsconfig')
        if any(p.startswith(protected) for p in paths):
            raise RuntimeError('Remote updates include generated data or storage configuration; review is required')
        local=set(p for p in checked('diff','--name-only','-z',base,'HEAD').split('\0') if p)
        if local.intersection(paths):raise RuntimeError('Local and remote changes overlap; review is required')
        print('Integrating independent GitHub updates; existing cache data is unchanged.',flush=True)
        before=checked('rev-parse','HEAD:cachedata')
        merge=git('merge','--no-ff','--no-commit',remote)
        if merge.returncode:
            git('merge','--abort');raise RuntimeError(merge.stderr.strip())
        try:
            after=checked('write-tree')
            if checked('rev-parse',after+':cachedata')!=before:raise RuntimeError('Integration changed cache data')
            checked('commit','-m','Integrate independent published repository updates')
        except BaseException:
            git('merge','--abort');raise
        return True
    except (OSError, RuntimeError) as exc:
        print('!! Cannot safely synchronize publication: '+str(exc),flush=True)
        return False
