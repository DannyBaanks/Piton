#include <stdint.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ── Collections ─────────────────────────────────────────────────────── */

enum { PITON_LIST = 1, PITON_TUPLE = 2, PITON_DICT = 3, PITON_SET = 4 };

typedef struct { int64_t key; int64_t value; } PitonEntry;

typedef struct {
    int64_t kind, length, capacity;
    PitonEntry entries[];
} PitonCollection;

static int64_t live_collections = 0;
static int64_t live_objects = 0;

void *piton_collection_new(int64_t kind, int64_t capacity) {
    if (capacity < 0) return NULL;
    PitonCollection *v = calloc(1, sizeof(*v) + (size_t)capacity * sizeof(PitonEntry));
    if (!v) return NULL;
    v->kind = kind; v->capacity = capacity;
    ++live_collections;
    return v;
}

void piton_collection_put(void *raw, int64_t index, int64_t key, int64_t value) {
    PitonCollection *c = raw;
    if (!c) return;
    if (c->kind == PITON_DICT || c->kind == PITON_SET) {
        for (int64_t i = 0; i < c->length; ++i)
            if (c->entries[i].key == key) {
                if (c->kind == PITON_DICT) c->entries[i].value = value;
                return;
            }
        index = c->length;
    }
    if (index < 0 || index >= c->capacity) return;
    c->entries[index].key = key;
    c->entries[index].value = value;
    if (index >= c->length) c->length = index + 1;
}

int64_t piton_collection_len(void *raw) {
    PitonCollection *c = raw;
    return c ? c->length : 0;
}

int64_t piton_collection_get(void *raw, int64_t key) {
    PitonCollection *c = raw;
    if (!c) return 0;
    if (c->kind == PITON_LIST || c->kind == PITON_TUPLE) {
        if (key < 0) key += c->length;
        if (key < 0 || key >= c->length) return 0;
        return c->entries[key].value;
    }
    if (c->kind == PITON_DICT)
        for (int64_t i = 0; i < c->length; ++i)
            if (c->entries[i].key == key) return c->entries[i].value;
    return 0;
}

void piton_collection_print(void *raw) {
    PitonCollection *c = raw;
    if (!c) return;
    char o = c->kind == PITON_LIST ? '[' : c->kind == PITON_TUPLE ? '(' : '{';
    char cl = c->kind == PITON_LIST ? ']' : c->kind == PITON_TUPLE ? ')' : '}';
    putchar(o);
    for (int64_t i = 0; i < c->length; ++i) {
        if (i) fputs(", ", stdout);
        if (c->kind == PITON_DICT)
            printf("%lld: %lld", (long long)c->entries[i].key, (long long)c->entries[i].value);
        else
            printf("%lld", (long long)c->entries[i].value);
    }
    if (c->kind == PITON_TUPLE && c->length == 1) putchar(',');
    putchar(cl);
    putchar('\n');
}

void piton_collection_free(void *raw) {
    if (raw) { --live_collections; free(raw); }
}

int64_t piton_collection_live_count(void) { return live_collections; }

/* ── Objects ─────────────────────────────────────────────────────────── */

typedef struct { const char *name; int64_t value; } PitonAttribute;

typedef struct {
    const char *class_name;
    int64_t length;
    PitonAttribute attributes[16];
} PitonObject;

void *piton_object_new(const char *class_name) {
    PitonObject *o = calloc(1, sizeof(*o));
    if (!o) return NULL;
    o->class_name = class_name;
    ++live_objects;
    return o;
}

void piton_object_set(void *raw, const char *name, int64_t value) {
    PitonObject *o = raw;
    if (!o || !name) return;
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) { o->attributes[i].value = value; return; }
    if (o->length >= 16) return;
    o->attributes[o->length].name = name;
    o->attributes[o->length].value = value;
    ++o->length;
}

int64_t piton_object_get(void *raw, const char *name) {
    PitonObject *o = raw;
    if (!o || !name) return 0;
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) return o->attributes[i].value;
    return 0;
}

void piton_object_free(void *raw) {
    if (raw) { --live_objects; free(raw); }
}

int64_t piton_object_live_count(void) { return live_objects; }

/* ── Raise / Float print ─────────────────────────────────────────────── */

void piton_raise(const char *type, const char *message) {
    fprintf(stderr, "%s", type ? type : "Exception");
    if (message && *message) fprintf(stderr, ": %s", message);
    fputc('\n', stderr);
    exit(1);
}

void piton_print_float(double value) {
    if (isfinite(value) && trunc(value) == value)
        printf("%.1f\n", value);
    else
        printf("%.15g\n", value);
}

/* ── BigInt ──────────────────────────────────────────────────────────── */

typedef struct {
    int sign;            /* 1 or -1 */
    uint64_t *limbs;     /* least significant first */
    int64_t count;       /* active limb count */
    int64_t capacity;
} PitonBigInt;

static void bi_ensure(PitonBigInt *a, int64_t cap) {
    if (a->capacity >= cap) return;
    a->limbs = realloc(a->limbs, (size_t)cap * sizeof(uint64_t));
    memset(a->limbs + a->capacity, 0, (size_t)(cap - a->capacity) * sizeof(uint64_t));
    a->capacity = cap;
}

static void bi_trim(PitonBigInt *a) {
    while (a->count > 0 && a->limbs[a->count - 1] == 0) --a->count;
    if (a->count == 0) a->sign = 1;
}

void *piton_bigint_from_i64(int64_t value) {
    PitonBigInt *a = calloc(1, sizeof(*a));
    a->sign = value < 0 ? -1 : 1;
    uint64_t abs_val = value < 0 ? (uint64_t)(-(value + 1)) + 1 : (uint64_t)value;
    if (abs_val) {
        bi_ensure(a, 1);
        a->limbs[0] = abs_val;
        a->count = 1;
    }
    return a;
}

void *piton_bigint_from_str(const char *s) {
    PitonBigInt *a = calloc(1, sizeof(*a));
    a->sign = 1;
    while (*s == ' ' || *s == '\t') ++s;
    if (*s == '-') { a->sign = -1; ++s; }
    else if (*s == '+') ++s;
    while (*s >= '0' && *s <= '9') {
        int digit = *s++ - '0';
        __uint128_t carry = 0;
        for (int64_t i = 0; i < a->count; ++i) {
            carry += (__uint128_t)a->limbs[i] * 10;
            a->limbs[i] = (uint64_t)carry;
            carry >>= 64;
        }
        if (carry) {
            bi_ensure(a, a->count + 1);
            a->limbs[a->count++] = (uint64_t)carry;
        }
        carry = digit;
        for (int64_t i = 0; i < a->count && carry; ++i) {
            carry += a->limbs[i];
            a->limbs[i] = (uint64_t)carry;
            carry >>= 64;
        }
        if (carry) {
            bi_ensure(a, a->count + 1);
            a->limbs[a->count++] = (uint64_t)carry;
        }
    }
    bi_trim(a);
    return a;
}

void piton_bigint_free(void *a) {
    if (a) { free(((PitonBigInt*)a)->limbs); free(a); }
}

static int bi_cmp_mag(PitonBigInt *a, PitonBigInt *b) {
    if (a->count != b->count) return a->count < b->count ? -1 : 1;
    for (int64_t i = a->count - 1; i >= 0; --i)
        if (a->limbs[i] != b->limbs[i]) return a->limbs[i] < b->limbs[i] ? -1 : 1;
    return 0;
}

int64_t piton_bigint_cmp(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    if (x->sign != y->sign) return x->sign < y->sign ? -1 : 1;
    int c = bi_cmp_mag(x, y);
    return x->sign < 0 ? -c : c;
}

void *piton_bigint_neg(void *a) {
    PitonBigInt *x = a;
    PitonBigInt *r = calloc(1, sizeof(*r));
    r->sign = -x->sign;
    if (x->count > 0) {
        bi_ensure(r, x->count);
        memcpy(r->limbs, x->limbs, (size_t)x->count * sizeof(uint64_t));
        r->count = x->count;
    }
    bi_trim(r);
    return r;
}

static void bi_add_mag(PitonBigInt *r, PitonBigInt *a, PitonBigInt *b) {
    int64_t max_c = a->count > b->count ? a->count : b->count;
    bi_ensure(r, max_c + 1);
    __uint128_t carry = 0;
    for (int64_t i = 0; i <= max_c; ++i) {
        if (i < a->count) carry += a->limbs[i];
        if (i < b->count) carry += b->limbs[i];
        r->limbs[i] = (uint64_t)carry;
        carry >>= 64;
        r->count = i + 1;
    }
}

static void bi_sub_mag(PitonBigInt *r, PitonBigInt *a, PitonBigInt *b) {
    bi_ensure(r, a->count);
    __uint128_t borrow = 0;
    for (int64_t i = 0; i < a->count; ++i) {
        __uint128_t diff = (__uint128_t)a->limbs[i] - borrow;
        if (i < b->count) diff -= b->limbs[i];
        borrow = (diff >> 64) & 1;
        r->limbs[i] = (uint64_t)diff;
        r->count = i + 1;
    }
    bi_trim(r);
}

void *piton_bigint_add(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    PitonBigInt *r = calloc(1, sizeof(*r));
    if (x->sign == y->sign) {
        r->sign = x->sign;
        bi_add_mag(r, x, y);
    } else {
        int c = bi_cmp_mag(x, y);
        if (c == 0) return r;
        if (c > 0) { r->sign = x->sign; bi_sub_mag(r, x, y); }
        else       { r->sign = y->sign; bi_sub_mag(r, y, x); }
    }
    bi_trim(r);
    return r;
}

void *piton_bigint_sub(void *a, void *b) {
    void *nb = piton_bigint_neg(b);
    void *r = piton_bigint_add(a, nb);
    piton_bigint_free(nb);
    return r;
}

void *piton_bigint_mul(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    PitonBigInt *r = calloc(1, sizeof(*r));
    r->sign = x->sign * y->sign;
    if (x->count == 0 || y->count == 0) return r;
    bi_ensure(r, x->count + y->count);
    for (int64_t i = 0; i < x->count; ++i) {
        __uint128_t carry = 0;
        for (int64_t j = 0; j < y->count || carry; ++j) {
            __uint128_t prod = (__uint128_t)r->limbs[i + j] + carry;
            if (j < y->count) prod += (__uint128_t)x->limbs[i] * y->limbs[j];
            r->limbs[i + j] = (uint64_t)prod;
            carry = prod >> 64;
        }
    }
    r->count = x->count + y->count;
    bi_trim(r);
    return r;
}

/* Bit-width of a magnitude. Returns 0 for zero. */
static int64_t bi_bit_width(PitonBigInt *a) {
    for (int64_t i = a->count - 1; i >= 0; --i) {
        if (a->limbs[i]) {
            int64_t w = i * 64;
            uint64_t v = a->limbs[i];
            while (v) { ++w; v >>= 1; }
            return w;
        }
    }
    return 0;
}

/* Unsigned long division: a = q*b + r, 0 <= r < b. Returns q.
   Schoolbook restoring division: process dividend bits from MSB to LSB. */
static void *bi_div_mod(void *a, void *b, void **rem_out) {
    PitonBigInt *dividend = a, *divisor = b;
    PitonBigInt *q = calloc(1, sizeof(*q));

    if (bi_cmp_mag(dividend, divisor) < 0) {
        if (rem_out) {
            PitonBigInt *r = calloc(1, sizeof(*r));
            if (dividend->count > 0) {
                bi_ensure(r, dividend->count);
                memcpy(r->limbs, dividend->limbs, (size_t)dividend->count * sizeof(uint64_t));
                r->count = dividend->count;
            }
            *rem_out = r;
        }
        return q;
    }
    if (bi_cmp_mag(dividend, divisor) == 0) {
        q = piton_bigint_from_i64(1);
        if (rem_out) *rem_out = calloc(1, sizeof(PitonBigInt));
        return q;
    }

    int64_t n_bits = bi_bit_width(dividend);
    int64_t d_bits = bi_bit_width(divisor);

    /* Working remainder — starts at 0, grows as we bring in dividend bits */
    PitonBigInt *work = calloc(1, sizeof(*work));
    int64_t q_bits = n_bits - d_bits + 1;
    if (q_bits > 0) {
        bi_ensure(q, (q_bits / 64) + 1);
        q->count = (q_bits / 64) + 1;
    }

    for (int64_t i = n_bits - 1; i >= 0; --i) {
        /* Bring in bit i of dividend into work */
        int64_t limb = i / 64;
        int bit = i % 64;
        int b = (limb < dividend->count) ? (int)((dividend->limbs[limb] >> bit) & 1) : 0;

        /* work = work << 1 | b */
        if (work->count == 0 && b == 0) continue;
        {
            int64_t new_count = work->count;
            if (work->count == 0) new_count = 1;
            uint64_t msb = (work->count > 0) ? (work->limbs[work->count - 1] >> 63) : 0;
            if (msb || b) ++new_count;
            bi_ensure(work, new_count);
            for (int64_t j = work->count - 1; j > 0; --j)
                work->limbs[j] = (work->limbs[j] << 1) | (work->limbs[j-1] >> 63);
            work->limbs[0] = (work->limbs[0] << 1) | (uint64_t)b;
            work->count = new_count;
            bi_trim(work);
        }

        if (bi_cmp_mag(work, divisor) >= 0) {
            bi_sub_mag(work, work, divisor);
            int64_t q_pos = i;
            int64_t ql = q_pos / 64;
            int qb = q_pos % 64;
            bi_ensure(q, ql + 1);
            if (q->count <= ql) q->count = ql + 1;
            q->limbs[ql] |= (1ULL << qb);
        }
    }
    bi_trim(q);
    bi_trim(work);
    if (rem_out) {
        if (work->count == 0) { free(work->limbs); free(work); *rem_out = calloc(1, sizeof(PitonBigInt)); }
        else *rem_out = work;
    } else {
        free(work->limbs);
        free(work);
    }
    return q;
}

void *piton_bigint_floor_div(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    if (y->count == 0) { fprintf(stderr, "ZeroDivisionError: division by zero\n"); exit(1); }
    PitonBigInt *abs_x = piton_bigint_neg(x); abs_x->sign = 1;
    PitonBigInt *abs_y = piton_bigint_neg(y); abs_y->sign = 1;
    void *q = bi_div_mod(abs_x, abs_y, NULL);
    piton_bigint_free(abs_x);
    piton_bigint_free(abs_y);
    PitonBigInt *rq = q;
    if (x->sign * y->sign < 0) {
        /* Check if remainder is non-zero → subtract 1 */
        PitonBigInt *ax = piton_bigint_neg(x); ax->sign = 1;
        PitonBigInt *ay = piton_bigint_neg(y); ay->sign = 1;
        void *rem = NULL;
        piton_bigint_free(q);
        q = bi_div_mod(ax, ay, &rem);
        rq = q;
        PitonBigInt *rr = rem;
        int has_rem = rr && rr->count > 0;
        piton_bigint_free(ax);
        piton_bigint_free(ay);
        if (has_rem) {
            PitonBigInt *one = piton_bigint_from_i64(1);
            void *nq = piton_bigint_sub(q, one);
            piton_bigint_free(q);
            piton_bigint_free(one);
            q = nq;
        }
        piton_bigint_free(rr);
    }
    PitonBigInt *result = q;
    result->sign = x->sign * y->sign;
    if (result->count == 0) result->sign = 1;
    return result;
}

void *piton_bigint_mod(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    if (y->count == 0) { fprintf(stderr, "ZeroDivisionError: modulo by zero\n"); exit(1); }
    PitonBigInt *ax = piton_bigint_neg(x); ax->sign = 1;
    PitonBigInt *ay = piton_bigint_neg(y); ay->sign = 1;
    void *rem = NULL;
    void *q = bi_div_mod(ax, ay, &rem);
    piton_bigint_free(ax);
    piton_bigint_free(ay);
    piton_bigint_free(q);
    PitonBigInt *r = rem;
    if (!r) r = piton_bigint_from_i64(0);
    /* r has the sign of b, and 0 <= r < |b| */
    r->sign = y->sign;
    if (r->count == 0) r->sign = 1;
    return r;
}

void piton_bigint_print(void *a) {
    PitonBigInt *bi = a;
    if (!bi || bi->count == 0) { puts("0"); return; }
    /* Convert to decimal by repeated div by 10 */
    char buf[128];
    int pos = 128;
    buf[--pos] = '\0';
    PitonBigInt *ten = piton_bigint_from_i64(10);
    PitonBigInt *work = piton_bigint_from_i64(0);
    free(((PitonBigInt*)work)->limbs);
    free(work);
    work = calloc(1, sizeof(*work));
    bi_ensure(work, bi->count);
    memcpy(work->limbs, bi->limbs, (size_t)bi->count * sizeof(uint64_t));
    work->count = bi->count;
    work->sign = 1;
    while (work->count > 0) {
        void *rem = NULL;
        void *q = bi_div_mod(work, ten, &rem);
        PitonBigInt *rr = rem;
        int digit = (rr && rr->count > 0) ? (int)rr->limbs[0] : 0;
        piton_bigint_free(rr);
        piton_bigint_free(work);
        work = q;
        buf[--pos] = '0' + digit;
    }
    piton_bigint_free(work);
    piton_bigint_free(ten);
    if (bi->sign < 0) buf[--pos] = '-';
    puts(buf + pos);
}
