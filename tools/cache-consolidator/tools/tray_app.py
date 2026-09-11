# -*- coding: utf-8 -*-
"""Cache Consolidator -- a tray app that watches the inbox and runs the pipeline.

    pythonw tools\\tray_app.py      (or double-click "Cache Consolidator.cmd")

WHAT IT IS FOR

    Drop a submission in _inbox and forget about it. While this sits in the tray it
    polls the inbox, waits for the drop to finish copying, files it under a name
    that cannot collide with anyone else's, runs the whole consolidate - audit -
    publish pipeline, and then moves the finished submission out of the way. Click
    the tray icon for a window with a live log and buttons for the manual commands.

WHY IT SHELLS OUT TO publish.py INSTEAD OF IMPORTING IT

    publish.py raises SystemExit on any git failure, and its own --watch loop has no
    guard around that: one .git/index.lock collision and the watcher is simply gone,
    with nothing left running to say so. Running it as a child process turns that
    into a non-zero exit code this supervisor can see, report and retry, and the
    tray icon survives it. It also keeps the published pipeline the one that is
    actually tested -- this file adds supervision, not logic.

WHY A FAILED RUN IS NOT MARKED DONE

    publish.py --watch records the inbox fingerprint whether the run succeeded or
    not, so a transient failure -- a GitHub hiccup, a lock collision -- leaves that
    submission unpublished until somebody happens to drop a different file. Here the
    fingerprint is recorded only after a clean exit; a failure retries on a backoff
    and says so.

WHY IT RENAMES ARRIVING ARCHIVES BUT NEVER LOOSE FILES

    intake extracts each archive into extracted/<stem>/ and, if that directory
    already exists, reports "cached" and moves on. Two submitters both sending
    WDB.zip therefore collide, and the second one is silently skipped -- which is
    why these have had to be renamed WDB(1).zip, WDB(2).zip by hand. So an arriving
    ARCHIVE is renamed to <stem>__<hash><ext>: unique per content, so two different
    WDB.zip files get two extractions and the same file dropped twice gets one.

    Loose .lua files are deliberately never renamed. luamerge matches them by exact
    filename against a five-name allow-list, so AIO_Client (1).lua is not a slightly
    wrong name -- it is an unrecognised file, dropped without comment.

    Archives already present at startup are normalized too. Intake reuses a
    completed extraction by its full archive hash, retaining the original source
    identity instead of counting a renamed archive as another contribution.
    Legacy extractions without hash evidence keep their original archive names.

WHY IT REFUSES TO RUN ELEVATED

    intake hands every submitted archive to 7-Zip, and a crafted archive can hold
    entries that resolve outside the extraction directory. When that was tested
    here, the escape failed because Windows refused to create the link WITHOUT
    ADMINISTRATOR -- not because 7-Zip or the intake code caught it. This app exists
    to point that pipeline at files strangers upload to a public Discord channel, so
    running it elevated would remove the only control that actually stopped the
    attack. It exits instead of asking.
"""
import ctypes
import hashlib
import json
import io
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import intake_jobs
from publish import BUSY_EXIT

PUBLISH = os.path.join(HERE, "publish.py")
STATE_PATH = os.path.join(config.WORK, "tray_state.json")
REPO = config.PUBLISH_REPO
UPSTREAM_URL = "https://github.com/hertigservices/ascension-data"
DONE_DIR = os.path.join(config.INBOX, "archive")
# Everything the window shows is appended here too, so a run that failed
# while nobody was watching can still be read back afterwards.
LOG_PATH = os.path.join(config.WORK, "tray_app.log")

APP_NAME = "Cache Consolidator"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AscensionCacheConsolidator"

POLL_SECONDS = 20        # how often the inbox is looked at
SETTLE_SECONDS = 5       # a drop must look identical this long before we touch it
RETRY_BACKOFF = [60, 300, 900, 1800]   # after the 1st, 2nd, 3rd, 4th+ failure
SINGLETON_PORT = 49731   # binding it is the "is another copy running" test
TAG = re.compile(r"__[0-9a-f]{8,16}$")   # a name this app has already filed


# --------------------------------------------------------------------- guards

def is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_singleton_sock = None


def claim_singleton():
    """False if another copy already holds the port.

    A socket rather than a lock file, because the OS releases it even when the
    process is killed -- a crash never leaves the app unable to start again.
    """
    global _singleton_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLETON_PORT))
    except OSError:
        s.close()
        return False
    s.listen(1)
    _singleton_sock = s      # held for the life of the process
    return True


def origin_url():
    """The web address of THIS checkout's origin remote, or None.

    Kept separate from UPSTREAM_URL on purpose. The upstream repository is the
    one-stop shop -- the place every submission is collected and the merged
    dataset is published -- so that is what the GitHub button opens, for a fork
    just as much as for the original. But publishing runs `git push origin` in
    whatever checkout it finds, so a fork's data goes to the fork. Showing both
    is the only way that is not confusing.
    """
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO,
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    url = (r.stdout or "").strip()
    if r.returncode != 0 or not url:
        return None
    # An SSH remote reads "<user>@<host>:owner/repo.git"; turn it into the
    # https form so it can be opened in a browser. (Spelling a whole host
    # out in a comment here trips the publish gate, which hunts for
    # anything email-shaped -- so do not put one back.)
    if url.startswith("git@"):
        url = "https://" + url[4:].replace(":", "/", 1)
    if url.endswith(".git"):
        url = url[:-4]
    return url


def console_python():
    """python.exe even when we were started by pythonw.exe.

    The child needs a real stdout to stream; deriving it from sys.executable keeps
    us on the same interpreter and the same site-packages.
    """
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            return alt
    return exe


# ---------------------------------------------------------------------- state

DEFAULTS = {"watch": True, "push": True, "file": True, "tidy": True}


def load_state():
    s = dict(DEFAULTS)
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            loaded = json.load(f)
        for k in DEFAULTS:
            if k in loaded:
                s[k] = bool(loaded[k])
    except Exception:
        pass
    return s


def save_state(s):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
    except OSError:
        pass


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        return True
    except Exception:
        return False


def set_autostart(on):
    """Add or remove this app from the current user's logon Run key.

    HKCU only, and only ever from an explicit click -- the app never installs
    itself. Returns a line for the log.
    """
    import winreg
    try:
        if on:
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                alt = exe[:-len("python.exe")] + "pythonw.exe"
                if os.path.exists(alt):
                    exe = alt
            cmd = '"%s" "%s"' % (exe, os.path.abspath(__file__))
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            return "will start at logon: %s" % cmd
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
        return "will no longer start at logon"
    except FileNotFoundError:
        return "was not set to start at logon"
    except OSError as e:
        return "!! could not change the logon setting: %s" % e


# ------------------------------------------------------------------ the inbox

def inbox_fingerprint():
    """{relpath: (size, mtime)} for everything in the inbox.

    Content hashing would be more precise and far slower on a 200 MB drop; the
    pipeline dedups by hash anyway, so the only cost of a false "changed" is one
    no-op run.
    """
    out = {}
    for dp, _dirs, files in os.walk(config.INBOX):
        if os.path.abspath(dp)==os.path.abspath(config.INBOX):_dirs[:]=[d for d in _dirs if d != "archive"]
        for fn in files:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, config.INBOX)] = (st.st_size, st.st_mtime_ns)
    return out


def root_files():
    """Files sitting directly in the inbox, i.e. things just dropped in."""
    try:
        return sorted(f for f in os.listdir(config.INBOX)
                      if os.path.isfile(os.path.join(config.INBOX, f)))
    except OSError:
        return []


def root_dirs():
    """Folders dropped straight into the inbox, never the inbox's own plumbing.

    People drag folders in as often as they drag archives in, and until now
    nothing ever filed one: tidy_inbox() moved whatever root_files() returned,
    root_files() filters on os.path.isfile, so a dropped folder was merged,
    left in place, and then looked like a fresh drop on the very next poll --
    eight full pipeline runs in one hour off a single folder.

    The exclusion is the whole risk here. DONE_DIR is *inside* the inbox, so a
    version of this that returned every directory would move archive/ into
    archive/YYYY-MM/archive/ and take the project's raw evidence with it. The
    rule comes from intake rather than a copy of it, for the reason
    archive_rules() gives; if intake cannot be imported we file no directories
    at all, which is the safe direction. DONE_DIR is then excluded again by
    real path, because that one must hold whether or not any import worked.
    """
    try:
        import intake
        tidy = intake.TIDY
    except Exception:
        return []
    done = os.path.realpath(DONE_DIR)
    out = []
    try:
        names = os.listdir(config.INBOX)
    except OSError:
        return []
    for name in sorted(names):
        p = os.path.join(config.INBOX, name)
        if not os.path.isdir(p):
            continue
        if tidy.match(name):
            continue
        if os.path.realpath(p) == done:
            continue
        out.append(name)
    return out


def archive_rules():
    """intake's own extension list and splitter, imported rather than copied.

    Two allow-lists that are supposed to agree will eventually stop agreeing. If
    intake cannot be imported we file nothing, which is the safe direction: the
    worst case is the old manual renaming, not a mis-filed submission.
    """
    try:
        import intake
        return set(intake.ARCHIVE_EXT), intake.split_archive
    except Exception:
        return None, None


def content_tag(path, n=8):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def file_arrival(name, log):
    """Rename one freshly-arrived archive to a name that cannot collide.

    Returns the new filename, or None if the file was left exactly as it is.
    """
    exts, split = archive_rules()
    if exts is None:
        return None
    src = os.path.join(config.INBOX, name)
    base, ext = split(name)
    if ext not in exts:
        return None            # loose .lua and friends keep their exact names
    if TAG.search(base):
        return None            # already filed by a previous run
    try:
        full_hash = content_tag(src, 64)
        import intake
        if full_hash not in intake.extraction_hashes():
            stem = re.sub(r"[^A-Za-z0-9._()+-]", "_", base)
            for candidate in (stem, stem + "__" + ext.replace(".", "")):
                old = os.path.join(intake.EXTRACT, candidate)
                if intake.occupied(old) and not re.fullmatch(r'[a-f0-9]{64}', intake._marker(old, intake.SRCHASH) or ''):
                    log("   kept %s: legacy extraction has no content-hash evidence" % name)
                    return None
        tag = full_hash[:8]
        dst = os.path.join(config.INBOX, "%s__%s%s" % (base, tag, ext))
        if os.path.exists(dst):
            # Same original name and same content hash: this is the same file
            # arriving twice. Collapse it rather than growing a second copy --
            # but only after the FULL hashes agree, so an 8-hex collision
            # cannot quietly overwrite a different submission.
            if content_tag(dst, 64) == full_hash:
                os.replace(src, dst)
                log("   %s is a duplicate of %s already filed"
                    % (name, os.path.basename(dst)))
                return os.path.basename(dst)
            tag = content_tag(src, 16)
            dst = os.path.join(config.INBOX, "%s__%s%s" % (base, tag, ext))
            if os.path.exists(dst):
                return None
        os.replace(src, dst)
        log("   filed %s -> %s" % (name, os.path.basename(dst)))
        return os.path.basename(dst)
    except OSError as e:
        log("!! could not file %s: %s" % (name, e))
        return None


def tidy_inbox(log, processed=None):
    """Move consolidated drops out of the inbox root into archive/YYYY-MM/.

    Both loose files and dropped folders, which is the point -- see root_dirs()
    for what leaving folders behind actually cost.

    Nothing is deleted: these archives are the raw evidence of a preservation
    project. The name is kept byte for byte, because intake keys extraction on
    the stem -- change that and everything is extracted a second time. Moving
    is safe for provenance too: intake's ledger is keyed on the sha256 of the
    file, not its path, and submission_segment() skips archive/ and YYYY-MM/
    segments, so a submission keeps its identity after being filed.

    Returns [(oldrelpath, newrelpath)].
    """
    moved = []
    names = root_files()
    dirs = root_dirs()
    if not names and not dirs:
        return moved
    month = time.strftime("%Y-%m")
    dest = os.path.join(DONE_DIR, month)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        log("!! could not make %s: %s" % (dest, e))
        return moved
    n_dirs = 0
    for name in names + dirs:
        is_dir = name in dirs
        if processed is not None:
            # Recheck each root immediately before moving it. A new file, or
            # a folder with new/changed children, belongs to the next sweep.
            current = inbox_fingerprint()
            members = {p: v for p, v in current.items()
                       if p == name or p.startswith(name + os.sep)}
            if not members or any(processed.get(p) != v for p, v in members.items()):
                log("~~ leaving %s pending: arrived or changed during the run" % name)
                continue
        src = os.path.join(config.INBOX, name)
        dst = os.path.join(dest, name)
        if os.path.exists(dst):
            # Same name already archived. Keep both: identical content is
            # harmless to the pipeline (it dedups by hash) and losing a
            # differing file would be silent data loss.
            if is_dir:
                # splitext on a folder is not merely useless, it is wrong:
                # "Rexxar - Conquest of Azeroth" splits at nothing, but any
                # folder with a dot in its name would be cut at that dot and
                # filed under a name nobody dropped.
                dst = os.path.join(dest, "%s__%s" % (name, int(time.time())))
            else:
                stem, ext = os.path.splitext(name)
                dst = os.path.join(dest, "%s__%s%s"
                                   % (stem, int(time.time()), ext))
        boundary = os.path.normcase(os.path.realpath(config.INBOX))
        targets = [os.path.normcase(os.path.realpath(p)) for p in (src, dst)]
        if any(os.path.commonpath([boundary, p]) != boundary or p == boundary
               for p in targets):
            log("!! refusing to move %s outside the inbox" % name)
            continue
        try:
            shutil.move(src, dst)
            moved.append((name, os.path.relpath(dst, config.INBOX)))
            if is_dir:
                n_dirs += 1
        except OSError as e:
            log("!! could not move %s: %s" % (name, e))
    if moved:
        what = "%d file(s)" % (len(moved) - n_dirs)
        if n_dirs:
            what = ("%s and %d folder(s)" % (what, n_dirs) if len(moved) - n_dirs
                    else "%d folder(s)" % n_dirs)
        log("tidied %s into archive/%s (nothing deleted; they are still "
            "scanned from there)" % (what, month))
    return moved


# --------------------------------------------------------------------- runner

def mmss(sec):
    sec = int(sec)
    return "%d:%02d" % (sec // 60, sec % 60) if sec >= 60 else "%ds" % sec


class Runner(object):
    """Runs one pipeline invocation at a time and streams its output to the log.

    Everything that starts a run goes through here, so a manual click during a
    watch run queues behind it instead of launching a second publish.py against
    the same git repository -- the exact collision that killed a watcher once.
    """

    def __init__(self, log, on_change):
        self.log = log
        self.on_change = on_change
        self.lock = threading.Lock()
        self.proc = None
        self.busy = threading.Event()
        self.label = ""
        self.started = 0.0
        self.last_result = "not run yet"
        self.last_ok = None

    def run(self, push, label, options=None):
        if not self.lock.acquire(blocking=False):
            self.log("~~ %s: a run is already in progress, ignoring" % label)
            return None
        self.busy.set()
        self.label = label
        self.started = time.time()
        self.on_change()
        try:
            if intake_jobs.read(os.path.join(config.WORK,'shared-collector.json'),{}).get('enabled'):
                request=intake_jobs.enqueue(config.WORK,push=push,**(options or {}))
                self.log('Queued for the background collector; online contributions finish first.')
                while True:
                    result=intake_jobs.read(request,{})
                    self.label=label+' - '+result.get('status','queued')
                    if result.get('status') in ('done','failed'):
                        self.last_ok=result['status']=='done';self.last_result=label+': '+result['status']
                        self.log(self.last_result+'; log: '+str(request.with_suffix('.log')))
                        return result.get('exit_code',1)
                    heartbeat=intake_jobs.read(os.path.join(config.WORK,'shared-collector.json'),{}).get('heartbeat',0)
                    if time.time()-heartbeat>90:
                        self.last_ok=None;self.last_result='Request queued; collector unavailable'
                        self.log(self.last_result+' — it will resume automatically.');return 0
                    time.sleep(1)
            cmd = [console_python(), "-u", PUBLISH] + (["--push"] if push else [])
            self.log("")
            self.log("=" * 70)
            self.log("== %s%s   %s" % (label, "" if push else "  (no push)",
                                       time.strftime("%H:%M:%S")))
            self.log("=" * 70)
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            request=intake_jobs.enqueue(config.WORK,push=push,**(options or {}))
            item=intake_jobs.read(request);item['status']='processing';intake_jobs.write(request,item)
            env['ASCENSION_MANUAL_REQUEST']=str(request)
            try:
                self.proc = subprocess.Popen(
                    cmd, cwd=config.WORK, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                self.last_result = "could not start: %s" % e
                self.last_ok = False
                self.log("!! " + self.last_result)
                return 1
            for line in self.proc.stdout:
                self.log(line.rstrip("\r\n"))
            rc = self.proc.wait()
            item.update(status='done' if rc==0 else 'failed',exit_code=rc);intake_jobs.write(request,item)
            self.proc = None
            took = time.time() - self.started
            self.last_ok = None if rc == BUSY_EXIT else (rc == 0)
            if rc == BUSY_EXIT:
                self.last_result = "%s: waiting for another publisher" % label
            else:
                self.last_result = ("%s: OK in %s" % (label, mmss(took)) if rc == 0
                                    else "%s: FAILED (exit %d) after %s"
                                         % (label, rc, mmss(took)))
            self.log("-- " + self.last_result)
            return rc
        finally:
            self.busy.clear()
            self.label = ""
            self.lock.release()
            self.on_change()

    def stop(self):
        p = self.proc
        if p is None or p.poll() is not None:
            return False
        self.log("~~ stopping the current run at your request")
        try:
            p.terminate()
        except OSError:
            pass
        return True


# -------------------------------------------------------------------- watcher

class Watcher(threading.Thread):
    """Poll the inbox and run the pipeline once a drop has finished landing."""

    daemon = True
    name = "watcher"

    def __init__(self, app):
        threading.Thread.__init__(self)
        self.app = app
        self.handled = {}
        self.pristine = set()
        self.failures = 0
        self.next_attempt = 0.0
        self.stopping = threading.Event()

    def run(self):
        # Whatever is in the inbox at startup counts as already handled, and is
        # never renamed. The pipeline is idempotent so a run would be harmless,
        # but merely starting the app should not push to GitHub, and renaming an
        # already-extracted archive would double-count its corroborations.
        self.handled = inbox_fingerprint()
        self.pristine = set(root_files())
        self.app.log("watching %s" % config.INBOX)
        self.app.log("  repo: %s" % REPO)
        self.app.log("  %d file(s) already present, %d of them at the top level; "
                     "leaving all of them exactly as they are."
                     % (len(self.handled), len(self.pristine)))
        self.app.log("  Drop a new file in, or press Consolidate now to run "
                     "against what is already there.")
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as e:            # never let the loop die
                self.app.log("!! watcher error: %r" % (e,))
            self.stopping.wait(POLL_SECONDS)

    def tick(self):
        if not self.app.state["watch"] or self.app.runner.busy.is_set():
            return
        if time.time() < self.next_attempt:
            return
        now = inbox_fingerprint()
        fresh = [p for p, v in now.items() if self.handled.get(p) != v]
        gone = [p for p in self.handled if p not in now]
        if not fresh:
            if gone:
                # Only removals. Whatever went has already been consolidated and
                # its records are in the store, so republishing would change
                # nothing -- and a five-minute run every time the inbox is tidied
                # is exactly what makes people switch the watcher off.
                self.app.log("inbox shrank by %d file(s); nothing new to "
                             "consolidate" % len(gone))
                self.handled = now
            return
        # Wait for the drop to stop changing. A 200 MB archive still being copied
        # is a truncated archive, and ledgering one is worse than waiting.
        self.app.log("\n%d new or changed file(s); waiting %ds for the drop to "
                     "finish" % (len(fresh), SETTLE_SECONDS))
        for p in sorted(fresh)[:6]:
            self.app.log("   %s" % p)
        time.sleep(SETTLE_SECONDS)
        if inbox_fingerprint() != now:
            self.app.log("still being written; will look again shortly")
            return
        self.consolidate_and_settle_up(gone, inbox_fingerprint())

    def consolidate_and_settle_up(self, gone, processed, push=None,
                                  label="auto-consolidate"):
        if push is None:
            push = self.app.state["push"]
        rc = self.app.runner.run(push, label, {'tidy':self.app.state['tidy'],'rename':self.app.state.get('file',True),'pristine':self.pristine})
        if rc is None:
            return                      # a manual run holds the lock; try later
        if rc == BUSY_EXIT:
            self.next_attempt = time.time() + POLL_SECONDS
            self.app.log("~~ another publisher is active; retrying in %s. "
                         "The submission remains pending." % mmss(POLL_SECONDS))
            return
        if rc != 0:
            # Deliberately NOT recording the fingerprint: this submission has not
            # been published, so the next poll must try it again rather than wait
            # for somebody else's drop to move the fingerprint past it.
            self.failures += 1
            wait = RETRY_BACKOFF[min(self.failures - 1, len(RETRY_BACKOFF) - 1)]
            self.next_attempt = time.time() + wait
            self.app.log("!! that run failed (%d in a row). Retrying in %s. "
                         "Nothing was published, and nothing has been moved."
                         % (self.failures, mmss(wait)))
            return
        self.failures = 0
        self.next_attempt = 0.0
        moved = [] # Publisher files only proven completed inputs while holding its lock.
        after = inbox_fingerprint()
        # Map only this sweep's original input to its archived location. Never
        # mark every file seen after the run as done: late arrivals were not
        # necessarily read by intake, even when they landed inside archive/.
        for original, value in processed.items():
            target = original
            for old, new in moved:
                if original == old or original.startswith(old + os.sep):
                    target = new + original[len(old):]
                    break
            if after.get(target) == value:
                self.handled[target] = value
        for path in list(self.handled):
            if path not in after:
                self.handled.pop(path, None)
        self.app.consumed.clear()
        self.pristine = set(root_files())


# ------------------------------------------------------------------ tray icon

def make_icon(rgb):
    """A disc in the state colour with two feeds merging into one arrow."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 3, size - 3), fill=rgb + (255,))
    w = (255, 255, 255, 235)
    d.line((17, 16, 32, 33), fill=w, width=6)
    d.line((47, 16, 32, 33), fill=w, width=6)
    d.line((32, 30, 32, 50), fill=w, width=6)
    d.polygon([(32, 56), (22, 42), (42, 42)], fill=w)
    return img


COLORS = {"running": (46, 120, 210), "watching": (40, 150, 74),
          "paused": (110, 110, 110), "failed": (190, 60, 50)}


# -------------------------------------------------------------------- the app

class App(object):

    def __init__(self):
        self.state = load_state()
        self.logq = queue.Queue()
        self.cmdq = queue.Queue()
        self.consumed = set()      # inbox paths this run is accounting for
        self.runner = Runner(self.log, self.refresh_soon)
        self.watcher = Watcher(self)
        self.icon = None
        self.icon_state = None
        self.root = None
        self.win = None
        self.text = None
        self.status_var = None
        self.detail_var = None
        self._inbox_at = 0.0
        self._inbox_seen = "counting..."
        self.origin = None          # filled in at startup; see origin_url()

    # ---- logging (safe from any thread) ------------------------------

    def log(self, line):
        """Put a line in the window, and also on disk.

        The window is the only place a run has ever reported itself, which
        is fine while somebody is watching it and useless afterwards: a run
        that failed overnight leaves nothing to read in the morning, and a
        run that is merely slow looks exactly like one that died. The file
        answers both, after the fact.

        It is opened and closed per line rather than held open. This is a
        few lines a minute, so the cost is nothing, and a held handle would
        lose its buffered tail the moment the app was killed -- which is
        exactly the case the log exists for.
        """
        stamp = time.strftime("%H:%M:%S")
        parts = [("" if not p.strip() else "%s  %s" % (stamp, p))
                 for p in str(line).split("\n")]
        for part in parts:
            self.logq.put(part)
        try:
            day = time.strftime("%Y-%m-%d")
            with io.open(LOG_PATH, "a", encoding="utf-8",
                         errors="replace") as f:
                for part in parts:
                    f.write((day + " " + part if part else "") + "\n")
        except Exception:
            pass    # a log that cannot be written must never stop a run

    # ---- filing ------------------------------------------------------

    def prepare_inbox(self, pristine):
        """Make sure nothing in the inbox is about to be silently skipped.

        Two separate hazards, both ending in a submission nobody ever reads:
        a file arriving with a name somebody else already used, and a file that
        has been sitting here all along whose extract directory was claimed by a
        different archive before it.
        """
        # A startup filename is not a permanent exemption: it can be reused
        # later for a different archive. Full-hash extraction reuse keeps this safe.
        names = root_files()
        if names:
            if self.state["file"]:
                for name in names:
                    new = file_arrival(name, self.log)
                    self.consumed.add(new or name)
            else:
                self.log("   (automatic filing is off; names left as they are)")
                self.consumed.update(names)
        if not self.state["file"]:
            return
        try:
            import sweep_inbox
            for old, new in sweep_inbox.fix_collisions(self.log, apply=True):
                self.consumed.discard(os.path.basename(old))
                self.consumed.add(os.path.basename(new))
        except Exception as e:
            # A sweep that cannot run must not stop a consolidation: the worst
            # case is the old behaviour, one submission read late rather than
            # a pipeline that refuses to run at all.
            self.log("!! could not check for name collisions: %r" % (e,))

    # ---- state -------------------------------------------------------

    def publish_state(self):
        if self.runner.busy.is_set():
            return "running"
        if self.runner.last_ok is False:
            return "failed"
        return "watching" if self.state["watch"] else "paused"

    def refresh_soon(self):
        self.cmdq.put(("refresh",))

    WORDS = {
        "watch": ("watching the inbox is ON",
                  "watching the inbox is OFF -- new files will be left alone"),
        "push": ("pushing to GitHub is ON",
                 "pushing to GitHub is OFF -- runs commit locally and stop"),
        "file": ("automatic filing is ON -- arriving archives get a unique name",
                 "automatic filing is OFF -- two submitters sending WDB.zip will "
                 "collide and the second will be skipped"),
        "tidy": ("tidying is ON -- consolidated drops move to archive/YYYY-MM",
                 "tidying is OFF -- the inbox root will keep growing"),
    }

    def toggle(self, key):
        self.state[key] = not self.state[key]
        save_state(self.state)
        self.log(self.WORDS[key][0 if self.state[key] else 1])
        self.refresh_soon()

    def start_run(self, push, label):
        def go():
            self.watcher.consolidate_and_settle_up(
                [], inbox_fingerprint(), push=push, label=label)
        threading.Thread(target=go, daemon=True).start()

    def tidy_now(self):
        if intake_jobs.read(os.path.join(config.WORK,'shared-collector.json'),{}).get('enabled'):
            intake_jobs.enqueue(config.WORK,tidy_only=True)
            heartbeat=intake_jobs.read(os.path.join(config.WORK,'shared-collector.json'),{}).get('heartbeat',0)
            self.log('Collector unavailable; filing request retained for when it resumes.' if time.time()-heartbeat>90 else 'Queued filing of completed inputs; unprocessed files stay in the inbox.')
        else:
            self.log('Background collector is not configured; no inputs moved.')

    # ---- tray --------------------------------------------------------

    def build_tray(self):
        import pystray
        from pystray import MenuItem as Item

        def checked(key):
            return lambda item: self.state[key]

        idle = lambda i: not self.runner.busy.is_set()
        menu = pystray.Menu(
            Item("Open %s" % APP_NAME, lambda: self.cmdq.put(("show",)),
                 default=True),
            pystray.Menu.SEPARATOR,
            Item("Consolidate now",
                 lambda: self.start_run(self.state["push"], "manual run"),
                 enabled=idle),
            Item("Consolidate without pushing",
                 lambda: self.start_run(False, "manual dry run"), enabled=idle),
            Item("Tidy the inbox now", lambda: self.tidy_now(), enabled=idle),
            pystray.Menu.SEPARATOR,
            Item("Watch the inbox", lambda: self.toggle("watch"),
                 checked=checked("watch")),
            Item("Push to GitHub", lambda: self.toggle("push"),
                 checked=checked("push")),
            Item("Rename arriving archives", lambda: self.toggle("file"),
                 checked=checked("file")),
            Item("Tidy up after a run", lambda: self.toggle("tidy"),
                 checked=checked("tidy")),
            pystray.Menu.SEPARATOR,
            Item("Open the inbox folder", lambda: self.open_path(config.INBOX)),
            Item("Open the project on GitHub",
                 lambda: webbrowser.open(UPSTREAM_URL)),
            Item("Open this checkout's own remote",
                 lambda: webbrowser.open(self.origin or UPSTREAM_URL),
                 visible=lambda i: bool(self.origin
                                        and self.origin != UPSTREAM_URL)),
            pystray.Menu.SEPARATOR,
            Item("Quit", lambda: self.cmdq.put(("quit",))),
        )
        self.icon = pystray.Icon("cache_consolidator",
                                 make_icon(COLORS["watching"]), APP_NAME, menu)
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()

    def update_tray(self):
        if self.icon is None:
            return
        st = self.publish_state()
        if st != self.icon_state:
            self.icon_state = st
            try:
                self.icon.icon = make_icon(COLORS[st])
            except Exception:
                pass
        words = {"running": "running: " + (self.runner.label or "pipeline"),
                 "watching": "watching the inbox",
                 "paused": "watching is off",
                 "failed": "last run failed"}
        try:
            self.icon.title = "%s -- %s" % (APP_NAME, words[st])
            self.icon.update_menu()
        except Exception:
            pass

    # ---- window ------------------------------------------------------

    def open_path(self, path):
        try:
            os.startfile(path)
        except OSError as e:
            self.log("!! could not open %s: %s" % (path, e))

    def build_window(self):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.root = tk.Tk()
        self.root.withdraw()

        win = tk.Toplevel(self.root)
        self.win = win
        win.title(APP_NAME)
        win.geometry("1000x640")
        win.minsize(780, 480)
        win.protocol("WM_DELETE_WINDOW", self.hide_window)

        head = tk.Frame(win, padx=12, pady=10)
        head.pack(fill="x")
        self.status_var = tk.StringVar(value="starting")
        self.detail_var = tk.StringVar(value="")
        tk.Label(head, textvariable=self.status_var,
                 font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(head, textvariable=self.detail_var, font=("Segoe UI", 9),
                 fg="#555555", anchor="w", justify="left").pack(fill="x")

        bar = tk.Frame(win, padx=12)
        bar.pack(fill="x")

        def button(text, cmd):
            b = tk.Button(bar, text=text, command=cmd, padx=10, pady=4)
            b.pack(side="left", padx=(0, 6))
            return b

        self.b_run = button("Consolidate now",
                            lambda: self.start_run(self.state["push"],
                                                   "manual run"))
        self.b_dry = button("Dry run (no push)",
                            lambda: self.start_run(False, "manual dry run"))
        self.b_stop = button("Stop this run", self.stop_run)
        self.b_tidy = button("Tidy the inbox", self.tidy_now)
        button("Inbox folder", lambda: self.open_path(config.INBOX))
        button("Repo folder", lambda: self.open_path(REPO))
        button("Project on GitHub", lambda: webbrowser.open(UPSTREAM_URL))

        opts = tk.Frame(win, padx=12, pady=8)
        opts.pack(fill="x")
        self.vars = {}
        for key, label in (("watch", "Watch the inbox"),
                           ("push", "Push to GitHub"),
                           ("file", "Rename arriving archives"),
                           ("tidy", "Tidy up after a run")):
            v = tk.BooleanVar(value=self.state[key])
            self.vars[key] = v
            tk.Checkbutton(opts, text=label, variable=v,
                           command=lambda k=key: self.set_from_ui(k)
                           ).pack(side="left", padx=(0, 14))
        self.v_boot = tk.BooleanVar(value=autostart_enabled())
        tk.Checkbutton(opts, text="Start when I log in", variable=self.v_boot,
                       command=self.set_autostart_from_ui).pack(side="left")

        tabs=ttk.Notebook(win)
        tabs.pack(fill="both",expand=True,padx=12,pady=(0,12))
        manual_tab=ttk.Frame(tabs);tabs.add(manual_tab,text="Manual inbox")
        from contribution_view import ContributionsView
        live_tab=ContributionsView(tabs,config.WORK,self.open_path);tabs.add(live_tab,text="Live contributions")
        tabs.select(live_tab)
        self.text = scrolledtext.ScrolledText(
            manual_tab, wrap="none", font=("Consolas", 9), bg="#101418", fg="#d8dee4",
            insertbackground="#d8dee4", state="disabled", padx=8, pady=6)
        self.text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.text.tag_configure("bad", foreground="#ff8a80")
        self.text.tag_configure("good", foreground="#9ae08a")
        self.text.tag_configure("head", foreground="#8ab4ff")

    def set_from_ui(self, key):
        if self.vars[key].get() != self.state[key]:
            self.toggle(key)

    def set_autostart_from_ui(self):
        self.log(set_autostart(self.v_boot.get()))
        self.v_boot.set(autostart_enabled())

    def stop_run(self):
        if not self.runner.stop():
            self.log("~~ nothing is running")

    def show_window(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def hide_window(self):
        """Closing the window hides it -- the watcher is the point of the app."""
        self.win.withdraw()

    # ---- main-thread pumps -------------------------------------------

    def drain(self):
        lines = []
        try:
            while True:
                lines.append(self.logq.get_nowait())
        except queue.Empty:
            pass
        if lines:
            self.text.configure(state="normal")
            at_end = self.text.yview()[1] > 0.999
            for line in lines:
                body = line[10:] if len(line) > 10 else line
                tag = ""
                if body.startswith("!!") or "FAILED" in body:
                    tag = "bad"
                elif body.startswith(("==", "--")):
                    tag = "head"
                elif ("audit clean" in body or "OK in" in body
                        or body.startswith("pushed ") or "filed " in body):
                    tag = "good"
                self.text.insert("end", line + "\n", tag)
            over = int(self.text.index("end-1c").split(".")[0]) - 6000
            if over > 0:
                self.text.delete("1.0", "%d.0" % over)
            self.text.configure(state="disabled")
            if at_end:
                self.text.see("end")
        try:
            while True:
                cmd = self.cmdq.get_nowait()
                if cmd[0] == "show":
                    self.show_window()
                elif cmd[0] == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.refresh()
        self.root.after(200, self.drain)

    def refresh(self):
        st = self.publish_state()
        if st == "running":
            self.status_var.set("Running: %s   (%s)"
                                % (self.runner.label,
                                   mmss(time.time() - self.runner.started)))
        else:
            self.status_var.set({"watching": "Watching the inbox",
                                 "paused": "Watching is off",
                                 "failed": "Last run failed"}[st])
        nxt = ""
        if self.watcher.next_attempt > time.time():
            nxt = "   retry in %s" % mmss(self.watcher.next_attempt - time.time())
        where = self.origin or "no git remote"
        self.detail_var.set(
            "%s\nlast: %s%s   |   inbox: %s\npush %s -> %s"
            % (config.INBOX, self.runner.last_result, nxt,
               self.inbox_summary(),
               "ON" if self.state["push"] else "off", where))
        running = (st == "running")
        for b in (self.b_run, self.b_dry, self.b_tidy):
            b.configure(state="disabled" if running else "normal")
        self.b_stop.configure(state="normal" if running and self.runner.proc is not None else "disabled")
        for key, v in self.vars.items():
            v.set(self.state[key])
        self.update_tray()

    def inbox_summary(self):
        """Cached: the GUI refreshes five times a second and this walks a tree."""
        if time.time() - self._inbox_at < 5.0:
            return self._inbox_seen
        self._inbox_at = time.time()
        try:
            files=inbox_fingerprint()
            n=len(files);b=sum(value[0] for value in files.values())
            self._inbox_seen = "%d pending file(s), %.0f MB (archive excluded)" % (n,b/1048576.0)
        except OSError:
            self._inbox_seen = "unreadable"
        return self._inbox_seen

    def quit(self):
        self.watcher.stopping.set()
        self.runner.stop()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ---- go ----------------------------------------------------------

    def start(self):
        self.build_window()
        self.log("%s -- %s" % (APP_NAME, time.strftime("%Y-%m-%d %H:%M:%S")))
        self.log("python %s" % sys.version.split()[0])
        self.origin = origin_url()
        self.log("project: %s" % UPSTREAM_URL)
        if self.origin and self.origin != UPSTREAM_URL:
            self.log("this checkout pushes to its own remote instead: %s"
                     % self.origin)
        elif not self.origin:
            self.log("this checkout has no git remote, so nothing will be "
                     "pushed; runs will commit locally and stop there.")
        self.build_tray()
        self.watcher.start()
        self.root.after(200, self.drain)
        self.show_window()
        self.root.mainloop()


def die(msg):
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, APP_NAME, 0x10)
    except Exception:
        pass
    print(msg)
    raise SystemExit(1)


def main():
    if is_elevated():
        die("Cache Consolidator will not run as Administrator.\n\n"
            "This app feeds archives uploaded by strangers to 7-Zip. A crafted "
            "archive can hold a symlink pointing outside the extraction folder, "
            "and the only thing that stopped that here was Windows refusing to "
            "create the link without administrator rights.\n\n"
            "Start it again from a normal, non-elevated session.")
    if not claim_singleton():
        die("Cache Consolidator is already running.\n\n"
            "Look for its icon in the notification area -- you may need to click "
            "the arrow that shows hidden icons.")
    for mod in ("pystray", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            die("%s is missing.\n\nInstall it with:\n\n"
                "    python -m pip install pystray pillow" % mod)
    if not os.path.exists(PUBLISH):
        die("Cannot find publish.py next to this script:\n\n    %s" % PUBLISH)
    os.makedirs(config.INBOX, exist_ok=True)
    App().start()


if __name__ == "__main__":
    main()
