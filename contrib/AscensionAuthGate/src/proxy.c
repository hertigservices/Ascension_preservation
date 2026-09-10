/* AuthGate clean local-bridge build, derived from FirstOni's AscensionAuthGate.
 * Keeps the working credential gate and in-process custom-auth responder.
 * No packet hooks, memory scans, breakpoint experiments, key export, or polling.
 * Source provenance and test/deployment procedure are recorded in ../README.md.
 * This build targets the local Ascension client and bridge on 127.0.0.1:8088.
 */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include "srp6_client.h"

static char g_dir[MAX_PATH], g_proxy_path[MAX_PATH], g_original_path[MAX_PATH];
static wchar_t g_proxy_path_w[MAX_PATH], g_original_path_w[MAX_PATH];
static int g_coa = 1;
static int g_ready = 0;
#define RVA_AUTHOBJ_SLOT 0x00bdbc04
#define OFF_LOGIN_K 0x120
#define GATE_LOGIN_RVA 0xD8A30
/* Runtime offsets default to the compiled (archive-build) values. An optional
 * authgate.profile beside this DLL overrides them for an exe-drifted build whose
 * extension is unchanged (see load_profile + tools/find-offsets.py). The login
 * prologue is still verified before hooking, so a wrong profile fails closed. */
static DWORD g_login_rva    = GATE_LOGIN_RVA;
static DWORD g_authobj_slot = RVA_AUTHOBJ_SLOT;
static DWORD g_off_login_k  = OFF_LOGIN_K;

/* Extensions.dll's world-endpoint allow-list check (see tools/allowlist.py). It builds
 * the stored endpoint string from Ascension.exe 0x00C79C9E, searches a 91-entry list in
 * .rdata, and records "not found":
 *
 *     10A3CACA  cmp  edi, [allowlist_end]
 *     10A3CAD3  sete byte ptr [ebp-0x41]      ; guard = (it == end)
 *
 * Only three loopback endpoints are listed (127.0.0.1:8085/:8087/:8088), so a LAN world
 * address trips the guard and the client dies with ERROR #132 shortly after the world
 * draws. Forcing the guard to 0 accepts any endpoint. Same-length patch:
 *     0F 94 45 BF  sete byte [ebp-0x41]   ->   C6 45 BF 00  mov byte [ebp-0x41], 0
 * Extension-relative like g_authobj_slot, so it is trusted only against the known
 * extension hash and is overridable by authgate.profile. The four bytes are verified
 * before the patch is written, so a wrong offset fails closed and patches nothing.
 * Applied only when authgate.cfg sets allow_remote_world = 1. */
#define EXT_ALLOWLIST_GUARD_RVA 0xA3CAD3
static DWORD g_allowlist_rva = EXT_ALLOWLIST_GUARD_RVA;
#define PROD_B0 51
#define PROD_B1 210
#define PROD_B2 230
#define PROD_B3 10
#define PROD_PORT 3724
#define LOCAL_RELAY_PORT 3725


static char g_aslog[MAX_PATH];
static CRITICAL_SECTION g_ascs;

/* canonical WoW SRP6 modulus, little-endian (wire order); matches WIRE-SPEC section 4 */
static const unsigned char N_LE[32] = {
    0xb7,0x9b,0x3e,0x2a,0x87,0x82,0x3c,0xab,0x8f,0x5e,0xbf,0xbf,0x8e,0xb1,0x01,0x08,
    0x53,0x50,0x06,0x29,0x8b,0x5b,0xad,0xbd,0x5b,0x53,0xe1,0x89,0x5e,0x64,0x4b,0x89
};
/* 10-byte post-M2 tail (their shim's fallback when no live capture is present) */
static const unsigned char M2_TAIL[10] = {
    0x00,0x00,0x80,0x00,0x00,0x00,0x00,0x00,0x01,0x00
};

static void aslog(const char *s)
{
    HANDLE h; DWORD w;
    if (!g_ready) return;
    EnterCriticalSection(&g_ascs);
    h = CreateFileA(g_aslog, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                    NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        SetFilePointer(h, 0, NULL, FILE_END);
        WriteFile(h, s, lstrlenA(s), &w, NULL);
        CloseHandle(h);
    }
    LeaveCriticalSection(&g_ascs);
}

/* Forward only read-only opens of THIS proxy to its genuine companion DLL.
 * Never redirect writes, deletes, creates, or another client's extension. */
static int read_only_open(DWORD access,DWORD creation) {
    const DWORD writes=GENERIC_WRITE|GENERIC_ALL|DELETE|WRITE_DAC|WRITE_OWNER|
        FILE_WRITE_DATA|FILE_APPEND_DATA|FILE_WRITE_EA|FILE_WRITE_ATTRIBUTES;
    return creation==OPEN_EXISTING && !(access&writes);
}
static int own_proxy_a(const char *name,DWORD access,DWORD creation) {
    char full[MAX_PATH];DWORD n;
    if(!name || !read_only_open(access,creation))return 0;
    n=GetFullPathNameA(name,MAX_PATH,full,NULL);
    return n>0 && n<MAX_PATH && !lstrcmpiA(full,g_proxy_path);
}
static int own_proxy_w(const wchar_t *name,DWORD access,DWORD creation) {
    wchar_t full[MAX_PATH];DWORD n;
    if(!name || !read_only_open(access,creation))return 0;
    n=GetFullPathNameW(name,MAX_PATH,full,NULL);
    return n>0 && n<MAX_PATH && !lstrcmpiW(full,g_proxy_path_w);
}

static void *hook_iat(HMODULE mod, const char *dll, const char *fn, void *repl)
{
    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_DATA_DIRECTORY *dir;
    IMAGE_IMPORT_DESCRIPTOR *imp;
    void *orig = NULL;

    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    dir = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir->VirtualAddress) return NULL;
    imp = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir->VirtualAddress);

    for (; imp->Name; imp++) {
        const char *dn = (const char *)(base + imp->Name);
        IMAGE_THUNK_DATA *oft, *ft;
        if (lstrcmpiA(dn, dll) != 0) continue;
        oft = (IMAGE_THUNK_DATA *)(base +
              (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
        ft = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        for (; oft->u1.AddressOfData; oft++, ft++) {
            IMAGE_IMPORT_BY_NAME *ibn;
            if (oft->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            ibn = (IMAGE_IMPORT_BY_NAME *)(base + oft->u1.AddressOfData);
            if (lstrcmpA((char *)ibn->Name, fn) == 0) {
                DWORD old;
                orig = (void *)ft->u1.Function;
                if (VirtualProtect(&ft->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) {
                    ft->u1.Function = (DWORD_PTR)repl;
                    VirtualProtect(&ft->u1.Function, sizeof(void *), old, &old);
                }
            }
        }
    }
    return orig;
}


/* Patch an import slot by matching the RESOLVED address rather than the import
 * name. Under Wine every ws2_32 OriginalFirstThunk entry reads with
 * IMAGE_ORDINAL_FLAG set, so the name-based walk above finds nothing even though
 * the file's imports are by name. Comparing against GetProcAddress works whether
 * the import was by name or ordinal. */
static void *hook_iat_addr(HMODULE mod, const char *dll, void *target, void *repl)
{
    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_DATA_DIRECTORY *dir;
    IMAGE_IMPORT_DESCRIPTOR *imp;
    void *orig = NULL;

    if (!target || dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    dir = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir->VirtualAddress) return NULL;
    imp = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir->VirtualAddress);

    for (; imp->Name; imp++) {
        IMAGE_THUNK_DATA *ft;
        if (dll && lstrcmpiA((const char *)(base + imp->Name), dll) != 0) continue;
        ft = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        for (; ft->u1.Function; ft++) {
            DWORD old;
            if ((void *)ft->u1.Function != target) continue;
            orig = target;
            if (VirtualProtect(&ft->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) {
                ft->u1.Function = (DWORD_PTR)repl;
                VirtualProtect(&ft->u1.Function, sizeof(void *), old, &old);
            }
        }
    }
    return orig;
}

typedef HANDLE (WINAPI *CreateFileA_t)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef HANDLE (WINAPI *CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);

static CreateFileA_t o_CreateFileA;
static CreateFileW_t o_CreateFileW;

static HANDLE WINAPI my_CreateFileA(LPCSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                    DWORD c, DWORD f, HANDLE t)
{
    if (own_proxy_a(n,a,c)) n=g_original_path;
    return o_CreateFileA(n,a,s,sa,c,f,t);
}

static HANDLE WINAPI my_CreateFileW(LPCWSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                    DWORD c, DWORD f, HANDLE t)
{
    if (own_proxy_w(n,a,c)) n=g_original_path_w;
    return o_CreateFileW(n,a,s,sa,c,f,t);
}


typedef int (WSAAPI *connect_t)(SOCKET, const struct sockaddr *, int);
typedef int (WSAAPI *WSAConnect_t)(SOCKET, const struct sockaddr *, int,
                                 LPWSABUF, LPWSABUF, LPQOS, LPQOS);
static connect_t o_connect_real;
static WSAConnect_t o_wsaconnect_real;

static int is_prod_auth(const struct sockaddr *name, int namelen)
{
    const struct sockaddr_in *sin = (const struct sockaddr_in *)name;
    const unsigned char *a;
    if (!name || namelen < (int)sizeof(struct sockaddr_in)) return 0;
    if (sin->sin_family != AF_INET) return 0;
    a = (const unsigned char *)&sin->sin_addr;
    return a[0] == PROD_B0 && a[1] == PROD_B1 && a[2] == PROD_B2 && a[3] == PROD_B3
        && sin->sin_port == htons(PROD_PORT);
}

/* build the redirected sockaddr (127.0.0.1:LOCAL_RELAY_PORT) into *out */
static void make_local(struct sockaddr_in *out, const struct sockaddr *name)
{
    unsigned char *a;
    memcpy(out, name, sizeof(struct sockaddr_in));
    a = (unsigned char *)&out->sin_addr;
    a[0] = 127; a[1] = 0; a[2] = 0; a[3] = 1;
    out->sin_port = htons(LOCAL_RELAY_PORT);
}


static int WSAAPI my_connect_inl(SOCKET s, const struct sockaddr *name, int namelen)
{
    if (is_prod_auth(name, namelen)) {
        struct sockaddr_in redir;
        int r;
        make_local(&redir, name);
        aslog("REDIR connect 51.210.230.10:3724 -> 127.0.0.1:3725 (relay)\r\n");
        r = o_connect_real(s, (const struct sockaddr *)&redir, sizeof(redir));
        return r;
    }
    return o_connect_real(s, name, namelen);
}

static int WSAAPI my_wsaconnect_inl(SOCKET s, const struct sockaddr *name, int namelen,
                                    LPWSABUF cd, LPWSABUF ced, LPQOS sq, LPQOS gq)
{
    if (is_prod_auth(name, namelen)) {
        struct sockaddr_in redir;
        make_local(&redir, name);
        aslog("REDIR WSAConnect 51.210.230.10:3724 -> 127.0.0.1:3725 (relay)\r\n");
        return o_wsaconnect_real(s, (const struct sockaddr *)&redir, sizeof(redir),
                                 cd, ced, sq, gq);
    }
    return o_wsaconnect_real(s, name, namelen, cd, ced, sq, gq);
}


static void *install_inline_hook(void *target, void *repl)
{
    unsigned char *t = (unsigned char *)target;
    static const unsigned char expect[5] = { 0x8B, 0xFF, 0x55, 0x8B, 0xEC };
    unsigned char *tramp;
    DWORD old;
    char b[80];
    int i;
    if (!t) return NULL;
    wsprintfA(b, "prologue %02x %02x %02x %02x %02x %02x %02x %02x\r\n",
              t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7]);
    aslog(b);
    for (i = 0; i < 5; i++)
        if (t[i] != expect[i]) { aslog("  -> unexpected prologue, NOT hooking\r\n"); return NULL; }
    tramp = (unsigned char *)VirtualAlloc(NULL, 16, MEM_COMMIT | MEM_RESERVE,
                                          PAGE_EXECUTE_READWRITE);
    if (!tramp) return NULL;
    memcpy(tramp, t, 5);                                   /* stolen bytes */
    tramp[5] = 0xE9;                                       /* jmp back to target+5 */
    *(int *)(tramp + 6) = (int)((t + 5) - (tramp + 10));
    if (!VirtualProtect(t, 5, PAGE_EXECUTE_READWRITE, &old)) {
        VirtualFree(tramp, 0, MEM_RELEASE); return NULL;
    }
    t[0] = 0xE9;                                           /* jmp to repl */
    *(int *)(t + 1) = (int)((unsigned char *)repl - (t + 5));
    VirtualProtect(t, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), t, 5);
    return tramp;
}


static FARPROC g_realFn = NULL;

static void ensure_real(void)
{
    HMODULE m;
    if (g_realFn) return;

    m = LoadLibraryA(g_original_path);
    if (m) g_realFn = GetProcAddress(m, "ClientExtensionsDummy");
    aslog(g_realFn ? "[startup] original extension loaded\r\n" : "[startup] original extension load FAILED\r\n");
}

__declspec(naked) void ClientExtensionsDummy(void)
{
    __asm {
        pushad
        call ensure_real
        popad
        cmp dword ptr [g_realFn], 0
        je no_real
        jmp dword ptr [g_realFn]
    no_real:
        ret
    }
}


/* SEH-guarded reads: the auth object is a heap pointer that may be null or in
 * flux during construction. Never let a bad read take the client down. */
#ifdef _MSC_VER
static int kr_read_u32(const void *addr, DWORD *out)
{
    __try { *out = *(volatile DWORD *)addr; return 1; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
}
#else
/* Cross-build port (clang/GCC, no MSVC SEH): validate the page before reading
 * instead of catching the fault. Same intent -- a bad pointer must not take the
 * client down -- reached by checking rather than trapping. */
static int kr_readable(const void *p, SIZE_T n)
{
    MEMORY_BASIC_INFORMATION mbi;
    if (!p || !VirtualQuery(p, &mbi, sizeof(mbi))) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    return (SIZE_T)((BYTE *)mbi.BaseAddress + mbi.RegionSize - (BYTE *)p) >= n;
}
static int kr_read_u32(const void *addr, DWORD *out)
{
    if (!kr_readable(addr, sizeof(DWORD))) return 0;
    *out = *(volatile DWORD *)addr; return 1;
}
#endif
static int kr_read_bytes(const void *addr, unsigned char *dst, int n)
{
#ifdef _MSC_VER
    __try {
        int i;
        for (i = 0; i < n; i++) dst[i] = ((volatile const unsigned char *)addr)[i];
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
#else
    int i;
    if (!kr_readable(addr, (SIZE_T)n)) return 0;
    for (i = 0; i < n; i++) dst[i] = ((volatile const unsigned char *)addr)[i];
    return 1;
#endif
}
static int kr_all_zero(const unsigned char *p, int n)
{
    int i; for (i = 0; i < n; i++) if (p[i]) return 0; return 1;
}


/* read the login K in-process via the confirmed pointer chain (guarded reads only; no polling or logging) */
static int as_read_login_K(unsigned char K[32])
{
    HMODULE eo = GetModuleHandleA("Extensions_orig.dll");
    DWORD obj = 0;
    if (!eo) return 0;
    if (!kr_read_u32((BYTE *)eo + g_authobj_slot, &obj) || !obj) return 0;
    return kr_read_bytes((const void *)(obj + g_off_login_k), K, 32) && !kr_all_zero(K, 32);
}

static int as_hmac_sha256(const unsigned char *key, ULONG keylen,
                          const unsigned char *msg, ULONG msglen, unsigned char out[32])
{
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE hh = NULL;
    LONG st;
    int ok = 0;
    st = BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL,
                                     BCRYPT_ALG_HANDLE_HMAC_FLAG);
    if (st == 0) {
        st = BCryptCreateHash(alg, &hh, NULL, 0, (PUCHAR)key, keylen, 0);
        if (st == 0) st = BCryptHashData(hh, (PUCHAR)msg, msglen, 0);
        if (st == 0) st = BCryptFinishHash(hh, out, 32, 0);
        ok = (st == 0);
    }
    if (hh) BCryptDestroyHash(hh);
    if (alg) BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

static int as_rand(unsigned char *p, ULONG n)
{
    return BCryptGenRandom(NULL, p, n, BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0;
}


static int as_build_challenge(unsigned char out[119])
{
    unsigned char B[32], salt[32], vchal[16];
    if (!as_rand(B,32) || !as_rand(salt,32) || !as_rand(vchal,16)) return 0;
    out[0] = 0; out[1] = 0; out[2] = 0;              /* cmd, error, result=SUCCESS */
    memcpy(out + 3, B, 32);                          /* [3:35]  server "pubkey" (throwaway) */
    out[35] = 0x01; out[36] = 0x07; out[37] = 0x20;  /* g_len, g, N_len */
    memcpy(out + 38, N_LE, 32);                      /* [38:70] canonical modulus (LE) */
    memcpy(out + 70, salt, 32);                      /* [70:102] salt */
    memcpy(out + 102, vchal, 16);                    /* [102:118] version_challenge */
    out[118] = 0x00;                                 /* security_flags */
    return 1;
}

/* ---- PATH 1 credential GATE: capture login(user,pass) @ RVA 0xD8A30 and validate
 * it with a real SRP6 login to the authserver, WITHOUT leaving the client's custom
 * (bypass) path. VALID -> call the original login (custom auth proceeds to char-select
 * via the in-process AUTHSRV); REJECTED/UNREACHABLE -> block. Login RVA is g_login_rva
 * (compiled default GATE_LOGIN_RVA, overridable by authgate.profile). */
#define GATE_AUTH_IP   0x0100007Fu     /* 127.0.0.1 authserver; authgate.cfg auth_ip overrides */

/* ---- optional local config: authgate.cfg beside the DLL ------------------------
 * Lets a deployment point the gate and the realm list at another machine without a
 * rebuild. Absent or unparsable -> the compiled defaults above are used unchanged.
 *
 *     auth_ip = 192.168.1.50        # authserver to validate credentials against
 *     realm   = 192.168.1.50:8088   # address handed to the client in the realm list
 *
 * Setting auth_ip to a non-loopback address deliberately relaxes the loopback-only
 * guard in srp6_client.h, and only for that one address. Passwords then cross the
 * LAN inside SRP6 (never in clear), but do this only on a network you trust. */
static unsigned long g_auth_ip = GATE_AUTH_IP;
static char g_realm_addr[64] = "127.0.0.1:8088";
static int g_allow_remote_world = 0;

static void cfg_trim(char *s)
{
    char *e; while (*s == ' ' || *s == '\t') memmove(s, s + 1, lstrlenA(s));
    e = s + lstrlenA(s);
    while (e > s && (e[-1]==' '||e[-1]=='\t'||e[-1]=='\r'||e[-1]=='\n')) *--e = 0;
}

static void load_config(void)
{
    char path[MAX_PATH], buf[512], line[160], b[200];
    HANDLE h; DWORD got = 0; int i = 0, n;

    if (lstrlenA(g_dir) + (int)sizeof("authgate.cfg") > MAX_PATH) {
        aslog("[cfg] client path too long for authgate.cfg; using compiled defaults\r\n");
        return;
    }
    lstrcpynA(path, g_dir, MAX_PATH);
    lstrcatA(path, "authgate.cfg");
    h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) { aslog("[cfg] no authgate.cfg; using compiled defaults\r\n"); return; }
    ReadFile(h, buf, sizeof(buf) - 1, &got, NULL);
    CloseHandle(h);
    buf[got] = 0;

    while (i < (int)got) {
        char *eq;
        n = 0;
        while (i < (int)got && buf[i] != '\n' && n < (int)sizeof(line) - 1) line[n++] = buf[i++];
        if (i < (int)got) i++;
        line[n] = 0;
        cfg_trim(line);
        if (!line[0] || line[0] == '#' || line[0] == ';') continue;
        eq = line; while (*eq && *eq != '=') eq++;
        if (!*eq) continue;
        *eq++ = 0;
        cfg_trim(line); cfg_trim(eq);

        if (!lstrcmpiA(line, "auth_ip")) {
            unsigned long a = inet_addr(eq);
            if (a == INADDR_NONE) { wsprintfA(b, "[cfg] bad auth_ip '%s'; keeping default\r\n", eq); aslog(b); }
            else {
                g_auth_ip = a;
                g_srp6_allowed_ip = a;      /* permit exactly this one endpoint */
                wsprintfA(b, "[cfg] auth_ip = %s\r\n", eq); aslog(b);
            }
        } else if (!lstrcmpiA(line, "allow_remote_world")) {
            g_allow_remote_world = (eq[0] == '1' || eq[0] == 'y' || eq[0] == 'Y' ||
                                    eq[0] == 't' || eq[0] == 'T');
            wsprintfA(b, "[cfg] allow_remote_world = %d\r\n", g_allow_remote_world);
            aslog(b);
        } else if (!lstrcmpiA(line, "realm")) {
            if (lstrlenA(eq) > 0 && lstrlenA(eq) < (int)sizeof(g_realm_addr)) {
                lstrcpynA(g_realm_addr, eq, sizeof(g_realm_addr));
                wsprintfA(b, "[cfg] realm = %s\r\n", g_realm_addr); aslog(b);
            }
        }
    }
}
typedef int (__cdecl *gate_login_t)(const char *, const char *);
static gate_login_t g_orig_login;
static volatile LONG g_gate_deny = 1;   /* set when the last login was rejected -> AUTHSRV fails it */
static int __cdecl gate_my_login(const char *user, const char *pass)
{
    int v; char b[128];
    if (!user || !pass) {
        InterlockedExchange(&g_gate_deny, 1);
        return g_orig_login(user, pass);
    }
    v = srp6_validate(user, pass, g_auth_ip);
    wsprintfA(b, "[gate] %s\r\n",
              v == 1 ? "VALID (proceed)" : v == 0 ? "REJECTED (auth-failed shown)" : "UNREACHABLE (auth-failed shown)");
    aslog(b);
    /* Always run the client's own login flow so it transitions state; if denied, the
     * in-process AUTHSRV returns an error and the client shows "authentication failed". */
    InterlockedExchange(&g_gate_deny, v == 1 ? 0 : 1);
    return g_orig_login(user, pass);
}
static void gate_install(void)
{
    BYTE *t = (BYTE *)GetModuleHandleA(NULL) + g_login_rva;
    BYTE *tr; DWORD o; char b[96]; unsigned char sv[32];
    if (!srp6_selftest(sv)) { aslog("[gate] SRP6 self-test FAIL\r\n"); return; }
    aslog("[gate] SRP6 self-test PASS\r\n");
    if (!(t[0]==0x55 && t[1]==0x8B && t[2]==0xEC && t[3]==0x80 && t[4]==0x3D)) {
        wsprintfA(b, "[gate] login prologue mismatch %02x %02x %02x %02x %02x -- not installed\r\n",
                  t[0],t[1],t[2],t[3],t[4]); aslog(b); return;
    }
    tr = (BYTE *)VirtualAlloc(NULL, 32, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!tr) { aslog("[gate] trampoline allocation failed\r\n"); return; }
    memcpy(tr, t, 10); tr[10] = 0xE9; *(DWORD *)(tr + 11) = (DWORD)(t + 10) - (DWORD)(tr + 15);
    g_orig_login = (gate_login_t)tr;
    if (!VirtualProtect(t, 5, PAGE_EXECUTE_READWRITE, &o)) {
        VirtualFree(tr, 0, MEM_RELEASE); g_orig_login = NULL;
        aslog("[gate] login hook protection failed\r\n"); return;
    }
    t[0] = 0xE9; *(DWORD *)(t + 1) = (DWORD)gate_my_login - (DWORD)(t + 5);
    VirtualProtect(t, 5, o, &o); FlushInstructionCache(GetCurrentProcess(), t, 10);
    aslog("[gate] login-capture + SRP6 gate installed (RVA 0xD8A30)\r\n");
}

static int as_recvn(SOCKET s, unsigned char *buf, int n)
{
    int got = 0, r;
    while (got < n) {
        r = recv(s, (char *)buf + got, n - got, 0);
        if (r <= 0) return got;
        got += r;
    }
    return got;
}

/* send() can return a short write even on loopback. */
static int as_sendn(SOCKET s, const unsigned char *buf, int n)
{
    int sent = 0, r;
    while (sent < n) {
        r = send(s, (const char *)buf + sent, n - sent, 0);
        if (r <= 0) return sent;
        sent += r;
    }
    return sent;
}

static void rl_str(unsigned char *buf, int *pos, const char *s)
{
    int n = lstrlenA(s) + 1;                 /* include the NUL */
    memcpy(buf + *pos, s, n); *pos += n;
}

/* Minimal two-block realm list (WIRE-SPEC section 7): one connectable realm at
 * 127.0.0.1:8088 (local Ascension bridge) + its category-27 metadata twin so
 * the CoA realm screen will display and select it. Auth-channel framing: LE size. */
/* Neutralise the world-endpoint allow-list so a non-loopback realm address works.
 * Only meaningful for multi-machine play; guarded on the exact original bytes so a
 * different build logs a mismatch instead of being corrupted. */
static void patch_world_allowlist(void)
{
    HMODULE eo = GetModuleHandleA("Extensions_orig.dll");
    BYTE *t; DWORD old; char b[128];
    static const BYTE want[4] = {0x0F,0x94,0x45,0xBF};   /* sete byte [ebp-0x41] */
    static const BYTE repl[4] = {0xC6,0x45,0xBF,0x00};   /* mov  byte [ebp-0x41],0 */
    if (!g_allow_remote_world) return;
    if (!eo) { aslog("[allowlist] Extensions_orig not loaded\r\n"); return; }
    t = (BYTE *)eo + g_allowlist_rva;
    if (!memcmp(t, repl, 4)) { aslog("[allowlist] already patched\r\n"); return; }
    if (memcmp(t, want, 4)) {
        wsprintfA(b, "[allowlist] guard bytes are %02X %02X %02X %02X, not 0F 94 45 BF -- NOT patching\r\n",
                  t[0], t[1], t[2], t[3]);
        aslog(b); return;
    }
    if (!VirtualProtect(t, 4, PAGE_EXECUTE_READWRITE, &old)) {
        aslog("[allowlist] guard not writable\r\n"); return;
    }
    memcpy(t, repl, 4);
    VirtualProtect(t, 4, old, &old);
    FlushInstructionCache(GetCurrentProcess(), t, 4);
    aslog("[allowlist] world-endpoint allow-list neutralised (remote world address permitted)\r\n");
}

static int build_realmlist(unsigned char *out)
{
    unsigned char pl[512];
    int p = 0, op = 0;
    unsigned int z = 0;
    unsigned short nr = 2, sz;
    float pop = 0.0f;

    memcpy(pl + p, &z, 4); p += 4;           /* uint32 unused */
    memcpy(pl + p, &nr, 2); p += 2;          /* uint16 numRealms */

    /* record 0: connectable */
    pl[p++] = 0;                             /* icon */
    pl[p++] = 0;                             /* lock */
    pl[p++] = 0;                             /* flags (no SPECIFY_BUILD -> no version gate) */
    rl_str(pl, &p, g_coa ? "Vol'jin - Conquest of Azeroth" : "Area 52 - Free-Pick");
    rl_str(pl, &p, g_realm_addr);            /* allow-listed loopback port, or authgate.cfg "realm" */
    memcpy(pl + p, &pop, 4); p += 4;         /* population f32 */
    pl[p++] = 0;                             /* numChars */
    pl[p++] = 1;                             /* category */
    pl[p++] = 1;                             /* realmID */

    /* record 1: metadata twin (Name!expansion!gamemode!image!unlocked!page!index!spell) */
    pl[p++] = 0; pl[p++] = 0; pl[p++] = 0;
    rl_str(pl, &p, g_coa ? "Vol'jin - Conquest of Azeroth!0!11!Voljin!true!1!4!13977864" : "Area 52 - Free-Pick!1!0!Area52!true!1!6!13977862");
    rl_str(pl, &p, "");                      /* empty address */
    memcpy(pl + p, &pop, 4); p += 4;
    pl[p++] = 0;                             /* numChars */
    pl[p++] = 27;                            /* category (metadata) */
    pl[p++] = 1;                             /* realmID */

    pl[p++] = 0x10; pl[p++] = 0x00;          /* trailer */

    out[op++] = 0x10;                        /* cmd */
    sz = (unsigned short)p;
    memcpy(out + op, &sz, 2); op += 2;       /* uint16 size (LE) */
    memcpy(out + op, pl, p); op += p;
    return op;
}


static void as_handle(SOCKET c)
{
    unsigned char hdr[4], body[1024], proof[256], K[32], M2[32], resp[44], nxt[8], chal[119];
    int size, pr;
    char b[128];

    if (as_recvn(c, hdr, 4) != 4) { aslog("  short hello header\r\n"); return; }
    if (hdr[0] != 0x00) {
        wsprintfA(b, "  first opcode 0x%02x, not 0x00 -- ignoring\r\n", hdr[0]); aslog(b); return;
    }
    size = hdr[2] | (hdr[3] << 8);
    if (size <= 0 || size > (int)sizeof(body)) {
        aslog("  invalid hello length\r\n"); return;
    }
    if (as_recvn(c, body, size) != size) {
        aslog("  short hello body\r\n"); return;
    }
    wsprintfA(b, "[%lu] hello 0x00 body=%dB -> sending challenge\r\n", GetTickCount(), size);
    aslog(b);

    if (!as_build_challenge(chal)) { aslog("  system RNG failed; refusing authentication\r\n"); return; }
    if (as_sendn(c, chal, 119) != 119) { aslog("  challenge send failed\r\n"); return; }

    /* block for the client's 0x01 proof. Once it arrives, K is guaranteed set:
     * the client had to derive K from our challenge to build the proof. */
    /* This client sends a fixed 75-byte proof. TCP may split it anywhere. */
    pr = as_recvn(c, proof, 75);
    if (pr != 75 || proof[0] != 0x01) {
        aslog("  incomplete or invalid proof\r\n"); return;
    }
    wsprintfA(b, "[%lu] proof recv %dB (opcode 0x%02x)\r\n", GetTickCount(), pr, proof[0]); aslog(b);

    /* GATE denial: the SRP6 gate rejected these creds -> return a proof-response with a
     * non-zero error so the client shows its normal "authentication failed" dialog
     * (instead of the login button appearing to do nothing). */
    if (InterlockedCompareExchange(&g_gate_deny, 0, 0) != 0) {
        ZeroMemory(resp, sizeof(resp));
        resp[0] = 0x01; resp[1] = 0x04;             /* cmd, error != 0 (auth failed) */
        as_sendn(c, resp, 44);
        aslog("[gate] denied -> sent AUTH_FAILED proof response (client shows failure)\r\n");
        return;
    }

    if (!as_read_login_K(K)) { aslog("  !! could not read login K in-process\r\n"); return; }
    if (!as_hmac_sha256(K, 32, (const unsigned char *)"OK", 2, M2)) {
        SecureZeroMemory(K,sizeof(K));
        aslog("  !! HMAC-SHA256 failed\r\n"); return;
    }

    SecureZeroMemory(K,sizeof(K));
    resp[0] = 0x01; resp[1] = 0x00;
    memcpy(resp + 2, M2, 32);
    memcpy(resp + 34, M2_TAIL, 10);
    SecureZeroMemory(M2,sizeof(M2));
    if (as_sendn(c, resp, 44) != 44) { aslog("  proof-response send failed\r\n"); return; }
    aslog("[*] 0x01 proof response sent (44B)\r\n");

    /* The bridge stages its own world key; no client key file or DB write. */

    /* serve the realm list on every 0x10 (the client polls ~1/s on the realm screen).
     * The first 0x10 arriving already means M2 was accepted. */
    {
        int first = 1;
        for (;;) {
            unsigned char rest[8], rl[600];
            int r = recv(c, (char *)nxt, 1, 0);
            if (r <= 0) { aslog("  client closed auth channel\r\n"); break; }
            if (nxt[0] == 0x10) {
                int rln;
                if (as_recvn(c, rest, 4) != 4) break; /* complete the 5-byte request */
                if (first) {
                    aslog("***** M2 ACCEPTED -- client sent 0x10 (realm-list request) *****\r\n");
                    first = 0;
                }
                rln = build_realmlist(rl);
                if (as_sendn(c, rl, rln) != rln) break;
                /* Routine realm polls do not write a log line. */
            } else {
                wsprintfA(b, "  post-auth opcode 0x%02x (len %d) -- ignoring\r\n", nxt[0], r);
                aslog(b);
            }
        }
    }
}

static DWORD WINAPI authsrv_thread(LPVOID unused)
{
    WSADATA wsa;
    SOCKET srv;
    struct sockaddr_in sa;
    int yes = 1;
    char b[96];
    (void)unused;

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { aslog("WSAStartup failed\r\n"); return 0; }
    srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv == INVALID_SOCKET) { aslog("socket() failed\r\n"); return 0; }
    /* Windows SO_REUSEADDR allowed a stale tap to steal auth connections.
     * A clean client must be the sole owner of its private auth listener. */
    if (setsockopt(srv, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
                   (const char *)&yes, sizeof(yes)) != 0) {
        aslog("exclusive listener setup failed\r\n"); closesocket(srv); return 0;
    }
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons(LOCAL_RELAY_PORT);
    sa.sin_addr.s_addr = htonl(0x7F000001);          /* 127.0.0.1 */
    if (bind(srv, (struct sockaddr *)&sa, sizeof(sa)) != 0) {
        wsprintfA(b, "!! bind 127.0.0.1:%d failed (err %d) -- is another responder running?\r\n",
                  LOCAL_RELAY_PORT, WSAGetLastError());
        aslog(b); closesocket(srv); return 0;
    }
    if (listen(srv, 4) != 0) { aslog("listen() failed\r\n"); closesocket(srv); return 0; }
    wsprintfA(b, "=== AUTHSRV listening on 127.0.0.1:%d (in-process) ===\r\n", LOCAL_RELAY_PORT);
    aslog(b);

    for (;;) {
        SOCKET c = accept(srv, NULL, NULL);
        if (c == INVALID_SOCKET) { Sleep(50); continue; }
        { DWORD timeout=10000;
          setsockopt(c,SOL_SOCKET,SO_RCVTIMEO,(const char*)&timeout,sizeof(timeout));
          setsockopt(c,SOL_SOCKET,SO_SNDTIMEO,(const char*)&timeout,sizeof(timeout)); }
        aslog("[+] client connected\r\n");
#ifdef _MSC_VER
        __try { as_handle(c); }
        __except (EXCEPTION_EXECUTE_HANDLER) { aslog("  handler exception\r\n"); }
#else
        /* No SEH on the cross-build. The faulting reads this guarded are already
         * page-validated above, so the residual risk is the socket path itself. */
        as_handle(c);
#endif
        closesocket(c);
    }
}

/* Parse an unsigned value: "0x.." hex or decimal. Advances nothing; bounded by NUL. */
static DWORD prof_num(const char *s)
{
    DWORD v = 0; int hex = 0;
    while (*s == ' ' || *s == '\t') s++;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { hex = 1; s += 2; }
    for (; *s; s++) {
        char c = *s; DWORD d;
        if (c >= '0' && c <= '9') d = (DWORD)(c - '0');
        else if (hex && c >= 'a' && c <= 'f') d = (DWORD)(c - 'a' + 10);
        else if (hex && c >= 'A' && c <= 'F') d = (DWORD)(c - 'A' + 10);
        else break;
        v = v * (hex ? 16 : 10) + d;
    }
    return v;
}

/* Find "key" in buf (distinct keys only), skip to '=', return its value via prof_num.
 * Returns 1 on hit. buf is NUL-terminated. */
static int prof_get(const char *buf, const char *key, DWORD *out)
{
    int kl = lstrlenA(key);
    const char *p = buf;
    for (; *p; p++) {
        int i = 0;
        while (i < kl && p[i] && p[i] == key[i]) i++;
        if (i == kl) {
            const char *q = p + kl;
            while (*q == ' ' || *q == '\t') q++;
            if (*q == '=') { *out = prof_num(q + 1); return 1; }
        }
    }
    return 0;
}

/* Optional runtime offset override for an exe-drifted build (see find-offsets.py).
 * Reads "authgate.profile" (key=value lines) beside this DLL; absent -> compiled
 * defaults. The login prologue check in gate_install remains the fail-closed guard. */
static void load_profile(void)
{
    char path[MAX_PATH], buf[1024], b[128]; HANDLE f; DWORD n = 0, v;
    if (lstrlenA(g_dir) + (int)sizeof("authgate.profile") > MAX_PATH) return;
    lstrcpynA(path, g_dir, MAX_PATH); lstrcatA(path, "authgate.profile");
    f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) {
        aslog("[startup] no authgate.profile; using compiled offsets\r\n"); return;
    }
    if (!ReadFile(f, buf, sizeof(buf) - 1, &n, NULL)) n = 0;
    CloseHandle(f); buf[n] = 0;
    if (prof_get(buf, "login_rva", &v))        g_login_rva = v;
    if (prof_get(buf, "authobj_slot_rva", &v)) g_authobj_slot = v;
    if (prof_get(buf, "off_login_k", &v))      g_off_login_k = v;
    if (prof_get(buf, "allowlist_rva", &v))    g_allowlist_rva = v;
    wsprintfA(b, "[startup] profile applied: login_rva=0x%X slot=0x%X off_k=0x%X "
              "allowlist=0x%X\r\n",
              g_login_rva, g_authobj_slot, g_off_login_k, g_allowlist_rva);
    aslog(b);
}

/* Initialize the status log before any startup routine can use it. */
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        char *p, *slash, mode[16]; DWORD pathlen;
        HMODULE cli, ws;
        HANDLE thread, log;
        void *pconnect, *pwsaconnect;
        DisableThreadLibraryCalls(h);
        InitializeCriticalSection(&g_ascs);
        pathlen=GetModuleFileNameA(h,g_dir,MAX_PATH);
        if (!pathlen || pathlen>=MAX_PATH) return FALSE;
        lstrcpyA(g_proxy_path,g_dir);
        if (!GetModuleFileNameW(h,g_proxy_path_w,MAX_PATH))return FALSE;
        slash = g_dir;
        for (p = g_dir; *p; ++p) if (*p == '\\' || *p == '/') slash = p;
        *(slash + 1) = 0;
        if (lstrlenA(g_dir)+(int)sizeof("Extensions_orig.dll")>MAX_PATH)return FALSE;
        lstrcpyA(g_original_path,g_dir);lstrcatA(g_original_path,"Extensions_orig.dll");
        if (!MultiByteToWideChar(CP_ACP,0,g_original_path,-1,g_original_path_w,MAX_PATH))return FALSE;
        if (GetEnvironmentVariableA("ASCENSION_AUTHGATE_MODE",mode,sizeof(mode))) {
            if (!lstrcmpA(mode,"ascension"))g_coa=0;
            else if (lstrcmpA(mode,"coa"))return FALSE;
        }
        lstrcpynA(g_aslog, g_dir, MAX_PATH);
        lstrcatA(g_aslog, "proxy_auth.log");
        log = CreateFileA(g_aslog, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                          CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (log != INVALID_HANDLE_VALUE) CloseHandle(log);
        g_ready = 1;
        aslog("=== AuthGate CLEAN: status logging only; no packet/key capture ===\r\n");
        load_profile();   /* optional per-build offset override; else compiled defaults */
        load_config();    /* optional deployment config: auth_ip / realm / allow_remote_world */
        cli = GetModuleHandleA(NULL);
        o_CreateFileA = (CreateFileA_t)hook_iat(cli, "kernel32.dll", "CreateFileA", (void *)my_CreateFileA);
        o_CreateFileW = (CreateFileW_t)hook_iat(cli, "kernel32.dll", "CreateFileW", (void *)my_CreateFileW);
        if (!o_CreateFileA) o_CreateFileA = CreateFileA;
        if (!o_CreateFileW) o_CreateFileW = CreateFileW;
        ws = GetModuleHandleA("ws2_32.dll");
        if (!ws) ws = LoadLibraryA("ws2_32.dll");
        pconnect = ws ? (void *)GetProcAddress(ws, "connect") : NULL;
        pwsaconnect = ws ? (void *)GetProcAddress(ws, "WSAConnect") : NULL;
        o_connect_real = (connect_t)install_inline_hook(pconnect, (void *)my_connect_inl);
        o_wsaconnect_real = (WSAConnect_t)install_inline_hook(pwsaconnect, (void *)my_wsaconnect_inl);
        /* Fall back to the import table when the prologue is not the Microsoft
         * hotpatch stub. Wine's ws2_32 is GCC-built (55 89 e5 ...), so the inline
         * hook correctly refuses it and the redirect would otherwise be lost.
         * Patching the thunk is compiler-agnostic and needs no stolen bytes.
         * This client imports ws2_32 by name and calls it only from the exe. */
        if (!o_connect_real)
            o_connect_real = (connect_t)hook_iat_addr(cli, "ws2_32.dll", pconnect,
                                                      (void *)my_connect_inl);
        if (!o_wsaconnect_real)
            o_wsaconnect_real = (WSAConnect_t)hook_iat_addr(cli, "ws2_32.dll", pwsaconnect,
                                                            (void *)my_wsaconnect_inl);
        /* WSAConnect is absent from some builds' imports; connect is what matters. */
        aslog(o_connect_real ? "[startup] local auth redirects installed\r\n"
                             : "[startup] auth redirect hook FAILED\r\n");
        /* Preserve the proven original-DLL load timing; defer loader redesign. */
        ensure_real();
        patch_world_allowlist();      /* after the genuine extension is loaded */
        gate_install();
        thread = CreateThread(NULL, 0, authsrv_thread, NULL, 0, NULL);
        if (thread) CloseHandle(thread);
        else aslog("[startup] auth server thread failed\r\n");
    }
    return TRUE;
}
