"""Passive read of the Ascension client's per-session key K (obj+0x120).

READ-ONLY. Uses OpenProcess(PROCESS_VM_READ) + ReadProcessMemory only -- no writes,
no code patching, no debugger attach, no watchdog interaction. It reads the client's
OWN computed X25519 session key so the local shim can answer the M2 challenge
(M2 = HMAC-SHA256(K,"OK"), verified at Extensions.dll RVA 0xe5dd0).

Pointer chain (from the verify site + authobj_get 0xe5cd0):
    authobj_get() returns &slot where slot is the static at RVA 0xbdbc04.
    verify does: eax = *slot (the heap object);  K = eax + 0x120 (32 bytes).
  =>  objptr = read_u32(ExtensionsBase + 0xbdbc04)
      K      = read_bytes(objptr + 0x120, 32)
"""
import ctypes
from ctypes import wintypes as w

K_OFFSET_IN_OBJ = 0x120
STATIC_OBJ_SLOT_RVA = 0xbdbc04      # holds the heap object pointer
MODULE_NAME = b"Extensions.dll"

TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE   = 0x8
TH32CS_SNAPMODULE32 = 0x10
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
INVALID = ctypes.c_void_p(-1).value

# Importable off Windows so callers that never take the RPM path (e.g. the AuthGate
# route, where the key is read in-process) can still import this module. Any actual
# call fails loudly rather than at import time.
try:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
except AttributeError:      # non-Windows CPython has no ctypes.WinDLL
    k32 = None


def _require_windows():
    if k32 is None:
        raise OSError("rpm_readk requires Windows; use the in-process key oracle instead")


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", w.DWORD), ("szExeFile", ctypes.c_char * 260)]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("th32ModuleID", w.DWORD),
                ("th32ProcessID", w.DWORD), ("GlblcntUsage", w.DWORD),
                ("ProccntUsage", w.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", w.DWORD), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_char * 256), ("szExePath", ctypes.c_char * 260)]


def find_pids(exe_name=b"Ascension.exe"):
    _require_windows()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID:
        return []
    pids = []
    try:
        pe = PROCESSENTRY32(); pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not k32.Process32First(snap, ctypes.byref(pe)):
            return []
        while True:
            if pe.szExeFile.lower() == exe_name.lower():
                pids.append(pe.th32ProcessID)
            if not k32.Process32Next(snap, ctypes.byref(pe)):
                break
    finally:
        k32.CloseHandle(snap)
    return pids


def module_base(pid, name=MODULE_NAME):
    _require_windows()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID:
        return None
    try:
        me = MODULEENTRY32(); me.dwSize = ctypes.sizeof(MODULEENTRY32)
        if not k32.Module32First(snap, ctypes.byref(me)):
            return None
        while True:
            if me.szModule.lower() == name.lower():
                return me.modBaseAddr
            if not k32.Module32Next(snap, ctypes.byref(me)):
                break
    finally:
        k32.CloseHandle(snap)
    return None


def _open(pid):
    _require_windows()
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    return h or None


def _read(h, addr, size):
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
    if not ok or got.value != size:
        return None
    return bytes(buf.raw)


def read_k_for_pid(pid):
    """Return (base, objptr, K) or None. K is 32 bytes at objptr+0x120."""
    base = module_base(pid)
    if not base:
        return None
    h = _open(pid)
    if not h:
        return None
    try:
        raw = _read(h, base + STATIC_OBJ_SLOT_RVA, 4)
        if raw is None:
            return None
        objptr = int.from_bytes(raw, "little")
        if objptr == 0:
            return (base, 0, None)
        k = _read(h, objptr + K_OFFSET_IN_OBJ, 32)
        return (base, objptr, k)
    finally:
        k32.CloseHandle(h)


def read_k():
    """Scan all Ascension.exe PIDs; return (pid, base, objptr, K) for the live one
    (non-null object + readable K), else the best partial info, else None."""
    best = None
    for pid in find_pids():
        r = read_k_for_pid(pid)
        if r is None:
            continue
        base, objptr, k = r
        if objptr and k is not None:
            return (pid, base, objptr, k)
        best = best or (pid, base, objptr, k)
    return best


if __name__ == "__main__":
    pids = find_pids()
    print("Ascension.exe PIDs:", pids)
    for pid in pids:
        r = read_k_for_pid(pid)
        if r is None:
            print("  pid %d: could not read (no module / no handle)" % pid); continue
        base, objptr, k = r
        print("  pid %d: Extensions.dll base=0x%x  objptr=0x%x  K=%s"
              % (pid, base, objptr, k.hex() if k else "<null obj>"))
