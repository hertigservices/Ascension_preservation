/* srp6_client.h — minimal SRP6 client for the AscensionRebirth credential GATE.
 * Validates (username,password) by performing a real GRUNT/SRP6 login against a
 * stock AzerothCore authserver and verifying its complete SRP server proof.
 * This gate is restricted to loopback. Big-number modexp
 * is schoolbook (correctness over speed; a login is a handful of modexps).
 * All bignums are LITTLE-ENDIAN limb arrays, matching WoW/AzerothCore's BigNumber.
 */
#ifndef SRP6_CLIENT_H
#define SRP6_CLIENT_H
#include <winsock2.h>
#include <windows.h>
#include <bcrypt.h>

#define BN_LIMBS 16                     /* 512-bit capacity (256-bit values, 512-bit products) */
typedef struct { unsigned int w[BN_LIMBS]; } bn_t;

static void bn_zero(bn_t *a){ int i; for(i=0;i<BN_LIMBS;i++) a->w[i]=0; }
static void bn_from_le(bn_t *a, const unsigned char *b, int n){ int i; bn_zero(a); for(i=0;i<n;i++) a->w[i>>2]|=(unsigned int)b[i]<<((i&3)*8); }
static void bn_to_le(unsigned char *b, const bn_t *a, int n){ int i; for(i=0;i<n;i++) b[i]=(unsigned char)(a->w[i>>2]>>((i&3)*8)); }
static int  bn_bit(const bn_t *a, int i){ return (int)((a->w[i>>5]>>(i&31))&1); }
static int  bn_top_bit(const bn_t *a){ int i; for(i=BN_LIMBS*32-1;i>=0;i--) if(bn_bit(a,i)) return i; return -1; }
static int  bn_cmp(const bn_t *a, const bn_t *b){ int i; for(i=BN_LIMBS-1;i>=0;i--){ if(a->w[i]!=b->w[i]) return a->w[i]<b->w[i]?-1:1; } return 0; }
static void bn_shl1(bn_t *a){ int i; unsigned int c=0,nc; for(i=0;i<BN_LIMBS;i++){ nc=a->w[i]>>31; a->w[i]=(a->w[i]<<1)|c; c=nc; } }
static void bn_sub(bn_t *a, const bn_t *b){ int i; unsigned long long br=0,t; for(i=0;i<BN_LIMBS;i++){ t=(unsigned long long)a->w[i]-b->w[i]-br; a->w[i]=(unsigned int)t; br=(t>>32)&1; } }

/* r = a * b  (a,b < 2^256; product fits BN_LIMBS=16) */
static void bn_mul(bn_t *r, const bn_t *a, const bn_t *b){
    unsigned long long acc; int i,j; bn_zero(r);
    unsigned int tmp[BN_LIMBS]; for(i=0;i<BN_LIMBS;i++) tmp[i]=0;
    for(i=0;i<8;i++){ unsigned long long carry=0;
        for(j=0;j<8 && i+j<BN_LIMBS;j++){ acc=(unsigned long long)a->w[i]*b->w[j]+tmp[i+j]+carry; tmp[i+j]=(unsigned int)acc; carry=acc>>32; }
        if(i+8<BN_LIMBS) tmp[i+8]+=(unsigned int)carry;
    }
    for(i=0;i<BN_LIMBS;i++) r->w[i]=tmp[i];
}
/* r = t mod n  (bit-serial; n has <=256 bits) */
static void bn_mod(bn_t *r, const bn_t *t, const bn_t *n){
    bn_t rem; int i, top; bn_zero(&rem);
    top=bn_top_bit(t); if(top<0){ *r=rem; return; }
    for(i=top;i>=0;i--){ bn_shl1(&rem); rem.w[0]|=bn_bit(t,i); if(bn_cmp(&rem,n)>=0) bn_sub(&rem,n); }
    *r=rem;
}
static void bn_mulmod(bn_t *r, const bn_t *a, const bn_t *b, const bn_t *n){ bn_t t; bn_mul(&t,a,b); bn_mod(r,&t,n); }
/* r = base^exp mod n */
static void bn_modexp(bn_t *r, const bn_t *base, const bn_t *exp, const bn_t *n){
    bn_t res, b; int i, top; bn_zero(&res); res.w[0]=1; b=*base; bn_mod(&b,&b,n);
    top=bn_top_bit(exp); if(top<0){ *r=res; return; }
    for(i=top;i>=0;i--){ bn_mulmod(&res,&res,&res,n); if(bn_bit(exp,i)) bn_mulmod(&res,&res,&b,n); }
    *r=res;
}

static int sha1_2(const unsigned char *a, ULONG na, const unsigned char *b, ULONG nb, unsigned char out[20]) {
    BCRYPT_ALG_HANDLE al=NULL; BCRYPT_HASH_HANDLE h=NULL; LONG st; int ok=0;
    ZeroMemory(out,20);
    st=BCryptOpenAlgorithmProvider(&al,BCRYPT_SHA1_ALGORITHM,NULL,0);
    if(st==0) st=BCryptCreateHash(al,&h,NULL,0,NULL,0,0);
    if(st==0) st=BCryptHashData(h,(PUCHAR)a,na,0);
    if(st==0 && nb) st=BCryptHashData(h,(PUCHAR)b,nb,0);
    if(st==0) st=BCryptFinishHash(h,out,20,0);
    ok=(st==0);
    if(h) BCryptDestroyHash(h);
    if(al) BCryptCloseAlgorithmProvider(al,0);
    return ok;
}
static int sha1(const unsigned char *d, ULONG n, unsigned char out[20]) {
    return sha1_2(d,n,NULL,0,out);
}

/* canonical WoW SRP6 modulus N (little-endian bytes) and g=7 */
static const char *SRP_N_HEX = "894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7"; /* big-endian */

static void hexbe_to_le(const char *hex, unsigned char *le, int n){
    int i; for(i=0;i<n;i++){ char c1=hex[2*(n-1-i)], c2=hex[2*(n-1-i)+1];
        int hi=(c1<='9')?c1-'0':(c1|32)-'a'+10, lo=(c2<='9')?c2-'0':(c2|32)-'a'+10; le[i]=(unsigned char)((hi<<4)|lo); }
}

/* compute x = SHA1(salt | SHA1(UPPER(user):UPPER(pass))), verifier v=g^x mod N (LE out) */
static void srp6_verifier(const char *user, const char *pass, const unsigned char salt[32], unsigned char v_le[32]){
    char up[128]; int i,n=0; unsigned char h1[20], xb[20]; bn_t N,G,X,V;
    unsigned char Nle[32]; hexbe_to_le(SRP_N_HEX,Nle,32);
    for(i=0;user[i]&&n<120;i++) up[n++]=(char)((user[i]>='a'&&user[i]<='z')?user[i]-32:user[i]);
    up[n++]=':';
    for(i=0;pass[i]&&n<127;i++) up[n++]=(char)((pass[i]>='a'&&pass[i]<='z')?pass[i]-32:pass[i]);
    sha1((unsigned char*)up,n,h1);
    sha1_2(salt,32,h1,20,xb);
    bn_from_le(&N,Nle,32); bn_zero(&G); G.w[0]=7; bn_from_le(&X,xb,20);
    bn_modexp(&V,&G,&X,&N); bn_to_le(v_le,&V,32);
}

/* full proof: given server B(LE),salt, compute A(LE) and M1; a is random (32B) */
static int srp6_proof(const char *user, const char *pass, const unsigned char B_le[32],
                       const unsigned char salt[32], const unsigned char a_le[32],
                       unsigned char A_le[32], unsigned char M1[20], unsigned char M2[20]){
    unsigned char Nle[32]; hexbe_to_le(SRP_N_HEX,Nle,32);
    char up[128]; int i,n=0,ok=0; unsigned char m2in[92]; unsigned char h1[20], xb[20];
    bn_t N,G,X,A_,B_,V,S,kv,base,expn; unsigned char v_le[32], u_in[64], ub[20], K[40];
    unsigned char Nh[20],gh[20],Uh[20],gmod[20], Sle[32], even[16],odd[16],he[20],ho[20], m1in[20+20+32+32+32+40];
    bn_from_le(&N,Nle,32); bn_zero(&G); G.w[0]=7;
    /* x, v */
    for(i=0;user[i]&&n<120;i++) up[n++]=(char)((user[i]>='a'&&user[i]<='z')?user[i]-32:user[i]);
    up[n++]=':'; for(i=0;pass[i]&&n<127;i++) up[n++]=(char)((pass[i]>='a'&&pass[i]<='z')?pass[i]-32:pass[i]);
    if(!sha1((unsigned char*)up,n,h1)) goto cleanup; if(!sha1_2(salt,32,h1,20,xb)) goto cleanup;
    bn_from_le(&X,xb,20); bn_modexp(&V,&G,&X,&N); bn_to_le(v_le,&V,32);
    /* A = g^a */
    { bn_t Aexp; bn_from_le(&Aexp,a_le,32); bn_modexp(&A_,&G,&Aexp,&N); bn_to_le(A_le,&A_,32); }
    bn_from_le(&B_,B_le,32);
    /* u = SHA1(A|B) LE */
    { memcpy(u_in,A_le,32); memcpy(u_in+32,B_le,32); if(!sha1(u_in,64,ub)) goto cleanup; }
    /* S = (B - k*v)^(a + u*x) mod N ; k=3 */
    { bn_t k,uxb,U_,t,kvm,nn; unsigned long long c; int j;
      bn_zero(&k); k.w[0]=3; bn_mulmod(&kv,&k,&V,&N);       /* kv = (k*v) mod N */
      /* base = (B - kv) mod N, borrow-safe: if B<kv add N first */
      bn_from_le(&t,B_le,32); kvm=kv; bn_from_le(&nn,Nle,32);
      if(bn_cmp(&t,&kvm)<0){ c=0; for(j=0;j<BN_LIMBS;j++){ unsigned long long s=(unsigned long long)t.w[j]+nn.w[j]+c; t.w[j]=(unsigned int)s; c=s>>32; } }
      bn_sub(&t,&kvm); bn_mod(&base,&t,&N);
      /* exponent = a + u*x */
      bn_from_le(&U_,ub,20); bn_mul(&uxb,&U_,&X);           /* u*x */
      bn_from_le(&expn,a_le,32);
      c=0; for(j=0;j<BN_LIMBS;j++){ unsigned long long s=(unsigned long long)expn.w[j]+uxb.w[j]+c; expn.w[j]=(unsigned int)s; c=s>>32; }
      bn_modexp(&S,&base,&expn,&N); bn_to_le(Sle,&S,32);
    }
    /* K = interleave SHA1(even)|SHA1(odd) of S */
    { int j; for(j=0;j<16;j++){ even[j]=Sle[2*j]; odd[j]=Sle[2*j+1]; } if(!sha1(even,16,he)) goto cleanup; if(!sha1(odd,16,ho)) goto cleanup;
      for(j=0;j<20;j++){ K[2*j]=he[j]; K[2*j+1]=ho[j]; } }
    /* M1 = SHA1( (SHA1(N)^SHA1(g)) | SHA1(U) | salt | A | B | K ) */
    { int j; if(!sha1(Nle,32,Nh)) goto cleanup; { unsigned char gb=7; if(!sha1(&gb,1,gh)) goto cleanup;} for(j=0;j<20;j++) gmod[j]=Nh[j]^gh[j];
      if(!sha1((unsigned char*)user,(ULONG)lstrlenA(user),Uh)) goto cleanup; /* NOTE: username case as-sent */
      { int p=0; memcpy(m1in+p,gmod,20); p+=20; memcpy(m1in+p,Uh,20); p+=20; memcpy(m1in+p,salt,32); p+=32;
        memcpy(m1in+p,A_le,32); p+=32; memcpy(m1in+p,B_le,32); p+=32; memcpy(m1in+p,K,40); p+=40; if(!sha1(m1in,p,M1)) goto cleanup; }
    }
    memcpy(m2in,A_le,32); memcpy(m2in+32,M1,20); memcpy(m2in+52,K,40);
    if(!sha1(m2in,sizeof(m2in),M2)) goto cleanup;
    ok=1;
cleanup:
    SecureZeroMemory(up,sizeof(up)); SecureZeroMemory(h1,sizeof(h1)); SecureZeroMemory(xb,sizeof(xb));
    SecureZeroMemory(&X,sizeof(X)); SecureZeroMemory(&V,sizeof(V)); SecureZeroMemory(&S,sizeof(S));
    SecureZeroMemory(&expn,sizeof(expn)); SecureZeroMemory(v_le,sizeof(v_le));
    SecureZeroMemory(K,sizeof(K)); SecureZeroMemory(Sle,sizeof(Sle));
    SecureZeroMemory(even,sizeof(even)); SecureZeroMemory(odd,sizeof(odd));
    SecureZeroMemory(he,sizeof(he)); SecureZeroMemory(ho,sizeof(ho));
    SecureZeroMemory(m1in,sizeof(m1in)); SecureZeroMemory(m2in,sizeof(m2in));
    return ok;
}

/* self-test: modexp + verifier against known vectors. returns 1 if both pass. */
static int srp6_selftest(unsigned char *dbg_v){
    unsigned char Nle[32]; bn_t N,G,E,R; unsigned char exp_le[32],res[32],vv[32];
    static const unsigned char WANT_MODEXP[32]={0xfa,0xe4,0xf6,0xea,0xda,0xef,0xec,0x49,0xe8,0x91,0x6a,0x3e,0x56,0x46,0x3d,0xef,0x0f,0x78,0x3d,0x3b,0x86,0x0a,0x58,0x3b,0x18,0xd5,0x70,0x42,0xcf,0x0c,0x6a,0x00};
    static const unsigned char SALT[32]={0xba,0x81,0xc2,0x50,0xa7,0xbe,0x2a,0x6a,0x07,0x75,0x70,0x00,0x8d,0xc6,0x74,0xff,0xe8,0xa3,0x70,0x50,0x0f,0x54,0x31,0x02,0xbc,0x13,0x63,0xe2,0x27,0xba,0x7b,0xa1};
    static const unsigned char WANT_V[32]={0x62,0x40,0xde,0x5c,0x59,0xcd,0x16,0x94,0xac,0x97,0x87,0x92,0xf0,0x74,0x3c,0xaa,0x00,0xbc,0x5b,0x89,0x17,0x27,0xc6,0xa3,0x7f,0x89,0xb4,0xf4,0xdd,0xd8,0x06,0x00};
    int i,ok1,ok2;
    hexbe_to_le(SRP_N_HEX,Nle,32); bn_from_le(&N,Nle,32); bn_zero(&G); G.w[0]=7;
    for(i=0;i<32;i++) exp_le[i]=(unsigned char)i;
    bn_from_le(&E,exp_le,32); bn_modexp(&R,&G,&E,&N); bn_to_le(res,&R,32);
    ok1=1; for(i=0;i<32;i++) if(res[i]!=WANT_MODEXP[i]) ok1=0;
    srp6_verifier("TESTACC","test",SALT,vv);
    if(dbg_v) memcpy(dbg_v,vv,32);
    ok2=1; for(i=0;i<32;i++) if(vv[i]!=WANT_V[i]) ok2=0;
    return ok1 && ok2;
}

/* ---- network: validate (user,pass) via a real GRUNT/SRP6 login to authip:3724 ----
 * Returns 1 = credentials valid, 0 = rejected, -1 = could not reach/parse. */
static int srp6_recvn(SOCKET s, unsigned char *b, int n){ int g=0,r; while(g<n){ r=recv(s,(char*)b+g,n-g,0); if(r<=0) return g; g+=r; } return g; }

static int srp6_sendn(SOCKET s,const unsigned char *b,int n) {
    int sent=0,r; while(sent<n) {r=send(s,(const char*)b+sent,n-sent,0);if(r<=0)return sent;sent+=r;} return sent;
}
static int srp6_credentials(const char *user,const char *pass) {
    int u,p;
    if(!user || !pass)return 0;
    u=lstrlenA(user);p=lstrlenA(pass);
    /* No silent truncation; this client's login field and stock password limit. */
    return u>0 && u<=16 && p>0 && p<=16;
}
/* Exchanged on a caller-owned socket; separate so tests can use isolated ports. */
static int srp6_exchange(SOCKET s,const char *user,const char *pass) {
    int ulen,i,r,sz,result=-1; DWORD timeout=5000; unsigned char diff=0;
    char upper[17];
    unsigned char chal[80],resp[256],proof[75],B[32],salt[32],A[32],M1[20],M2[20],a_le[32],Nle[32];
    static const unsigned char PRE[29]={'W','o','W',0,3,3,5,0x34,0x30,0x36,0x38,0x78,0,
        0x6e,0x69,0x57,0,0x53,0x55,0x6e,0x65,0x98,0xfe,0xff,0xff,0x7f,0,0,1};
    if(!srp6_credentials(user,pass))return -1;
    if(setsockopt(s,SOL_SOCKET,SO_RCVTIMEO,(const char*)&timeout,sizeof(timeout)) ||
       setsockopt(s,SOL_SOCKET,SO_SNDTIMEO,(const char*)&timeout,sizeof(timeout)))return -1;
    ulen=lstrlenA(user);
    for(i=0;i<ulen;i++)upper[i]=(char)((user[i]>='a'&&user[i]<='z')?user[i]-32:user[i]);upper[ulen]=0;
    sz=30+ulen;chal[0]=0;chal[1]=8;chal[2]=(unsigned char)sz;chal[3]=(unsigned char)(sz>>8);
    memcpy(chal+4,PRE,29);chal[33]=(unsigned char)ulen;memcpy(chal+34,upper,ulen);
    if(srp6_sendn(s,chal,34+ulen)!=34+ulen)goto done;
    /* Failure is a three-byte packet, not a truncated 119-byte success. */
    if(srp6_recvn(s,resp,3)!=3 || resp[0]!=0)goto done;
    if(resp[2]!=0){result=0;goto done;}
    if(srp6_recvn(s,resp+3,116)!=116)goto done;
    hexbe_to_le(SRP_N_HEX,Nle,32);
    if(resp[35]!=1 || resp[36]!=7 || resp[37]!=32 || memcmp(resp+38,Nle,32) || resp[118]!=0)goto done;
    memcpy(B,resp+3,32);memcpy(salt,resp+70,32);
    {bn_t bnB,bnN,rem;bn_from_le(&bnB,B,32);bn_from_le(&bnN,Nle,32);bn_mod(&rem,&bnB,&bnN);if(bn_top_bit(&rem)<0)goto done;}
    /* A failed system RNG must never fall back to predictable tick counts. */
    if(BCryptGenRandom(NULL,a_le,32,BCRYPT_USE_SYSTEM_PREFERRED_RNG)!=0)goto done;
    if(!srp6_proof(upper,pass,B,salt,a_le,A,M1,M2))goto done;
    proof[0]=1;memcpy(proof+1,A,32);memcpy(proof+33,M1,20);ZeroMemory(proof+53,22);
    if(srp6_sendn(s,proof,75)!=75)goto done;
    r=srp6_recvn(s,resp,2);
    if(r!=2 || resp[0]!=1)goto done;
    if(resp[1]!=0){result=0;goto done;}
    /* Success alone is insufficient: authenticate the server with SHA1(A|M1|K). */
    if(srp6_recvn(s,resp+2,30)!=30)goto done;
    for(i=0;i<20;i++)diff|=resp[2+i]^M2[i];
    result=diff?0:1;
done:
    SecureZeroMemory(upper,sizeof(upper));SecureZeroMemory(a_le,sizeof(a_le));
    SecureZeroMemory(M1,sizeof(M1));SecureZeroMemory(M2,sizeof(M2));SecureZeroMemory(proof,sizeof(proof));
    return result;
}
/* 0 = loopback only (default). Set from config to permit one non-loopback
 * authserver; deliberately a single address, not a wildcard. */
static unsigned long g_srp6_allowed_ip = 0;


static int srp6_validate(const char *user,const char *pass,unsigned long authip_net) {
    SOCKET s;struct sockaddr_in sa;int result;
    /* Loopback-only by default. A deployment may opt in to ONE additional endpoint
     * (see authgate.cfg / g_srp6_allowed_ip) for LAN play; anything else is refused. */
    if(authip_net!=(g_srp6_allowed_ip?g_srp6_allowed_ip:htonl(INADDR_LOOPBACK))
       || !srp6_credentials(user,pass))return -1;
    s=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);if(s==INVALID_SOCKET)return -1;
    ZeroMemory(&sa,sizeof(sa));sa.sin_family=AF_INET;sa.sin_port=htons(3724);sa.sin_addr.s_addr=authip_net;
    if(connect(s,(struct sockaddr*)&sa,sizeof(sa))!=0){closesocket(s);return -1;}
    result=srp6_exchange(s,user,pass);closesocket(s);return result;
}

#endif /* SRP6_CLIENT_H */
