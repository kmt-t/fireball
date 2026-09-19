/*
 * experiments/pysim/benchmarks/profile/guest/suite.c
 * Freestanding wasm32 (MVP) guest suite used as a large profiling workload for pysim.
 *
 * Sixteen independent kernels cover integer, 64-bit, floating-point, sub-word memory,
 * br_table dispatch, call_indirect, recursion, and data-dependent branching.  Every kernel
 * seeds its own generator, so results depend only on the argument.
 *
 * Target constraints (pysim mirrors an embedded target):
 *   - MVP instructions only (built with -mcpu=mvp), no libc, no imports.
 *   - The interpreter's local-value stack is 128 words shared by the whole call chain,
 *     so recursion is shallow and frames are small.  Bulk data lives in linear memory.
 *   - Module limits: 256 functions, 1024 basic blocks, 64 exports.
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef int i32;
typedef short i16;
typedef unsigned long long u64;

#define EXPORT(name) __attribute__((export_name(#name)))

static u32 rng_state;

static void seed(u32 s) { rng_state = s ? s : 0x9E3779B9u; }

static u32 rnd(void) {
  u32 x = rng_state;
  x ^= x << 13;
  x ^= x >> 17;
  x ^= x << 5;
  rng_state = x;
  return x;
}

static u32 mix(u32 h, u32 v) { return (h ^ v) * 16777619u + (h >> 15); }

/* ---------------------------------------------------------------- SHA-256 */

static const u32 SHA_K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
    0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
    0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
    0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
    0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2};

static u32 sha_w[64];
static u32 sha_h[8];
static u8 sha_buf[64];

static u32 rotr(u32 x, u32 n) { return (x >> n) | (x << (32 - n)); }

static void sha_block(void) {
  for (u32 i = 0; i < 16; i++) {
    const u8 *p = sha_buf + i * 4;
    sha_w[i] = ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | p[3];
  }
  for (u32 i = 16; i < 64; i++) {
    u32 a = sha_w[i - 15], b = sha_w[i - 2];
    u32 s0 = rotr(a, 7) ^ rotr(a, 18) ^ (a >> 3);
    u32 s1 = rotr(b, 17) ^ rotr(b, 19) ^ (b >> 10);
    sha_w[i] = sha_w[i - 16] + s0 + sha_w[i - 7] + s1;
  }
  u32 a = sha_h[0], b = sha_h[1], c = sha_h[2], d = sha_h[3];
  u32 e = sha_h[4], f = sha_h[5], g = sha_h[6], h = sha_h[7];
  for (u32 i = 0; i < 64; i++) {
    u32 t1 = h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] +
             sha_w[i];
    u32 t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
    h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
  }
  sha_h[0] += a; sha_h[1] += b; sha_h[2] += c; sha_h[3] += d;
  sha_h[4] += e; sha_h[5] += f; sha_h[6] += g; sha_h[7] += h;
}

EXPORT(k_sha256) u32 k_sha256(u32 blocks) {
  seed(0x5A17u);
  sha_h[0] = 0x6a09e667; sha_h[1] = 0xbb67ae85; sha_h[2] = 0x3c6ef372; sha_h[3] = 0xa54ff53a;
  sha_h[4] = 0x510e527f; sha_h[5] = 0x9b05688c; sha_h[6] = 0x1f83d9ab; sha_h[7] = 0x5be0cd19;
  for (u32 b = 0; b < blocks; b++) {
    for (u32 i = 0; i < 64; i += 4) {
      u32 r = rnd();
      sha_buf[i] = (u8)r; sha_buf[i + 1] = (u8)(r >> 8);
      sha_buf[i + 2] = (u8)(r >> 16); sha_buf[i + 3] = (u8)(r >> 24);
    }
    sha_block();
  }
  u32 h = 0;
  for (u32 i = 0; i < 8; i++) h = mix(h, sha_h[i]);
  return h;
}

/* ------------------------------------------------------------------ CRC32 */

static u32 crc_tab[256];
static u8 crc_buf[1024];

static void crc_init(void) {
  for (u32 i = 0; i < 256; i++) {
    u32 c = i;
    for (u32 k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
    crc_tab[i] = c;
  }
}

EXPORT(k_crc32) u32 k_crc32(u32 passes) {
  seed(0xC0C3u);
  crc_init();
  u32 h = 0;
  for (u32 p = 0; p < passes; p++) {
    for (u32 i = 0; i < sizeof crc_buf; i++) crc_buf[i] = (u8)rnd();
    u32 c = ~0u;
    for (u32 i = 0; i < sizeof crc_buf; i++) c = crc_tab[(c ^ crc_buf[i]) & 255] ^ (c >> 8);
    h = mix(h, ~c);
  }
  return h;
}

/* ------------------------------------------------- Sorting and searching */

#define SORT_MAX 768
static i32 sort_a[SORT_MAX];
static i32 sort_b[SORT_MAX];
static i32 sort_stack[48];

static void insertion(i32 *a, i32 lo, i32 hi) {
  for (i32 i = lo + 1; i <= hi; i++) {
    i32 v = a[i], j = i - 1;
    while (j >= lo && a[j] > v) { a[j + 1] = a[j]; j--; }
    a[j + 1] = v;
  }
}

/* Iterative quicksort: an explicit stack keeps the wasm call depth at one. */
static void quicksort(i32 *a, i32 n) {
  i32 sp = 0;
  sort_stack[sp++] = 0;
  sort_stack[sp++] = n - 1;
  while (sp > 0) {
    i32 hi = sort_stack[--sp], lo = sort_stack[--sp];
    if (hi - lo < 10) { insertion(a, lo, hi); continue; }
    i32 mid = lo + ((hi - lo) >> 1);
    i32 x = a[lo], y = a[mid], z = a[hi];
    i32 pivot = x < y ? (y < z ? y : (x < z ? z : x)) : (x < z ? x : (y < z ? z : y));
    i32 i = lo, j = hi;
    while (i <= j) {
      while (a[i] < pivot) i++;
      while (a[j] > pivot) j--;
      if (i <= j) { i32 t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
    }
    if (lo < j) { sort_stack[sp++] = lo; sort_stack[sp++] = j; }
    if (i < hi) { sort_stack[sp++] = i; sort_stack[sp++] = hi; }
  }
}

static void heapsort(i32 *a, i32 n) {
  for (i32 s = n / 2 - 1; s >= 0; s--) {
    i32 root = s;
    for (;;) {
      i32 child = root * 2 + 1;
      if (child >= n) break;
      if (child + 1 < n && a[child] < a[child + 1]) child++;
      if (a[root] >= a[child]) break;
      i32 t = a[root]; a[root] = a[child]; a[child] = t;
      root = child;
    }
  }
  for (i32 end = n - 1; end > 0; end--) {
    i32 t = a[0]; a[0] = a[end]; a[end] = t;
    i32 root = 0;
    for (;;) {
      i32 child = root * 2 + 1;
      if (child >= end) break;
      if (child + 1 < end && a[child] < a[child + 1]) child++;
      if (a[root] >= a[child]) break;
      i32 u = a[root]; a[root] = a[child]; a[child] = u;
      root = child;
    }
  }
}

static i32 bsearch_pos(const i32 *a, i32 n, i32 key) {
  i32 lo = 0, hi = n - 1;
  while (lo <= hi) {
    i32 mid = lo + ((hi - lo) >> 1);
    if (a[mid] == key) return mid;
    if (a[mid] < key) lo = mid + 1; else hi = mid - 1;
  }
  return -1;
}

EXPORT(k_sort) u32 k_sort(u32 rounds) {
  seed(0x50127u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    for (i32 i = 0; i < SORT_MAX; i++) { sort_a[i] = (i32)(rnd() >> 8) - 0x400000; sort_b[i] = sort_a[i]; }
    quicksort(sort_a, SORT_MAX);
    heapsort(sort_b, SORT_MAX);
    for (i32 i = 0; i < SORT_MAX; i++) {
      if (sort_a[i] != sort_b[i]) return 0xDEAD0001u;
      if (i && sort_a[i - 1] > sort_a[i]) return 0xDEAD0002u;
    }
    for (u32 q = 0; q < 96; q++) {
      i32 key = (q & 1) ? sort_a[rnd() % SORT_MAX] : (i32)rnd();
      h = mix(h, (u32)bsearch_pos(sort_a, SORT_MAX, key));
    }
    h = mix(h, (u32)sort_a[SORT_MAX / 2]);
  }
  return h;
}

/* -------------------------------------------------------- Matrix multiply */

#define MN 16
static i32 mat_a[MN * MN], mat_b[MN * MN], mat_c[MN * MN];
static double fm_a[MN * MN], fm_b[MN * MN], fm_c[MN * MN];

EXPORT(k_matmul) u32 k_matmul(u32 rounds) {
  seed(0x3A73u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    for (u32 i = 0; i < MN * MN; i++) {
      mat_a[i] = (i32)(rnd() & 0xFF) - 128; mat_b[i] = (i32)(rnd() & 0xFF) - 128;
    }
    for (u32 i = 0; i < MN; i++)
      for (u32 j = 0; j < MN; j++) {
        i32 s = 0;
        for (u32 k = 0; k < MN; k++) s += mat_a[i * MN + k] * mat_b[k * MN + j];
        mat_c[i * MN + j] = s;
      }
    for (u32 i = 0; i < MN * MN; i++) h = mix(h, (u32)mat_c[i]);
  }
  return h;
}

EXPORT(k_fmatmul) u32 k_fmatmul(u32 rounds) {
  seed(0xF3A7u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    for (u32 i = 0; i < MN * MN; i++) {
      fm_a[i] = (double)(i32)(rnd() & 0x3FF) / 64.0 - 8.0;
      fm_b[i] = (double)(i32)(rnd() & 0x3FF) / 64.0 - 8.0;
    }
    for (u32 i = 0; i < MN; i++)
      for (u32 j = 0; j < MN; j++) {
        double s = 0.0;
        for (u32 k = 0; k < MN; k++) s += fm_a[i * MN + k] * fm_b[k * MN + j];
        fm_c[i * MN + j] = s;
      }
    for (u32 i = 0; i < MN * MN; i++) h = mix(h, (u32)(i32)(fm_c[i] * 16.0));
  }
  return h;
}

/* ------------------------------------------------------------------ N-body */

#define NB 5
static double nb_x[NB], nb_y[NB], nb_vx[NB], nb_vy[NB], nb_m[NB];

EXPORT(k_nbody) u32 k_nbody(u32 steps) {
  seed(0x2B0D1u);
  for (u32 i = 0; i < NB; i++) {
    nb_x[i] = (double)i * 4.0 - 8.0 + (double)(rnd() & 63) / 64.0;
    nb_y[i] = (double)(rnd() & 255) / 32.0 - 4.0;
    nb_vx[i] = (double)(rnd() & 31) / 512.0;
    nb_vy[i] = (double)(rnd() & 31) / 512.0;
    nb_m[i] = 0.5 + (double)(rnd() & 15) / 16.0;
  }
  const double dt = 0.01;
  for (u32 s = 0; s < steps; s++) {
    for (u32 i = 0; i < NB; i++)
      for (u32 j = i + 1; j < NB; j++) {
        double dx = nb_x[i] - nb_x[j], dy = nb_y[i] - nb_y[j];
        double d2 = dx * dx + dy * dy + 0.05;
        double inv = 1.0 / (d2 * __builtin_sqrt(d2));
        double fi = nb_m[j] * inv * dt, fj = nb_m[i] * inv * dt;
        nb_vx[i] -= dx * fi; nb_vy[i] -= dy * fi;
        nb_vx[j] += dx * fj; nb_vy[j] += dy * fj;
      }
    for (u32 i = 0; i < NB; i++) { nb_x[i] += nb_vx[i] * dt; nb_y[i] += nb_vy[i] * dt; }
  }
  double e = 0.0;
  for (u32 i = 0; i < NB; i++)
    e += 0.5 * nb_m[i] * (nb_vx[i] * nb_vx[i] + nb_vy[i] * nb_vy[i]);
  return (u32)(i32)(e * 1.0e6);
}

/* ------------------------------------------------------------- Mandelbrot */

EXPORT(k_mandel) u32 k_mandel(u32 rows) {
  u32 total = 0;
  for (u32 py = 0; py < rows; py++)
    for (u32 px = 0; px < 32; px++) {
      double cr = (double)(i32)px / 12.0 - 2.2, ci = (double)(i32)py / 8.0 - 1.2;
      double zr = 0.0, zi = 0.0;
      u32 it = 0;
      while (it < 40 && zr * zr + zi * zi <= 4.0) {
        double t = zr * zr - zi * zi + cr;
        zi = 2.0 * zr * zi + ci;
        zr = t;
        it++;
      }
      total = mix(total, it);
    }
  return total;
}

/* ------------------------------------------------------------ LZ77 codec */

#define LZ_N 768
static u8 lz_in[LZ_N], lz_out[LZ_N * 2], lz_back[LZ_N];
static u16 lz_head[256];

static u32 lz_compress(void) {
  for (u32 i = 0; i < 256; i++) lz_head[i] = 0xFFFF;
  u32 ip = 0, op = 0;
  while (ip < LZ_N) {
    u32 best = 0, dist = 0;
    if (ip + 3 <= LZ_N) {
      u32 hh = (lz_in[ip] * 31u + lz_in[ip + 1] * 7u + lz_in[ip + 2]) & 255;
      u32 cand = lz_head[hh];
      lz_head[hh] = (u16)ip;
      if (cand != 0xFFFF && ip - cand < 4096) {
        u32 l = 0;
        while (ip + l < LZ_N && l < 130 && lz_in[cand + l] == lz_in[ip + l]) l++;
        if (l >= 3) { best = l; dist = ip - cand; }
      }
    }
    if (best) {
      lz_out[op++] = (u8)(0x80 | (best - 3)); lz_out[op++] = (u8)dist; lz_out[op++] = (u8)(dist >> 8);
      ip += best;
    } else {
      lz_out[op++] = lz_in[ip++] & 0x7F;
    }
  }
  return op;
}

static u32 lz_expand(u32 clen) {
  u32 ip = 0, op = 0;
  while (ip < clen) {
    u8 t = lz_out[ip++];
    if (t & 0x80) {
      u32 len = (t & 0x7F) + 3, dist = lz_out[ip] | ((u32)lz_out[ip + 1] << 8);
      ip += 2;
      for (u32 k = 0; k < len; k++) { lz_back[op] = lz_back[op - dist]; op++; }
    } else {
      lz_back[op++] = t;
    }
  }
  return op;
}

EXPORT(k_lz) u32 k_lz(u32 rounds) {
  static const char *const words[8] = {"alpha ", "beta ", "gamma ", "delta ", "fire ", "ball ", "wasm ", "jit "};
  seed(0x1277u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    u32 n = 0;
    while (n < LZ_N) {
      const char *w = words[rnd() & 7];
      for (; *w && n < LZ_N; w++) lz_in[n++] = (u8)*w;
      if ((rnd() & 7) == 0 && n < LZ_N) lz_in[n++] = (u8)('0' + (rnd() % 10));
    }
    u32 clen = lz_compress();
    if (lz_expand(clen) != LZ_N) return 0xDEAD0003u;
    for (u32 i = 0; i < LZ_N; i++)
      if (lz_back[i] != lz_in[i]) return 0xDEAD0004u;
    h = mix(h, clen);
  }
  return h;
}

/* ------------------------------------------------------------------ Sieve */

static u8 sieve_buf[8192];

EXPORT(k_sieve) u32 k_sieve(u32 passes) {
  u32 h = 0;
  for (u32 p = 0; p < passes; p++) {
    u32 limit = 4096 + p * 512;
    if (limit > sizeof sieve_buf) limit = sizeof sieve_buf;
    for (u32 i = 0; i < limit; i++) sieve_buf[i] = 1;
    sieve_buf[0] = sieve_buf[1] = 0;
    for (u32 i = 2; i * i < limit; i++)
      if (sieve_buf[i])
        for (u32 j = i * i; j < limit; j += i) sieve_buf[j] = 0;
    u32 count = 0;
    for (u32 i = 0; i < limit; i++) count += sieve_buf[i];
    h = mix(h, count);
  }
  return h;
}

/* --------------------------------------------- Recursive-descent evaluator */

static u8 ex_text[160];
static u32 ex_pos;
static u32 ex_len;

static i32 ex_expr(void);

static i32 ex_factor(void) {
  u8 c = ex_text[ex_pos];
  if (c == '(') {
    ex_pos++;
    i32 v = ex_expr();
    ex_pos++; /* ')' */
    return v;
  }
  if (c == '-') { ex_pos++; return -ex_factor(); }
  i32 v = 0;
  while (ex_text[ex_pos] >= '0' && ex_text[ex_pos] <= '9') v = v * 10 + (ex_text[ex_pos++] - '0');
  return v;
}

static i32 ex_term(void) {
  i32 v = ex_factor();
  for (;;) {
    u8 c = ex_text[ex_pos];
    if (c == '*') { ex_pos++; v *= ex_factor(); }
    else if (c == '/') { ex_pos++; i32 d = ex_factor(); v = d ? v / d : v; }
    else return v;
  }
}

static i32 ex_expr(void) {
  i32 v = ex_term();
  for (;;) {
    u8 c = ex_text[ex_pos];
    if (c == '+') { ex_pos++; v += ex_term(); }
    else if (c == '-') { ex_pos++; v -= ex_term(); }
    else return v;
  }
}

static void ex_put_num(void) {
  u32 n = rnd() % 900 + 1, div = 100;
  while (div > n) div /= 10;
  if (div == 0) div = 1;
  for (; div; div /= 10) ex_text[ex_len++] = (u8)('0' + (n / div) % 10);
}

static void ex_gen(u32 nest) {
  if (nest && (rnd() & 3) == 0) {
    ex_text[ex_len++] = '(';
    ex_gen(nest - 1);
    ex_text[ex_len++] = ')';
    return;
  }
  ex_put_num();
}

EXPORT(k_expr) u32 k_expr(u32 count) {
  static const u8 ops[4] = {'+', '-', '*', '/'};
  seed(0xE0BBu);
  u32 h = 0;
  for (u32 n = 0; n < count; n++) {
    ex_len = 0;
    u32 terms = 3 + (rnd() & 3);
    for (u32 t = 0; t < terms; t++) {
      if (t) ex_text[ex_len++] = ops[rnd() & 3];
      ex_gen(1);
    }
    ex_text[ex_len] = 0;
    ex_pos = 0;
    h = mix(h, (u32)ex_expr());
  }
  return h;
}

/* -------------------------------------------- Bytecode VM (br_table) */

enum { OP_HALT, OP_PUSH, OP_ADD, OP_MUL, OP_XOR, OP_DUP, OP_SWAP, OP_DROP, OP_DEC, OP_JNZ, OP_SHL, OP_AND, OP_OVER, OP_ROT };

static u8 vm_code[32];
static i32 vm_stack[16];

static u32 vm_run(u32 max_steps) {
  i32 sp = 0;
  u32 pc = 0, steps = 0;
  while (steps++ < max_steps) {
    u8 op = vm_code[pc++];
    switch (op) {
      case OP_HALT: return (u32)vm_stack[sp - 1] ^ steps;
      case OP_PUSH: vm_stack[sp++] = (i32)(i16)(vm_code[pc] | (vm_code[pc + 1] << 8)); pc += 2; break;
      case OP_ADD: sp--; vm_stack[sp - 1] += vm_stack[sp]; break;
      case OP_MUL: sp--; vm_stack[sp - 1] *= vm_stack[sp]; break;
      case OP_XOR: sp--; vm_stack[sp - 1] ^= vm_stack[sp]; break;
      case OP_DUP: vm_stack[sp] = vm_stack[sp - 1]; sp++; break;
      case OP_SWAP: { i32 t = vm_stack[sp - 1]; vm_stack[sp - 1] = vm_stack[sp - 2]; vm_stack[sp - 2] = t; break; }
      case OP_DROP: sp--; break;
      case OP_DEC: vm_stack[sp - 1]--; break;
      case OP_JNZ: { u32 target = vm_code[pc++]; if (vm_stack[sp - 1] != 0) pc = target; break; }
      case OP_SHL: sp--; vm_stack[sp - 1] <<= (vm_stack[sp] & 15); break;
      case OP_AND: sp--; vm_stack[sp - 1] &= vm_stack[sp]; break;
      case OP_OVER: vm_stack[sp] = vm_stack[sp - 2]; sp++; break;
      case OP_ROT: { i32 t = vm_stack[sp - 3]; vm_stack[sp - 3] = vm_stack[sp - 2]; vm_stack[sp - 2] = vm_stack[sp - 1]; vm_stack[sp - 1] = t; break; }
      default: return 0xDEAD0005u;
    }
  }
  return 0xDEAD0006u;
}

EXPORT(k_vm) u32 k_vm(u32 iterations) {
  /* acc = 0; i = n; do { acc = (acc + i * 3) ^ (i << 2); i--; } while (i); return acc; */
  static const u8 prog[] = {
      OP_PUSH, 0, 0,                      /* [acc] */
      OP_PUSH, 0, 0,                      /* [acc i]   (i patched at byte 4) */
      /* loop @6 */
      OP_OVER, OP_OVER,                   /* [acc i acc i] */
      OP_PUSH, 3, 0, OP_MUL, OP_ADD,      /* [acc i acc+i*3] */
      OP_OVER, OP_PUSH, 2, 0, OP_SHL,     /* [acc i x i<<2] */
      OP_XOR,                             /* [acc i y] */
      OP_ROT, OP_DROP,                    /* [i y] */
      OP_SWAP, OP_DEC,                    /* [y i-1] */
      OP_JNZ, 6,
      OP_DROP, OP_HALT};                  /* [y] */
  seed(0x77u);
  u32 h = 0;
  for (u32 r = 0; r < iterations; r++) {
    for (u32 i = 0; i < sizeof prog; i++) vm_code[i] = prog[i];
    vm_code[4] = (u8)(rnd() & 63) + 1;
    h = mix(h, vm_run(20000));
  }
  return h;
}

/* --------------------------------------- Function-pointer dispatch table */

static u32 fp0(u32 x) { return x + 0x9E37u; }
static u32 fp1(u32 x) { return x * 5u + 1u; }
static u32 fp2(u32 x) { return x ^ (x >> 7); }
static u32 fp3(u32 x) { return (x << 3) | (x >> 29); }
static u32 fp4(u32 x) { return x - 0x1234u; }
static u32 fp5(u32 x) { return x * x + 3u; }
static u32 fp6(u32 x) { return ~x; }
static u32 fp7(u32 x) { return (x & 0xFFFFu) * 65599u + (x >> 16); }

static u32 (*const fp_table[8])(u32) = {fp0, fp1, fp2, fp3, fp4, fp5, fp6, fp7};

EXPORT(k_dispatch) u32 k_dispatch(u32 n) {
  seed(0xD15Bu);
  u32 x = 1;
  for (u32 i = 0; i < n; i++) x = fp_table[(x ^ rnd()) & 7](x + i);
  return x;
}

/* ------------------------------------------------------------- Hash table */

#define HT_SIZE 512
static u32 ht_key[HT_SIZE], ht_val[HT_SIZE];
static u8 ht_state[HT_SIZE]; /* 0 empty, 1 used, 2 tombstone */

static i32 ht_find(u32 key) {
  u32 idx = (key * 2654435761u) >> 23;
  for (u32 probe = 0; probe < HT_SIZE; probe++) {
    u32 i = (idx + probe) & (HT_SIZE - 1);
    if (ht_state[i] == 0) return -1;
    if (ht_state[i] == 1 && ht_key[i] == key) return (i32)i;
  }
  return -1;
}

static void ht_put(u32 key, u32 val) {
  i32 f = ht_find(key);
  if (f >= 0) { ht_val[f] = val; return; }
  u32 idx = (key * 2654435761u) >> 23;
  for (u32 probe = 0; probe < HT_SIZE; probe++) {
    u32 i = (idx + probe) & (HT_SIZE - 1);
    if (ht_state[i] != 1) { ht_state[i] = 1; ht_key[i] = key; ht_val[i] = val; return; }
  }
}

EXPORT(k_hash) u32 k_hash(u32 rounds) {
  seed(0x4A5Bu);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    for (u32 i = 0; i < HT_SIZE; i++) ht_state[i] = 0;
    u32 keys[96];
    for (u32 i = 0; i < 96; i++) { keys[i] = rnd(); ht_put(keys[i], i * 7u + r); }
    for (u32 i = 0; i < 96; i += 3) {
      i32 f = ht_find(keys[i]);
      if (f >= 0) ht_state[f] = 2;
    }
    for (u32 i = 0; i < 96; i++) {
      i32 f = ht_find(keys[i]);
      h = mix(h, f < 0 ? 0xFFFFu : ht_val[f]);
    }
  }
  return h;
}

/* -------------------------------------------------------------- 64-bit ops */

EXPORT(k_int64) u32 k_int64(u32 n) {
  seed(0x64B17u);
  u64 acc = 0x123456789ABCDEFull, m = 4294967291ull;
  u32 h = 0;
  for (u32 i = 0; i < n; i++) {
    u64 a = ((u64)rnd() << 32) | rnd(), b = (u64)rnd() | 1u;
    acc = (acc * 6364136223846793005ull + 1442695040888963407ull) ^ (a >> 11);
    u64 mm = ((acc % m) * (b % m)) % m;
    u64 x = a, y = b;
    while (y) { u64 t = x % y; x = y; y = t; }
    h = mix(h, (u32)mm ^ (u32)x ^ (u32)__builtin_popcountll(acc) ^ (u32)__builtin_clzll(acc | 1));
  }
  return h ^ (u32)(acc >> 32);
}

/* -------------------------------------------------------------- Game of life */

#define LW 24
#define LH 16
static u8 life_a[LW * LH], life_b[LW * LH];

EXPORT(k_life) u32 k_life(u32 generations) {
  seed(0x11FEu);
  for (u32 i = 0; i < LW * LH; i++) life_a[i] = (rnd() & 3) == 0;
  u8 *cur = life_a, *nxt = life_b;
  for (u32 g = 0; g < generations; g++) {
    for (u32 y = 0; y < LH; y++)
      for (u32 x = 0; x < LW; x++) {
        u32 n = 0;
        for (i32 dy = -1; dy <= 1; dy++)
          for (i32 dx = -1; dx <= 1; dx++) {
            if (!dx && !dy) continue;
            u32 yy = (y + LH + (u32)dy) % LH, xx = (x + LW + (u32)dx) % LW;
            n += cur[yy * LW + xx];
          }
        nxt[y * LW + x] = (u8)(n == 3 || (n == 2 && cur[y * LW + x]));
      }
    u8 *t = cur; cur = nxt; nxt = t;
  }
  u32 h = 0;
  for (u32 i = 0; i < LW * LH; i++) h = mix(h, cur[i]);
  return h;
}

/* ------------------------------------------------- String state machine */

static u8 str_buf[512];

EXPORT(k_text) u32 k_text(u32 rounds) {
  static const char *const words[6] = {"jit", "trace", "cache", "42", "8192", "handoff"};
  seed(0x7E57u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    u32 n = 0;
    while (n < sizeof str_buf - 12) {
      const char *w = words[rnd() % 6];
      while (*w) str_buf[n++] = (u8)*w++;
      str_buf[n++] = (rnd() & 1) ? ' ' : ',';
    }
    u32 state = 0, words_seen = 0, number = 0, sum = 0, djb = 5381;
    for (u32 i = 0; i < n; i++) {
      u8 c = str_buf[i];
      djb = djb * 33u + c;
      u32 cls = (c >= '0' && c <= '9') ? 1 : (c == ' ' || c == ',') ? 2 : 0;
      switch (state * 3 + cls) {
        case 0: state = 0; break;                             /* in a word */
        case 1: state = 1; number = c - '0'; break;           /* word -> digits */
        case 2: state = 2; words_seen++; break;               /* word -> separator */
        case 3: state = 1; number = number * 10 + (c - '0'); break;
        case 4: number = number * 10 + (c - '0'); break;      /* digits continue */
        case 5: state = 2; sum += number; words_seen++; break;
        case 6: state = 0; break;
        case 7: state = 1; number = c - '0'; break;
        default: break;                                       /* separator run */
      }
    }
    h = mix(h, djb ^ (words_seen << 16) ^ sum);
  }
  return h;
}

/* ---------------------------------------------- Sub-word FIR filter */

#define FIR_N 512
#define FIR_TAPS 12
static i16 fir_in[FIR_N], fir_out[FIR_N];
static const i16 fir_taps[FIR_TAPS] = {-3, 5, 11, -17, 29, 61, 61, 29, -17, 11, 5, -3};

EXPORT(k_fir) u32 k_fir(u32 rounds) {
  seed(0xF12u);
  u32 h = 0;
  for (u32 r = 0; r < rounds; r++) {
    for (u32 i = 0; i < FIR_N; i++) fir_in[i] = (i16)(rnd() >> 12) - 0x4000;
    for (u32 i = FIR_TAPS; i < FIR_N; i++) {
      i32 acc = 0;
      for (u32 k = 0; k < FIR_TAPS; k++) acc += fir_in[i - k] * fir_taps[k];
      fir_out[i] = (i16)(acc >> 6);
    }
    for (u32 i = FIR_TAPS; i < FIR_N; i += 7) h = mix(h, (u32)(i32)fir_out[i]);
  }
  return h;
}

/* ------------------------------------------------------------ Whole mix */

EXPORT(k_mix) u32 k_mix(u32 scale) {
  u32 h = 0;
  h = mix(h, k_sha256(2 * scale));
  h = mix(h, k_crc32(scale));
  h = mix(h, k_sort(scale));
  h = mix(h, k_matmul(scale));
  h = mix(h, k_fmatmul(scale));
  h = mix(h, k_nbody(20 * scale));
  h = mix(h, k_mandel(2 * scale));
  h = mix(h, k_lz(scale));
  h = mix(h, k_sieve(scale));
  h = mix(h, k_expr(8 * scale));
  h = mix(h, k_vm(2 * scale));
  h = mix(h, k_dispatch(64 * scale));
  h = mix(h, k_hash(scale));
  h = mix(h, k_int64(16 * scale));
  h = mix(h, k_life(scale));
  h = mix(h, k_text(scale));
  h = mix(h, k_fir(scale));
  return h;
}
