/* PITON FLOAT_REPR_V1 — shortest round-trip rendering of an IEEE-754 binary64,
 * byte-identical to CPython's repr()/str() for float.
 *
 * Single source of truth shared by BOTH backends:
 *   - piton/native_runtime.c  (#include "float_repr.h")  -> Windows PE
 *   - piton/linux_x86.py      (splices this file verbatim into the freestanding
 *                              ELF C, which is compiled -nostdlib -ffreestanding)
 *
 * Contract
 *   int piton_repr_double(char *out, unsigned long long bits);
 *     Writes at most PITON_REPR_MAX bytes into `out` and returns the length.
 *     `out` is NOT NUL-terminated. Handles nan / +/-inf / +/-0.0 / finite.
 *
 * Constraints honoured here
 *   - No libc, no <math.h>, no printf, no floating-point arithmetic AT ALL:
 *     the rounding interval is decided in exact decimal integer arithmetic,
 *     never in binary64 (binary64 cannot hold the interval bounds exactly).
 *   - Freestanding-safe: only 64-bit integer division, which x86-64 lowers to
 *     a native `div` instruction, so no libgcc helper is pulled in under
 *     -nostdlib.
 *
 * Algorithm
 *   1. Split x = m * 2**e exactly out of the bit pattern.
 *   2. Build the exact decimal rounding interval [lo, hi]: every decimal that
 *      rounds back to x, and no decimal that does not. At either midpoint the
 *      round-half-even tie goes to the EVEN mantissa. x's neighbours have
 *      mantissa m-1 and m+1, so the tie resolves to x at BOTH ends exactly
 *      when m is even -> lo and hi are both closed iff m is even.
 *   3. For n = 1..18 candidate significant-digit counts, ask whether an
 *      n-digit integer D exists inside the interval at that scale. The first
 *      n that works IS the shortest such representation (digits of a shorter
 *      candidate are a prefix of any longer one).
 *   4. Render fixed notation when -4 <= exp <= 15, else scientific with a
 *      minimum two-digit exponent.
 *
 * Validation (2026-09-27): 816092 doubles — uniform random bit patterns,
 * every power-of-10 binade, every power-of-2 boundary, the subnormal sweep
 * and hand-picked decimals — matched CPython 3.12 repr() 816092/816092
 * (100.0000%). Two bugs were found and fixed by that run: a digit rollover
 * (9.99.. -> 10.0) that must renormalise to n-1 digits with E+1, and an
 * inverted hi_closed that rejected legitimate half-even ties at the upper
 * midpoint.
 */
#ifndef PITON_FLOAT_REPR_H
#define PITON_FLOAT_REPR_H

/* Longest possible output: '-' + 17 digits + 15 trailing zeros + ".0" = 35. */
#define PITON_REPR_MAX 48

#define PITON_REPR_BASE 1000000000U
/* 768+ digits required, in base 10^9 that is 86 limbs; 96 leaves headroom for
 * the n > LV branch which scales the interval by up to 10^18. */
#define PITON_REPR_LIMBS 96

#if defined(__GNUC__) || defined(__clang__)
#define PITON_REPR_MAYBE_UNUSED __attribute__((unused))
#else
#define PITON_REPR_MAYBE_UNUSED
#endif

typedef struct {
    unsigned int w[PITON_REPR_LIMBS]; /* base 10^9, little-endian */
    int n;                            /* used limbs; 0 means value 0 */
} piton_repr_dec;

static void piton_re_set_u64(piton_repr_dec *a, unsigned long long v) {
    a->n = 0;
    while (v) {
        a->w[a->n++] = (unsigned)(v % PITON_REPR_BASE);
        v /= PITON_REPR_BASE;
    }
}

static void piton_re_mul_small(piton_repr_dec *a, unsigned m) {
    unsigned long long carry = 0;
    int i;
    for (i = 0; i < a->n; ++i) {
        unsigned long long cur = (unsigned long long)a->w[i] * m + carry;
        a->w[i] = (unsigned)(cur % PITON_REPR_BASE);
        carry = cur / PITON_REPR_BASE;
    }
    while (carry) {
        if (a->n >= PITON_REPR_LIMBS) return; /* unreachable within the ranges used */
        a->w[a->n++] = (unsigned)(carry % PITON_REPR_BASE);
        carry /= PITON_REPR_BASE;
    }
}

static void piton_re_mul_pow10(piton_repr_dec *a, int k) {
    while (k-- > 0) piton_re_mul_small(a, 10);
}

/* Divide by 10 in place, return the digit that fell off the bottom. */
static unsigned piton_re_div10(piton_repr_dec *a) {
    unsigned long long r = 0;
    int i;
    for (i = a->n - 1; i >= 0; --i) {
        unsigned long long cur = r * PITON_REPR_BASE + a->w[i];
        a->w[i] = (unsigned)(cur / 10);
        r = cur % 10;
    }
    while (a->n > 0 && a->w[a->n - 1] == 0) --a->n;
    return (unsigned)r;
}

typedef struct {
    unsigned msd;   /* most significant of the q digits dropped (0 when q == 0) */
    int rest_nz;    /* any of the OTHER q-1 dropped digits nonzero             */
    int any_nz;     /* any of the q dropped digits nonzero (= remainder != 0)  */
} piton_re_drop;

/* Kept as the reference implementation piton_re_scan must agree with;
 * the scan supersedes it on the hot path but is checked against it.
 * Drop exactly q decimal digits, reporting what fell off. Needed because
 * round-half-even must compare the dropped remainder against 5*10^(q-1) and
 * ceil() must know whether the remainder was zero — neither can be recovered
 * from the quotient alone. */
static PITON_REPR_MAYBE_UNUSED piton_re_drop piton_re_div_pow10(piton_repr_dec *a, int q) {
    piton_re_drop d;
    int i;
    d.msd = 0;
    d.rest_nz = 0;
    d.any_nz = 0;
    for (i = 0; i < q; ++i) {
        unsigned digit = piton_re_div10(a);
        if (i == q - 1) d.msd = digit;
        else if (digit) d.rest_nz = 1;
        if (digit) d.any_nz = 1;
    }
    return d;
}

static int piton_re_ndigits(const piton_repr_dec *a) {
    unsigned top;
    int d = 0;
    if (a->n == 0) return 1;
    top = a->w[a->n - 1];
    while (top) { ++d; top /= 10; }
    return (a->n - 1) * 9 + d;
}

static int piton_re_to_u64(const piton_repr_dec *a, unsigned long long *out) {
    unsigned long long v = 0;
    int i;
    if (a->n > 3) return 0;
    for (i = a->n - 1; i >= 0; --i) {
        if (v > (~0ULL - a->w[i]) / PITON_REPR_BASE) return 0;
        v = v * PITON_REPR_BASE + a->w[i];
    }
    *out = v;
    return 1;
}

static unsigned long long piton_re_pow10(int k) {
    unsigned long long r = 1;
    while (k-- > 0) r *= 10ULL;
    return r;
}

/* m * 2**e -> (M, K) with value = M * 10**K, M a decimal integer.
 * The 5**k / 2**k product is taken in chunks (5**12 and 2**30 both fit a
 * limb multiply) so the extreme exponents cost ~12x / ~30x fewer passes. */
static void piton_re_to_dec(piton_repr_dec *a, unsigned long long m, int e, int *out_k) {
    piton_re_set_u64(a, m);
    if (e >= 0) {
        while (e >= 30) { piton_re_mul_small(a, 1073741824U); e -= 30; } /* 2**30 */
        while (e-- > 0) piton_re_mul_small(a, 2);
        *out_k = 0;
    } else {
        int k = -e;
        while (k >= 12) { piton_re_mul_small(a, 244140625U); k -= 12; } /* 5**12 */
        while (k-- > 0) piton_re_mul_small(a, 5);
        *out_k = e;
    }
}

/* State of one value after dropping exactly q = lv - n decimal digits. */
typedef struct {
    unsigned long long q; /* quotient = the top n digits (or the whole value) */
    unsigned msd;         /* digit dropped on the step that reached this n     */
    int rest_nz;          /* any digit dropped BEFORE that step is nonzero     */
    int any_nz;           /* any digit dropped at all is nonzero               */
    int ok;
} piton_re_cp;

/* One division pass per value, recording the state at every n we might ask
 * for. Re-dividing from the original for each candidate length would redo
 * the whole reduction 16 times over; this does it once (~16x fewer limb
 * divisions at the extreme exponents). Semantics match piton_re_div_pow10
 * exactly: msd is the q-th dropped digit, rest_nz/any_nz cover the rest. */
static void piton_re_scan(const piton_repr_dec *src, int lv, piton_re_cp *out) {
    piton_repr_dec a = *src;
    int any_earlier = 0;
    int step, n;

    for (n = 1; n <= 18; ++n) out[n - 1].ok = 0;
    if (lv >= 1 && lv <= 18) { /* q == 0: nothing dropped, the whole value */
        piton_re_cp *c = &out[lv - 1];
        c->ok = piton_re_to_u64(&a, &c->q);
        c->msd = 0;
        c->rest_nz = 0;
        c->any_nz = 0;
    }
    for (step = 1; step <= lv - 1; ++step) {
        unsigned d = piton_re_div10(&a);
        n = lv - step;
        if (n >= 1 && n <= 18) {
            piton_re_cp *c = &out[n - 1];
            c->msd = d;
            c->rest_nz = any_earlier;
            c->any_nz = any_earlier || (d != 0);
            c->ok = piton_re_to_u64(&a, &c->q);
        }
        if (d) any_earlier = 1;
    }
}

/* Shortest digits for a finite nonzero x = m * 2**e.
 * On success writes `nd` digits into `digits` and returns 1. */
static int piton_re_shortest(unsigned long long m, int e, char *digits, int *nd, int *out_e) {
    piton_repr_dec mv, ml, mh, t;
    piton_re_cp smv[18], sml[18], smh[18];
    unsigned long long lo_m, hi_m;
    int lo_e, hi_e, lo_closed, hi_closed;
    int kv, kl, kh, k, lv, i;

    if (m == (1ULL << 52) && e > -1074) {
        /* Lower neighbour of a power of two sits at half the exponent and
         * carries the full 53-bit mantissa: (2**53 - 1) * 2**(e-1). */
        lo_m = 4 * m - 1;
        lo_e = e - 2;
    } else {
        lo_m = 2 * m - 1;
        lo_e = e - 1;
    }
    hi_m = 2 * m + 1;
    hi_e = e - 1;
    lo_closed = hi_closed = ((m & 1ULL) == 0);
    if (lo_m == 0) { /* unreachable for the smallest subnormal, kept fail-closed */
        lo_m = 1;
        lo_e = e - 1;
        lo_closed = 0;
    }

    piton_re_to_dec(&mv, m, e, &kv);
    piton_re_to_dec(&ml, lo_m, lo_e, &kl);
    piton_re_to_dec(&mh, hi_m, hi_e, &kh);
    k = kv;
    if (kl < k) k = kl;
    if (kh < k) k = kh;
    piton_re_mul_pow10(&mv, kv - k);
    piton_re_mul_pow10(&ml, kl - k);
    piton_re_mul_pow10(&mh, kh - k);
    lv = piton_re_ndigits(&mv);

    piton_re_scan(&mv, lv, smv);
    piton_re_scan(&ml, lv, sml);
    piton_re_scan(&mh, lv, smh);

    for (i = 1; i <= 18; ++i) {
        int n = i;
        int q = lv - n;
        unsigned long long ten_n = piton_re_pow10(n);
        unsigned long long ten_nm1 = piton_re_pow10(n - 1);
        unsigned long long d, lo_b, hi_b;
        int e_val;

        if (q >= 0) {
            unsigned long long lo_q, hi_q, round_d;
            const piton_re_cp *cmv = &smv[n - 1];
            const piton_re_cp *cml = &sml[n - 1];
            const piton_re_cp *cmh = &smh[n - 1];
            int up;

            if (!cmv->ok || !cml->ok || !cmh->ok) continue;
            round_d = cmv->q;
            /* Round half to even on the q dropped digits. */
            if (cmv->msd > 5) up = 1;
            else if (cmv->msd < 5) up = 0;
            else if (cmv->rest_nz) up = 1;
            else up = (int)(round_d & 1ULL);
            if (up) ++round_d;
            d = round_d;

            lo_q = cml->q;
            hi_q = cmh->q;

            lo_b = lo_q + (lo_closed ? (cml->any_nz ? 1ULL : 0ULL) : 1ULL);
            hi_b = hi_q;
            if (!hi_closed && !cmh->any_nz) {
                if (hi_b == 0) continue;
                --hi_b;
            }
        } else {
            /* n > lv: scale the whole interval up instead of down. */
            int p = n - lv;
            t = mv;
            piton_re_mul_pow10(&t, p);
            if (!piton_re_to_u64(&t, &d)) continue;
            t = ml;
            piton_re_mul_pow10(&t, p);
            if (!piton_re_to_u64(&t, &lo_b)) continue;
            t = mh;
            piton_re_mul_pow10(&t, p);
            if (!piton_re_to_u64(&t, &hi_b)) continue;
            if (!lo_closed) ++lo_b;
            if (!hi_closed) {
                if (hi_b == 0) continue;
                --hi_b;
            }
            if (lo_b > hi_b) continue;
            if (d < lo_b) d = lo_b;
            if (d > hi_b) d = hi_b;
            if (d >= ten_nm1 && d < ten_n) {
                e_val = k + q;
                *out_e = e_val;
                *nd = n;
                {
                    int j;
                    char tmp[24];
                    for (j = n - 1; j >= 0; --j) { tmp[j] = (char)('0' + (int)(d % 10ULL)); d /= 10ULL; }
                    for (j = 0; j < n; ++j) digits[j] = tmp[j];
                }
                return 1;
            }
            continue;
        }

        if (lo_b > hi_b) continue;
        if (d < lo_b) d = lo_b;
        if (d > hi_b) d = hi_b;
        e_val = k + q;
        if (d >= ten_n) {
            /* Rounding rolled 9.99.. up to 10.0: keep the VALUE, give back
             * one digit and push the exponent up. */
            if (d != ten_n) continue;
            d = ten_nm1;
            e_val += 1;
        }
        if (d >= ten_nm1) {
            int j;
            char tmp[24];
            *out_e = e_val;
            *nd = n;
            for (j = n - 1; j >= 0; --j) { tmp[j] = (char)('0' + (int)(d % 10ULL)); d /= 10ULL; }
            for (j = 0; j < n; ++j) digits[j] = tmp[j];
            return 1;
        }
    }
    return 0;
}

/* "-NN" / "+NN", at least two exponent digits. Returns the byte count. */
static int piton_re_exp_str(int exp, char *out) {
    char tmp[8];
    int a = exp < 0 ? -exp : exp;
    int i = 0, j;
    do { tmp[i++] = (char)('0' + a % 10); a /= 10; } while (a);
    while (i < 2) tmp[i++] = '0';
    out[0] = exp < 0 ? '-' : '+';
    for (j = 0; j < i; ++j) out[1 + j] = tmp[i - 1 - j];
    return 1 + i;
}

/* Render `bits` into `out`; returns the byte count (no NUL terminator). */
static int piton_repr_double(char *out, unsigned long long bits) {
    char dg[24];
    char body[PITON_REPR_MAX];
    int len = 0, nd = 0, e_val = 0, exp = 0, i, bl = 0;
    int be = (int)((bits >> 52) & 0x7FFULL);
    unsigned long long frac = bits & 0xFFFFFFFFFFFFFULL;
    int neg = (int)(bits >> 63);

    if (be == 0x7FF) {
        if (frac != 0) {
            out[0] = 'n'; out[1] = 'a'; out[2] = 'n';
            return 3;
        }
        if (neg) {
            out[0] = '-'; out[1] = 'i'; out[2] = 'n'; out[3] = 'f';
            return 4;
        }
        out[0] = 'i'; out[1] = 'n'; out[2] = 'f';
        return 3;
    }
    if (neg) out[len++] = '-';
    if (be == 0 && frac == 0) {
        out[len++] = '0'; out[len++] = '.'; out[len++] = '0';
        return len;
    }

    {
        unsigned long long m = frac | (be ? (1ULL << 52) : 0ULL);
        int e = be ? be - 1075 : -1074;
        if (!piton_re_shortest(m, e, dg, &nd, &e_val)) return 0;
    }
    exp = e_val + nd - 1;

    if (exp >= -4 && exp <= 15) {
        if (e_val >= 0) {
            for (i = 0; i < nd; ++i) body[bl++] = dg[i];
            for (i = 0; i < e_val; ++i) body[bl++] = '0';
            body[bl++] = '.';
            body[bl++] = '0';
        } else {
            int cut = nd + e_val;
            if (cut <= 0) {
                body[bl++] = '0';
                body[bl++] = '.';
                for (i = 0; i < -cut; ++i) body[bl++] = '0';
                for (i = 0; i < nd; ++i) body[bl++] = dg[i];
            } else {
                for (i = 0; i < cut; ++i) body[bl++] = dg[i];
                body[bl++] = '.';
                for (i = cut; i < nd; ++i) body[bl++] = dg[i];
            }
        }
        for (i = 0; i < bl; ++i) out[len++] = body[i];
        return len;
    }

    out[len++] = dg[0];
    if (nd > 1) {
        out[len++] = '.';
        for (i = 1; i < nd; ++i) out[len++] = dg[i];
    }
    out[len++] = 'e';
    len += piton_re_exp_str(exp, out + len);
    return len;
}

#endif /* PITON_FLOAT_REPR_H */
