"""Consolidate whatever is in the inbox and publish the result.

    python publish.py            # consolidate, show what changed, do not push
    python publish.py --push     # ...and push it
    python publish.py --watch    # keep watching the inbox and repeat

This is the whole loop in one place: drop a submission in `_inbox/`, and the
merged data ends up public without anyone deciding anything by hand.

WHAT GUARDS THE PUSH
--------------------
Everything here is reversible except the push.  A force-push does not un-publish
a leak -- once account data is on GitHub it has been fetched, cached and indexed,
and deleting the commit changes none of that.  So the audit is a hard gate: it
runs against the bytes in the repository working tree, not against the export
directory they were copied from, and a non-zero exit stops the run before
anything is committed.  There is deliberately no flag to skip it.

The same reasoning applies to the pipeline stages: if merging or rebuilding
failed, the data on disk is a half-written state and must not be published as
though it were a consolidation.

WHAT IT WILL NOT DO
-------------------
It never invents a remote, never force-pushes, and never commits anything under
`quarantine/`.  If tracked files have unrelated changes, it says so and
stops rather than sweeping someone else's edit into a commit. Untracked drafts
outside the output stay uncommitted and do not block the run.
"""
import os, io, re, sys, subprocess, shutil, time, filecmp

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config

REPO = config.PUBLISH_REPO
DATA = os.path.join(REPO, "cachedata")
POLL_SECONDS = 60
BUSY_EXIT = 75  # temporary lock contention, not a pipeline failure


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        print((r.stdout + r.stderr).strip())
        raise SystemExit(f"!! {' '.join(cmd)} failed ({r.returncode})")
    return r


def stream(cmd, cwd=None, every=10):
    """Run a command with its output arriving live, not at the end.

    The push is the one step here that can take minutes, and it was the one step
    with nothing to look at: git writes transfer progress to stderr, `run()`
    captures stderr, and git suppresses progress altogether when stderr is not a
    terminal -- which it is not when something is reading this output. So a large
    push printed "committed" and then nothing at all until it finished, which is
    indistinguishable from a hang.

    `--progress` makes git report anyway; not capturing lets it through. Progress
    lines are thinned to one every `every` percent, because git emits hundreds and
    a log is not a terminal that can overwrite its own last line.
    """
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1)
    tail, last = [], {}
    for line in p.stdout:
        line = line.rstrip("\r\n")
        if not line:
            continue
        tail.append(line)
        del tail[:-40]
        # The server echoes its own progress back prefixed "remote: ";
        # strip that before matching or those lines are never thinned,
        # and on a large push they are the noisiest of the lot.
        m = re.match(r"^(?:remote:\s*)?([A-Za-z][A-Za-z ]+):\s+(\d+)%", line)
        if m:
            phase, pct = m.group(1), int(m.group(2))
            if pct - last.get(phase, -every) < every and pct != 100:
                continue
            last[phase] = pct
        print("   " + line, flush=True)
    return p.wait(), tail


def push_branch(branch):
    """Retry an unresolved thin-pack base once without rerunning the pipeline.

    Keep normal efficient pushes and ordinary rejection behavior. A self-contained
    pack avoids requiring delta bases already on the receiver. No force, audit
    bypass, index change, or history rewrite is involved.
    """
    command = ["git", "push", "--progress", "origin", branch]
    rc, tail = stream(command, cwd=REPO)
    unresolved = any(re.search(r"remote:.*fatal: pack has [0-9]+ unresolved deltas", line)
                     for line in tail)
    if rc and unresolved:
        print("Remote could not resolve the transfer's delta bases; retrying once "
              "with a self-contained pack (no consolidation rerun).", flush=True)
        rc, tail = stream(["git", "push", "--progress", "--no-thin", "origin", branch],
                          cwd=REPO)
    return rc, tail


def nul(*args):
    """A git path list, split on NUL instead of on whitespace.

    Without -z git quotes any name holding a space, and splitting the result
    then yields two names that are each wrong. The repo contains
    `tools/Install Caches.cmd`, so this is the ordinary case, not the corner.
    """
    return [p for p in git(*args).stdout.split(chr(0)) if p]


def status_paths(*extra):
    """(XY, path) per porcelain entry, with the real path, never a quoted one.

    The quoting is what broke the tools/ exemption below: git reported
    `?? "tools/Install Caches.cmd"`, the prefix test saw a leading quote
    instead of `tools/`, and a file the guard was written to ignore blocked
    publishing instead. -z never quotes.

    Rename and copy entries carry a second NUL-terminated field holding the
    old name. Consume it, or it reads as an entry of its own with someone
    else's status bytes sliced off the front of it.
    """
    recs = iter(nul("status", "--porcelain", "-z", *extra))
    out = []
    for rec in recs:
        out.append((rec[:2], rec[3:]))
        if rec[:1] in ("R", "C"):
            next(recs, None)
    return out


def git(*args, check=True):
    return run(["git", *args], cwd=REPO, check=check)


def inbox_fingerprint():
    """(name, size, mtime) for everything in the inbox, archive/ included.

    Content hashing would be more precise and much slower on a 200 MB drop; the
    pipeline dedups by hash anyway, so the only cost of a false "changed" is one
    wasted no-op run.
    """
    out = []
    for dp, _d, fs in os.walk(config.INBOX):
        for fn in sorted(fs):
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((os.path.relpath(p, config.INBOX), st.st_size,
                        int(st.st_mtime)))
    return sorted(out)


def settled(prev):
    """True when the inbox stopped changing.

    A drop of several archives arrives over seconds, and a file still being
    copied is a truncated archive. Consolidating mid-copy would ledger a corrupt
    submission, so wait for two identical readings before touching anything.
    """
    time.sleep(3)
    return inbox_fingerprint() == prev


def consolidate():
    print(f"\n{'='*70}\n== consolidating\n{'='*70}", flush=True)
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "update.py")])
    return r.returncode == 0


def sync_data():
    """Mirror the export into the repository, deletions included.

    A copy that only adds leaves a mode behind after it stops existing -- the
    same stale-output problem the exporter prunes internally -- so the mirror
    has to remove as well as write.
    """
    src = config.OUT
    os.makedirs(DATA, exist_ok=True)
    wrote = removed = 0
    keep = set()
    for dp, _d, fs in os.walk(src):
        rel = os.path.relpath(dp, src)
        dst_dir = os.path.join(DATA, rel) if rel != "." else DATA
        os.makedirs(dst_dir, exist_ok=True)
        for fn in fs:
            s, d = os.path.join(dp, fn), os.path.join(dst_dir, fn)
            keep.add(os.path.normcase(os.path.abspath(d)))
            # shallow=False: compare contents, not just size and mtime. A
            # regenerated-but-identical file must not show up as a change.
            if not (os.path.exists(d) and filecmp.cmp(s, d, shallow=False)):
                shutil.copy2(s, d)
                wrote += 1
    for dp, _d, fs in os.walk(DATA, topdown=False):
        for fn in fs:
            p = os.path.join(dp, fn)
            if os.path.normcase(os.path.abspath(p)) not in keep:
                os.remove(p)
                removed += 1
        if dp != DATA and not os.listdir(dp):
            os.rmdir(dp)
    print(f"data: {wrote} file(s) written, {removed} removed")
    return wrote or removed


def sync_tools():
    """Publish the pipeline itself, but only files the repo already tracks.

    The intake directory also holds this maintainer's private realm-database
    scripts. Adding "every .py here" would publish those the first time someone
    wrote a new one, so the shipped set stays an explicit allow-list -- the
    files already under version control.
    """
    if os.path.isfile(os.path.join(REPO, ".ascension-data.json")):
        return []  # Source is maintained in Ascension_preservation.
    tracked = nul("ls-files", "-z", "tools")
    # Which of these is someone else part-way through? copy2 would erase that
    # edit with no diff, no prompt and no trace -- the intake copy simply wins,
    # and the loss looks exactly like the author forgetting to save. tools/ is
    # exempt from the foreign-change check because this script owns it, so this
    # is the only place that can notice.
    dirty = set(p for _xy, p in status_paths("--", "tools"))
    wrote, held = [], []
    for rel in tracked:
        s = os.path.join(HERE, os.path.basename(rel))
        d = os.path.join(REPO, rel.replace("/", os.sep))
        if not os.path.exists(s) or filecmp.cmp(s, d, shallow=False):
            continue
        if rel in dirty:
            held.append(rel)
            continue
        shutil.copy2(s, d)
        wrote.append(rel)
    if held:
        # Named, and not fatal. Publishing DATA must not stop because a
        # maintainer is editing a script; the held file just does not travel
        # this run, and it is not staged either, so nothing half-done ships.
        print("tools: NOT overwriting %d file(s) edited in the repo:"
              % len(held))
        for rel in held:
            print("   " + rel)
        print("   commit or revert them, or copy them into " + HERE)
    if wrote:
        print(f"tools: {len(wrote)} file(s) updated from {HERE}")
    # The caller stages these by name. Returning a count instead is what let
    # `git add -A tools` publish a file this script never touched.
    return wrote


def audit():
    """The gate. Runs on the repository tree -- the actual bytes to be pushed."""
    print(f"\n{'='*70}\n== publish audit\n{'='*70}", flush=True)
    ok = True
    targets = [p for p in (DATA, os.path.join(REPO, "tools"), os.path.join(REPO, "docs"))
               if os.path.isdir(p)]
    # The README and LICENSE sit at the repository root, so walking the
    # subdirectories never touched them -- and they are published too. Ask git
    # which top-level files are tracked rather than naming them here, so a new
    # one is covered the day it appears.
    targets += [os.path.join(REPO, f) for f in nul("ls-files", "-z")
                if "/" not in f]
    for target in targets:
        if not os.path.exists(target):
            continue
        r = subprocess.run([sys.executable, "-B",
                            os.path.join(HERE, "audit_publish.py"), target])
        ok = ok and r.returncode == 0
    # The other half of the gate. audit_publish asks whether the tree contains
    # something it must not; this asks whether it still contains what it is
    # supposed to. Both have to pass, because the dataset has already been
    # published once with every vendor price silently zeroed and nothing in
    # here noticed -- see the header of audit_columns.py.
    r = subprocess.run([sys.executable, "-B",
                        os.path.join(HERE, "audit_columns.py"), DATA])
    ok = ok and r.returncode == 0
    # The gate itself is only worth as much as its own test.
    for t in ("test_audit.py", "test_columns.py"):
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, t)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            print(f"!! the publish gate failed its own test ({t})")
            ok = False
    ok = check_tool_imports() and ok
    return ok


def check_tool_imports():
    """Every module a published tool imports must itself be published.

    sync_tools() ships an allow-list -- the files git already tracks -- which
    is right for keeping private scripts out and wrong in one direction nobody
    was watching: a NEW module is not on the list, so the first publish after
    splitting code into one silently ships the caller without the callee. That
    is not hypothetical. luamerge.py grew `import harvestmerge` and was pushed
    without tools/harvestmerge.py, so the public pipeline died on
    ModuleNotFoundError for anyone who cloned it, while every check here passed
    -- the same shape as the bug audit_columns.py exists for, and caught by the
    same reflex: ask whether what we shipped is still whole, not just clean.

    Static, on the repository tree, because importing them would run them.
    """
    tracked = [f for f in nul("ls-files", "-z", "tools")
               if f.endswith(".py")]
    have = set(os.path.splitext(os.path.basename(f))[0] for f in tracked)
    missing = {}
    for rel in tracked:
        p = os.path.join(REPO, rel.replace("/", os.sep))
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8", errors="replace") as f:
            src = f.read()
        for m in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][\w, ]*)",
                            src, re.M):
            for name in (x.strip() for x in m.split(",")):
                # A local module is one that exists as a .py beside this file.
                # Anything else is stdlib or third-party and not our problem.
                if (name and name not in have
                        and os.path.exists(os.path.join(HERE, name + ".py"))):
                    missing.setdefault(name, []).append(os.path.basename(rel))
    if missing:
        print(f"\n{'='*70}")
        print("!! PUBLISHED TOOLS ARE INCOMPLETE -- these modules are imported "
              "but not tracked:")
        for name, by in sorted(missing.items()):
            print("  tools/%-28s imported by %s" % (name + ".py", ", ".join(sorted(set(by)))))
        print("Fix with:  git add tools/<name>.py   (check it is publishable "
              "first -- this list is exactly how a private script would get in)")
        return False
    return True


def describe(status):
    counts = {}
    for xy, _path in status:
        counts[xy.strip() or "?"] = counts.get(xy.strip() or "?", 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))


LOCK = os.path.join(config.WORK, "publish.lock")


def _pid_alive(pid):
    """Windows has no kill(pid, 0); ask the task list instead.

    Wrong answers here are not symmetric. Reporting a dead holder as alive
    wedges publishing until someone deletes a file by hand; reporting a live
    holder as dead lets two runs collide, which is the thing being prevented.
    So anything unexpected -- tasklist missing, output unreadable -- is treated
    as ALIVE and the run backs off.
    """
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return str(pid) in out


class Lock(object):
    """One publish at a time, across processes, held from intake to commit.

    tray_app.py already serialises its OWN runs, but that lock lives inside one
    process, so it says nothing about a publish.py started from a shell -- and
    on 2026-09-09 those two ran together: two export.py writing the same
    union/*.tsv.gz, and union/itemcache.tsv.gz read back mid-write as a
    truncated gzip. It was caught, but by luck of ordering rather than by
    design, and the failure it can produce is the quiet kind: a second writer
    that lands between the gate and the commit replaces a file the gate has
    already blessed with one that is complete, valid, and stale.

    An integrity check cannot see that. Only exclusion can, which is why this
    is a lock and not another audit.
    """

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(LOCK)), exist_ok=True)
        me = "%d\n%s\n%s\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S"),
                               " ".join(sys.argv))
        for attempt in (1, 2):
            try:
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, me.encode("utf-8"))
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    with io.open(LOCK, encoding="utf-8") as f:
                        who = f.read().strip().splitlines()
                except OSError:
                    who = []
                pid = int(who[0]) if who and who[0].isdigit() else 0
                if attempt == 1 and pid and not _pid_alive(pid):
                    # A killed or crashed run leaves its lock behind. Say so out
                    # loud -- silently stealing a lock is how a real collision
                    # gets mistaken for a stale one.
                    print("publish.lock held by pid %d, which is gone -- "
                          "taking it over (was: %s)"
                          % (pid, " | ".join(who[1:]) or "no detail"))
                    try:
                        os.unlink(LOCK)
                    except OSError:
                        pass
                    continue
                print("\n~~ another publish is already running -- pid %s, "
                      "started %s" % (who[0] if who else "?",
                                      who[1] if len(who) > 1 else "?"))
                print("   %s" % (who[2] if len(who) > 2 else ""))
                print("   Refusing to run two at once: they share "
                      "%s and the repository." % config.OUT)
                print("   Wait for it to finish, or kill it and retry.")
                self.held = False
                return self
        self.held = False
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.unlink(LOCK)
            except OSError:
                pass
        return False


def publish(push):
    with Lock() as lock:
        if not lock.held:
            return None
        return _publish(push)


def _publish(push):
    # Untracked drafts outside our output never enter the scoped git add.
    # Keep them in place; tracked edits still need their owner's attention.
    # Check before spending several minutes rebuilding the data.
    foreign = [(xy, p) for xy, p in status_paths()
               if xy != "??" and not p.startswith(("cachedata/", "tools/"))]
    if foreign:
        print("\n!! the working tree has changes this script did not make:")
        for xy, p in foreign[:20]:
            print("   %s %s" % (xy, p))
        print("   commit or revert them first; refusing to sweep them into a push")
        return False

    # A pre-existing staged tool must also block a no-data-change run, which
    # otherwise reaches push without entering the commit-time index guard.
    staged = nul("diff", "--cached", "--name-only", "-z")
    strays = [p for p in staged if not p.startswith("cachedata/")]
    if strays:
        print("\n!! the index holds files this run did not write:")
        for p in strays[:20]:
            print("   " + p)
        print("   unstage them first; refusing to publish unrelated staged work")
        return False

    import batch_inputs, intake_jobs
    manual = intake_jobs.manual_request(config.WORK, os.environ.get("ASCENSION_MANUAL_REQUEST"))
    if manual and manual.get('tidy_only'):
        print('Filed completed inputs:',len(intake_jobs.archive_completed(config.WORK)))
        return True
    if manual and manual.get('rename'):
        import tray_app
        from types import SimpleNamespace
        tray_app.App.prepare_inbox(SimpleNamespace(state={'file':True},consumed=set(),log=print),set(manual.get('pristine',[])))
    batch = batch_inputs.prepare(os.environ.get("ASCENSION_BATCH_PLAN"), config.INBOX)
    inputs = intake_jobs.snapshot(config.INBOX)
    if inputs:
        time.sleep(5)
        if intake_jobs.snapshot(config.INBOX)!=inputs:
            print('Inbox is still being copied; retrying after it settles.')
            return None
    if not consolidate():
        print("\n!! the pipeline did not finish; nothing published")
        return False

    batch_inputs.record(batch, config.OUT, config.WORK)
    copied = sync_tools()
    changed = sync_data() + len(copied)
    if not audit():
        print("\n!! AUDIT FAILED -- nothing committed, nothing pushed.")
        print("   Every hit must be explained before this can go public.")
        return False
    print("\naudit clean")

    # Scoped to what this run produced. Asked about the whole tree, a second
    # maintainer's half-finished file reads as "there is something to commit",
    # and the commit that follows either sweeps it in or fails outright with
    # nothing staged.
    ours = ["cachedata"] + copied
    status = status_paths("--", *ours)
    if not status:
        # "Nothing to commit" is not "nothing to push". A run that committed and
        # then failed to push -- dropped network, timeout, an interrupted run --
        # leaves the work in a local commit, and returning success here would
        # mean every later run cheerfully reports "already published" while that
        # commit is never sent anywhere. Fall through instead; the push below
        # costs nothing when there is genuinely nothing to send.
        print("nothing new to commit")
    else:
        print(f"\n{len(status)} path(s) changed: {describe(status)}")
        # cachedata is wholly this script's output, so -A is right there --
        # sync_data mirrors it including deletions. tools/ is shared with a
        # human editing the same checkout, so only the files sync_tools just
        # copied are ours to commit.
        git("add", "-A", "cachedata")
        for rel in copied:
            git("add", "--", rel)
        msg = ("Consolidate submissions and republish\n\n"
               "Automated run: every file in the inbox merged, deduplicated and\n"
               "re-exported, then audited for player data before commit.\n\n"
               "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n")
        # Last look before the one irreversible step. Staging is scoped above,
        # but the index is shared: anything a person left staged would ride
        # along into an automated commit and, with --push, straight to GitHub.
        staged = nul("diff", "--cached", "--name-only", "-z")
        strays = [f for f in staged
                  if not f.startswith("cachedata/") and f not in copied]
        if strays:
            print(chr(10) + "!! the index holds files this run did not write:")
            for f in strays[:20]:
                print("   " + f)
            print("   unstage them first; refusing to commit someone "
                  "else's work under this message")
            return False
        git("commit", "-m", msg)
        print("committed " + git("rev-parse", "--short", "HEAD").stdout.strip())

    if not push:
        print("\nnot pushing (pass --push to publish)")
        return True
    if not git("remote", check=False).stdout.strip():
        print("!! no git remote configured; commit kept, nothing pushed")
        return False
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    print(f"pushing {branch} to origin -- this is the slow part on a big "
          f"dataset, and it reports as it goes:", flush=True)
    t0 = time.time()
    rc, tail = push_branch(branch)
    if rc != 0:
        print("\n".join(tail))
        print("!! push failed; the commit is still here, retry when resolved")
        return False
    print(f"pushed {branch} in {time.time() - t0:.0f}s")
    retained = {}
    completed = intake_jobs.eligible_completed(config.WORK,config.OUT,inputs,retained=retained)
    intake_jobs.file_completed(config.WORK,completed,git('rev-parse','HEAD').stdout.strip(),retained=retained)
    if retained:
        print('Preserved undecoded files in processed bundles:',sum(map(len,retained.values())),
              '-- see _inbox/archive/RETAINED-FILES.md after filing')
    if not manual or manual.get('tidy'):
        print('Filed completed inputs:',len(intake_jobs.archive_completed(config.WORK)))
    return True


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description="Consolidate and audit datasets; --push publishes them.")
    parser.add_argument("--push", action="store_true", help="Push audited dataset commits to the configured remote")
    parser.add_argument("--watch", action="store_true", help="Repeat when the intake changes")
    args = parser.parse_args(argv)
    push = args.push
    if not args.watch:
        result = publish(push)
        return BUSY_EXIT if result is None else (0 if result else 1)
    print(f"watching {config.INBOX}\n  repo: {REPO}\n"
          f"  push: {'yes' if push else 'no (dry run)'}")
    last = None
    while True:
        now = inbox_fingerprint()
        if now != last and settled(now):
            now = inbox_fingerprint()
            if last is not None:
                print(f"\n[{time.strftime('%H:%M:%S')}] inbox changed "
                      f"({len(now)} files)")
            if publish(push):
                last = now
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
