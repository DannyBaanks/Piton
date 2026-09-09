#include <stdint.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ── PitonValue: tagged 64-bit value (wire format) ────────────────────── */

enum {
    PITON_TAG_NONE   = 0,
    PITON_TAG_BOOL   = 1,
    PITON_TAG_INT    = 2,
    PITON_TAG_FLOAT  = 3,   /* payload = pointer to heap double */
    PITON_TAG_OBJECT = 4,   /* payload = pointer to heap object */
};

enum {
    SUB_TAG_STR     = 5,
    SUB_TAG_LIST    = 6,
    SUB_TAG_TUPLE   = 7,
    SUB_TAG_DICT    = 8,
    SUB_TAG_SET     = 9,
    SUB_TAG_BIGINT  = 10,
};

/* Encoded wire format: same as ABI — 3-bit tag in high bits, 61-bit payload. */
#define TAG_SHIFT  61
#define PAYLOAD_MASK  ((1LL << TAG_SHIFT) - 1)
#define SIGN_EXTEND(v) (((int64_t)(v) << (64 - TAG_SHIFT)) >> (64 - TAG_SHIFT))

typedef struct { uint8_t sub_tag; int64_t refcount; } PitonHeader;

static inline int64_t pv_encode(int tag, int64_t payload) {
    return ((int64_t)tag << TAG_SHIFT) | (payload & PAYLOAD_MASK);
}

static inline int pv_tag(int64_t v) { return (int)((v >> TAG_SHIFT) & 7); }
static inline int64_t pv_payload(int64_t v) { return v & PAYLOAD_MASK; }
static inline int64_t pv_payload_signed(int64_t v) { return SIGN_EXTEND(pv_payload(v)); }

static inline int64_t pv_none(void)  { return pv_encode(PITON_TAG_NONE, 0); }
static inline int64_t pv_bool(int b) { return pv_encode(PITON_TAG_BOOL, b ? 1 : 0); }
static inline int64_t pv_int(int64_t i) { return pv_encode(PITON_TAG_INT, i); }

static inline PitonHeader *pv_header_from_ptr(void *ptr) {
    return (PitonHeader *)ptr;
}

/* ── PitonStr ─────────────────────────────────────────────────────────── */

typedef struct {
    PitonHeader header;
    int64_t len;
    char *data;
} PitonStr;

static void *piton_str_new(const char *s, int64_t len) {
    PitonStr *str = calloc(1, sizeof(*str));
    str->header.sub_tag = SUB_TAG_STR;
    str->header.refcount = 1;
    str->len = len;
    str->data = malloc((size_t)len + 1);
    if (s && len > 0) memcpy(str->data, s, (size_t)len);
    str->data[len] = '\0';
    return str;
}

/* ── PitonCollection (list / tuple) ──────────────────────────────────── */

typedef struct {
    int64_t key;
    int64_t value;
} PitonEntry;

typedef struct {
    PitonHeader header;
    int64_t kind;     /* 1=list, 2=tuple */
    int64_t length;
    int64_t capacity;
    int64_t *items;   /* encoded PitonValues */
} PitonCollection;

static int64_t live_collections = 0;

/* ── PitonDict ────────────────────────────────────────────────────────── */

typedef struct {
    int64_t key;
    int64_t value;
} PitonDictEntry;

typedef struct {
    PitonHeader header;
    int64_t length;
    int64_t capacity;
    PitonDictEntry *entries; /* encoded PitonValues */
} PitonDict;

static int64_t live_dicts = 0;

/* ── PitonSet ─────────────────────────────────────────────────────────── */

typedef struct {
    PitonHeader header;
    int64_t length;
    int64_t capacity;
    int64_t *items; /* encoded PitonValues */
} PitonSet;

static int64_t live_sets = 0;

/* ── PitonObject ──────────────────────────────────────────────────────── */

typedef struct {
    const char *name;
    int64_t value;  /* encoded PitonValue */
} PitonAttribute;

typedef struct {
    PitonHeader header;
    const char *class_name;
    const char *parent_class_name;  /* NULL if no parent */
    int64_t length;
    PitonAttribute attributes[16];
} PitonObject;

static int64_t live_objects = 0;

/* ── PitonBigInt ──────────────────────────────────────────────────────── */

typedef struct {
    PitonHeader header;
    int sign;
    uint64_t *limbs;
    int64_t count;
    int64_t capacity;
} PitonBigInt;

/* ── Forward decls for deep free/print ────────────────────────────────── */

static void piton_value_deep_free(int64_t v);
static void piton_value_print_inner(int64_t v, int recursing);
static int piton_value_eq_raw(int64_t a, int64_t b);

/* ── Refcount ──────────────────────────────────────────────────────────── */

void piton_value_incref(int64_t v) {
    if (pv_tag(v) != PITON_TAG_OBJECT) return;
    void *ptr = (void *)pv_payload(v);
    if (!ptr) return;
    PitonHeader *h = pv_header_from_ptr(ptr);
    ++h->refcount;
}

void piton_value_decref(int64_t v) {
    piton_value_deep_free(v);
}

/* ── BigInt helpers ────────────────────────────────────────────────────── */

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

static void *piton_bigint_from_i64_impl(int64_t value) {
    PitonBigInt *a = calloc(1, sizeof(*a));
    a->header.sub_tag = SUB_TAG_BIGINT;
    a->header.refcount = 1;
    a->sign = value < 0 ? -1 : 1;
    uint64_t abs_val = value < 0 ? (uint64_t)(-(value + 1)) + 1 : (uint64_t)value;
    if (abs_val) { bi_ensure(a, 1); a->limbs[0] = abs_val; a->count = 1; }
    return a;
}

static int bi_cmp_mag(PitonBigInt *a, PitonBigInt *b) {
    if (a->count != b->count) return a->count < b->count ? -1 : 1;
    for (int64_t i = a->count - 1; i >= 0; --i)
        if (a->limbs[i] != b->limbs[i]) return a->limbs[i] < b->limbs[i] ? -1 : 1;
    return 0;
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

static void *bi_div_mod(void *a, void *b, void **rem_out) {
    PitonBigInt *dividend = a, *divisor = b;
    PitonBigInt *q = piton_bigint_from_i64_impl(0);

    if (bi_cmp_mag(dividend, divisor) < 0) {
        if (rem_out) {
            PitonBigInt *r = piton_bigint_from_i64_impl(0);
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
        free(((PitonBigInt*)q)->limbs); free(q);
        q = piton_bigint_from_i64_impl(1);
        if (rem_out) *rem_out = piton_bigint_from_i64_impl(0);
        return q;
    }

    int64_t n_bits = bi_bit_width(dividend);
    int64_t d_bits = bi_bit_width(divisor);
    PitonBigInt *work = piton_bigint_from_i64_impl(0);
    int64_t q_bits = n_bits - d_bits + 1;
    if (q_bits > 0) { bi_ensure(q, (q_bits / 64) + 1); q->count = (q_bits / 64) + 1; }

    for (int64_t i = n_bits - 1; i >= 0; --i) {
        int64_t limb = i / 64;
        int bit = i % 64;
        int b = (limb < dividend->count) ? (int)((dividend->limbs[limb] >> bit) & 1) : 0;
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
            int64_t ql = i / 64, qb = i % 64;
            bi_ensure(q, ql + 1);
            if (q->count <= ql) q->count = ql + 1;
            q->limbs[ql] |= (1ULL << qb);
        }
    }
    bi_trim(q); bi_trim(work);
    if (rem_out) {
        if (work->count == 0) { free(work->limbs); free(work); *rem_out = piton_bigint_from_i64_impl(0); }
        else *rem_out = work;
    } else { free(work->limbs); free(work); }
    return q;
}

/* ── Deep equality ─────────────────────────────────────────────────────── */

static int piton_value_eq_raw(int64_t a, int64_t b) {
    if (a == b) return 1;  /* same encoding = same value (covers NONE, BOOL, INT) */
    int ta = pv_tag(a), tb = pv_tag(b);
    if (ta != tb) return 0;
    int64_t pa = pv_payload(a), pb = pv_payload(b);
    switch (ta) {
    case PITON_TAG_INT: return pv_payload_signed(a) == pv_payload_signed(b);
    case PITON_TAG_FLOAT: return *(double*)pa == *(double*)pb;
    case PITON_TAG_OBJECT: {
        PitonHeader *ha = (PitonHeader *)pa, *hb = (PitonHeader *)pb;
        if (ha->sub_tag != hb->sub_tag) return 0;
        switch (ha->sub_tag) {
        case SUB_TAG_STR: {
            PitonStr *sa = (PitonStr *)pa, *sb = (PitonStr *)pb;
            return sa->len == sb->len && memcmp(sa->data, sb->data, (size_t)sa->len) == 0;
        }
        case SUB_TAG_LIST: case SUB_TAG_TUPLE: {
            PitonCollection *ca = (PitonCollection *)pa, *cb = (PitonCollection *)pb;
            if (ca->length != cb->length) return 0;
            for (int64_t i = 0; i < ca->length; ++i)
                if (!piton_value_eq_raw(ca->items[i], cb->items[i])) return 0;
            return 1;
        }
        default: return pa == pb;
        }
    }
    default: return 0;
    }
}

int piton_value_eq(int64_t a, int64_t b) { return piton_value_eq_raw(a, b); }

/* ── Deep free ─────────────────────────────────────────────────────────── */

static void piton_value_deep_free(int64_t v) {
    if (pv_tag(v) != PITON_TAG_OBJECT) return;
    void *ptr = (void *)pv_payload(v);
    if (!ptr) return;
    PitonHeader *h = pv_header_from_ptr(ptr);
    if (--h->refcount > 0) return;
    switch (h->sub_tag) {
    case SUB_TAG_STR: {
        PitonStr *s = ptr;
        free(s->data); free(s);
        break;
    }
    case SUB_TAG_LIST: case SUB_TAG_TUPLE: {
        PitonCollection *c = ptr;
        for (int64_t i = 0; i < c->length; ++i) piton_value_deep_free(c->items[i]);
        free(c->items); free(c);
        --live_collections;
        break;
    }
    case SUB_TAG_DICT: {
        PitonDict *d = ptr;
        for (int64_t i = 0; i < d->length; ++i) {
            piton_value_deep_free(d->entries[i].key);
            piton_value_deep_free(d->entries[i].value);
        }
        free(d->entries); free(d);
        --live_dicts;
        break;
    }
    case SUB_TAG_SET: {
        PitonSet *s = ptr;
        for (int64_t i = 0; i < s->length; ++i) piton_value_deep_free(s->items[i]);
        free(s->items); free(s);
        --live_sets;
        break;
    }
    case SUB_TAG_BIGINT: {
        PitonBigInt *bi = ptr;
        free(bi->limbs); free(bi);
        break;
    }
    default: free(ptr); break;
    }
}

/* ── Print (recursive) ─────────────────────────────────────────────────── */

static void piton_value_print_inner(int64_t v, int recursing) {
    switch (pv_tag(v)) {
    case PITON_TAG_NONE:
        fputs("None", stdout);
        break;
    case PITON_TAG_BOOL:
        fputs(pv_payload(v) ? "True" : "False", stdout);
        break;
    case PITON_TAG_INT:
        printf("%lld", (long long)pv_payload_signed(v));
        break;
    case PITON_TAG_FLOAT: {
        double d = *(double *)pv_payload(v);
        if (isfinite(d) && trunc(d) == d) printf("%.1f", d);
        else printf("%.15g", d);
        break;
    }
    case PITON_TAG_OBJECT: {
        void *ptr = (void *)pv_payload(v);
        PitonHeader *h = ptr ? (PitonHeader *)ptr : NULL;
        if (!h) { printf("<null>"); break; }
        switch (h->sub_tag) {
        case SUB_TAG_STR: {
            PitonStr *s = ptr;
            fwrite(s->data, 1, (size_t)s->len, stdout);
            break;
        }
        case SUB_TAG_LIST: case SUB_TAG_TUPLE: {
            PitonCollection *c = ptr;
            char o = c->kind == 1 ? '[' : '(';
            char cl = c->kind == 1 ? ']' : ')';
            putchar(o);
            for (int64_t i = 0; i < c->length; ++i) {
                if (i) fputs(", ", stdout);
                piton_value_print_inner(c->items[i], 1);
            }
            if (c->kind == 2 && c->length == 1) putchar(',');
            putchar(cl);
            break;
        }
        case SUB_TAG_DICT: {
            PitonDict *d = ptr;
            putchar('{');
            for (int64_t i = 0; i < d->length; ++i) {
                if (i) fputs(", ", stdout);
                piton_value_print_inner(d->entries[i].key, 1);
                fputs(": ", stdout);
                piton_value_print_inner(d->entries[i].value, 1);
            }
            putchar('}');
            break;
        }
        case SUB_TAG_SET: {
            PitonSet *s = ptr;
            putchar('{');
            for (int64_t i = 0; i < s->length; ++i) {
                if (i) fputs(", ", stdout);
                piton_value_print_inner(s->items[i], 1);
            }
            putchar('}');
            break;
        }
        case SUB_TAG_BIGINT: {
            PitonBigInt *bi = ptr;
            if (!bi || bi->count == 0) { printf("0"); break; }
            char buf[128]; int pos = 128; buf[--pos] = '\0';
            PitonBigInt *ten = piton_bigint_from_i64_impl(10);
            PitonBigInt *work = piton_bigint_from_i64_impl(0);
            free(work->limbs); free(work);
            work = calloc(1, sizeof(*work));
            work->header = (PitonHeader){SUB_TAG_BIGINT, 1};
            bi_ensure(work, bi->count);
            memcpy(work->limbs, bi->limbs, (size_t)bi->count * sizeof(uint64_t));
            work->count = bi->count; work->sign = 1;
            while (work->count > 0) {
                void *rem = NULL;
                void *q = bi_div_mod(work, ten, &rem);
                PitonBigInt *rr = rem;
                int digit = (rr && rr->count > 0) ? (int)rr->limbs[0] : 0;
                if (rr) { free(rr->limbs); free(rr); }
                free(work->limbs); free(work);
                work = q;
                buf[--pos] = '0' + digit;
            }
            free(work->limbs); free(work);
            free(ten->limbs); free(ten);
            if (bi->sign < 0) buf[--pos] = '-';
            printf("%s", buf + pos);
            break;
        }
        default:
            printf("<object@%p>", ptr);
            break;
        }
        break;
    }
    default:
        printf("<unknown>");
        break;
    }
}

/* ── Collection API ────────────────────────────────────────────────────── */

void *piton_collection_new(int64_t kind, int64_t capacity) {
    if (capacity < 0) capacity = 0;
    PitonCollection *c = calloc(1, sizeof(*c));
    c->header.sub_tag = (kind == 1) ? SUB_TAG_LIST : SUB_TAG_TUPLE;
    c->header.refcount = 1;
    c->kind = kind;
    c->capacity = capacity;
    if (capacity > 0) c->items = calloc((size_t)capacity, sizeof(int64_t));
    ++live_collections;
    return c;
}

void piton_collection_put(void *raw, int64_t index, int64_t key, int64_t value) {
    PitonCollection *c = raw;
    if (!c) return;
    if (index < 0 || index >= c->capacity) return;
    c->items[index] = pv_int(value);  /* auto-encode raw int as PitonValue */
    if (index >= c->length) c->length = index + 1;
}

int64_t piton_collection_len(void *raw) {
    PitonCollection *c = raw;
    return c ? c->length : 0;
}

int64_t piton_collection_get(void *raw, int64_t key) {
    PitonCollection *c = raw;
    if (!c) return pv_none();
    int64_t idx = pv_payload_signed(key);
    if (idx < 0) idx += c->length;
    if (idx < 0 || idx >= c->length) return pv_none();
    int64_t v = c->items[idx];
    /* decode: return raw int for INT/BOOL, return pointer for OBJECT */
    if (pv_tag(v) == PITON_TAG_INT) return pv_payload_signed(v);
    if (pv_tag(v) == PITON_TAG_BOOL) return pv_payload(v) ? 1 : 0;
    return v;  /* OBJECT/FLOAT: return as-is (pointer) */
}

void piton_collection_print(void *raw) {
    piton_value_print_inner(pv_encode(PITON_TAG_OBJECT, (int64_t)raw), 0);
    putchar('\n');
}

void piton_collection_free(void *raw) {
    if (!raw) return;
    piton_value_deep_free(pv_encode(PITON_TAG_OBJECT, (int64_t)raw));
}

int64_t piton_collection_live_count(void) { return live_collections; }

/* ── Dict API ──────────────────────────────────────────────────────────── */

void *piton_dict_new(int64_t capacity) {
    if (capacity < 0) capacity = 0;
    PitonDict *d = calloc(1, sizeof(*d));
    d->header.sub_tag = SUB_TAG_DICT;
    d->header.refcount = 1;
    d->capacity = capacity;
    if (capacity > 0) d->entries = calloc((size_t)capacity, sizeof(PitonDictEntry));
    ++live_dicts;
    return d;
}

void piton_dict_put(void *raw, int64_t key, int64_t value) {
    PitonDict *d = raw;
    if (!d) return;
    int64_t ek = pv_int(key), ev = pv_int(value);  /* auto-encode */
    for (int64_t i = 0; i < d->length; ++i)
        if (piton_value_eq_raw(d->entries[i].key, ek)) {
            piton_value_deep_free(d->entries[i].value);
            d->entries[i].value = ev;
            return;
        }
    if (d->length >= d->capacity) {
        int64_t new_cap = d->capacity ? d->capacity * 2 : 4;
        d->entries = realloc(d->entries, (size_t)new_cap * sizeof(PitonDictEntry));
        memset(d->entries + d->capacity, 0, (size_t)(new_cap - d->capacity) * sizeof(PitonDictEntry));
        d->capacity = new_cap;
    }
    d->entries[d->length].key = ek;
    d->entries[d->length].value = ev;
    ++d->length;
}

int64_t piton_dict_get(void *raw, int64_t key) {
    PitonDict *d = raw;
    if (!d) return pv_none();
    int64_t ek = pv_int(key);
    for (int64_t i = 0; i < d->length; ++i)
        if (piton_value_eq_raw(d->entries[i].key, ek)) {
            int64_t v = d->entries[i].value;
            if (pv_tag(v) == PITON_TAG_INT) return pv_payload_signed(v);
            if (pv_tag(v) == PITON_TAG_BOOL) return pv_payload(v) ? 1 : 0;
            return v;
        }
    return pv_none();
}

int64_t piton_dict_len(void *raw) {
    PitonDict *d = raw;
    return d ? d->length : 0;
}

void piton_dict_print(void *raw) {
    piton_value_print_inner(pv_encode(PITON_TAG_OBJECT, (int64_t)raw), 0);
    putchar('\n');
}

void piton_dict_free(void *raw) {
    if (!raw) return;
    piton_value_deep_free(pv_encode(PITON_TAG_OBJECT, (int64_t)raw));
}

int64_t piton_dict_live_count(void) { return live_dicts; }

/* ── Set API ───────────────────────────────────────────────────────────── */

void *piton_set_new(int64_t capacity) {
    if (capacity < 0) capacity = 0;
    PitonSet *s = calloc(1, sizeof(*s));
    s->header.sub_tag = SUB_TAG_SET;
    s->header.refcount = 1;
    s->capacity = capacity;
    if (capacity > 0) s->items = calloc((size_t)capacity, sizeof(int64_t));
    ++live_sets;
    return s;
}

void piton_set_add(void *raw, int64_t value) {
    PitonSet *s = raw;
    if (!s) return;
    int64_t ev = pv_int(value);  /* auto-encode */
    for (int64_t i = 0; i < s->length; ++i)
        if (piton_value_eq_raw(s->items[i], ev)) return;
    if (s->length >= s->capacity) {
        int64_t new_cap = s->capacity ? s->capacity * 2 : 4;
        s->items = realloc(s->items, (size_t)new_cap * sizeof(int64_t));
        memset(s->items + s->capacity, 0, (size_t)(new_cap - s->capacity) * sizeof(int64_t));
        s->capacity = new_cap;
    }
    s->items[s->length++] = ev;
}

int64_t piton_set_len(void *raw) {
    PitonSet *s = raw;
    return s ? s->length : 0;
}

void piton_set_print(void *raw) {
    piton_value_print_inner(pv_encode(PITON_TAG_OBJECT, (int64_t)raw), 0);
    putchar('\n');
}

void piton_set_free(void *raw) {
    if (!raw) return;
    piton_value_deep_free(pv_encode(PITON_TAG_OBJECT, (int64_t)raw));
}

int64_t piton_set_live_count(void) { return live_sets; }

/* ── Closure escape (CLOSURES_COMPLETE_V1) ───────────────────────────── */

#define PITON_CLOSURE_MAGIC 0x5049544EC10557LL

typedef struct {
    int64_t magic;
    int64_t addr;
    int64_t n_args;
    int64_t n_cells;
    int64_t cells[4];
} PitonClosure;

int64_t piton_closure_new8(int64_t addr, int64_t n_args, int64_t n_cells,
                           int64_t c0, int64_t c1, int64_t c2, int64_t c3) {
    PitonClosure *c = calloc(1, sizeof(PitonClosure));
    c->magic = PITON_CLOSURE_MAGIC;
    c->addr = addr;
    c->n_args = n_args;
    c->n_cells = n_cells;
    int64_t cs[4] = {c0, c1, c2, c3};
    for (int i = 0; i < n_cells && i < 4; i++) c->cells[i] = cs[i];
    return (int64_t)c;
}

int64_t piton_closure_call6(int64_t callee, int64_t argc,
                            int64_t a0, int64_t a1, int64_t a2, int64_t a3) {
    if (!callee || ((int64_t *)callee)[0] != PITON_CLOSURE_MAGIC)
        return ((int64_t(*)(int64_t, int64_t, int64_t, int64_t))callee)(a0, a1, a2, a3);
    PitonClosure *c = (PitonClosure *)callee;
    if (argc != c->n_args) {
        fprintf(stderr, "TypeError: closure called with wrong number of arguments\n");
        exit(2);
    }
    int64_t total = c->n_cells + argc;
    if (total > 4) {
        fprintf(stderr, "TypeError: closure cell count plus arguments exceeds four\n");
        exit(2);
    }
    int64_t x[4] = {a0, a1, a2, a3};
    for (int i = 0; i < c->n_cells && i < 4; i++) {
        for (int j = 3; j > i; j--) x[j] = x[j - 1];
        x[i] = c->cells[i];
    }
    return ((int64_t(*)(int64_t, int64_t, int64_t, int64_t))c->addr)(x[0], x[1], x[2], x[3]);
}

/* ── Object API ────────────────────────────────────────────────────────── */

void *piton_object_new(const char *class_name) {
    PitonObject *o = calloc(1, sizeof(*o));
    o->header.sub_tag = 0;
    o->header.refcount = 1;
    o->class_name = class_name;
    o->parent_class_name = NULL;
    ++live_objects;
    return o;
}

void *piton_object_new_with_parent(const char *class_name, const char *parent_class_name) {
    PitonObject *o = calloc(1, sizeof(*o));
    o->header.sub_tag = 0;
    o->header.refcount = 1;
    o->class_name = class_name;
    o->parent_class_name = parent_class_name;
    ++live_objects;
    return o;
}

const char *piton_object_class_name(void *raw) {
    PitonObject *o = raw;
    return o ? o->class_name : NULL;
}

const char *piton_object_parent_class_name(void *raw) {
    PitonObject *o = raw;
    return o ? o->parent_class_name : NULL;
}

void piton_object_set(void *raw, const char *name, int64_t value) {
    PitonObject *o = raw;
    if (!o || !name) return;
    int64_t ev = pv_int(value);  /* auto-encode */
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) {
            piton_value_deep_free(o->attributes[i].value);
            o->attributes[i].value = ev;
            return;
        }
    if (o->length >= 16) return;
    o->attributes[o->length].name = name;
    o->attributes[o->length].value = ev;
    ++o->length;
}

int64_t piton_object_get(void *raw, const char *name) {
    PitonObject *o = raw;
    if (!o || !name) return pv_none();
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) {
            int64_t v = o->attributes[i].value;
            if (pv_tag(v) == PITON_TAG_INT) return pv_payload_signed(v);
            if (pv_tag(v) == PITON_TAG_BOOL) return pv_payload(v) ? 1 : 0;
            return v;
        }
    return pv_none();
}

void piton_object_free(void *raw) {
    if (!raw) return;
    PitonObject *o = raw;
    for (int64_t i = 0; i < o->length; ++i)
        piton_value_deep_free(o->attributes[i].value);
    free(o);
    --live_objects;
}

int64_t piton_object_live_count(void) { return live_objects; }

/* ── BigInt API (unchanged logic) ─────────────────────────────────────── */

void *piton_bigint_from_i64(int64_t value) { return piton_bigint_from_i64_impl(value); }

void *piton_bigint_from_str(const char *s) {
    PitonBigInt *a = calloc(1, sizeof(*a));
    a->header.sub_tag = SUB_TAG_BIGINT;
    a->header.refcount = 1;
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
        if (carry) { bi_ensure(a, a->count + 1); a->limbs[a->count++] = (uint64_t)carry; }
        carry = digit;
        for (int64_t i = 0; i < a->count && carry; ++i) {
            carry += a->limbs[i];
            a->limbs[i] = (uint64_t)carry;
            carry >>= 64;
        }
        if (carry) { bi_ensure(a, a->count + 1); a->limbs[a->count++] = (uint64_t)carry; }
    }
    bi_trim(a);
    return a;
}

void piton_bigint_free(void *a) { if (a) { free(((PitonBigInt*)a)->limbs); free(a); } }

int64_t piton_bigint_cmp(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    if (x->sign != y->sign) return x->sign < y->sign ? -1 : 1;
    int c = bi_cmp_mag(x, y);
    return x->sign < 0 ? -c : c;
}

void *piton_bigint_neg(void *a) {
    PitonBigInt *x = a;
    PitonBigInt *r = piton_bigint_from_i64_impl(0);
    r->sign = -x->sign;
    if (x->count > 0) {
        bi_ensure(r, x->count);
        memcpy(r->limbs, x->limbs, (size_t)x->count * sizeof(uint64_t));
        r->count = x->count;
    }
    bi_trim(r);
    return r;
}

void *piton_bigint_add(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    PitonBigInt *r = piton_bigint_from_i64_impl(0);
    if (x->sign == y->sign) { r->sign = x->sign; bi_add_mag(r, x, y); }
    else {
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
    PitonBigInt *r = piton_bigint_from_i64_impl(0);
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

void *piton_bigint_floor_div(void *a, void *b) {
    PitonBigInt *x = a, *y = b;
    if (y->count == 0) { fprintf(stderr, "ZeroDivisionError: division by zero\n"); exit(1); }
    PitonBigInt *abs_x = piton_bigint_neg(x); abs_x->sign = 1;
    PitonBigInt *abs_y = piton_bigint_neg(y); abs_y->sign = 1;
    void *q = bi_div_mod(abs_x, abs_y, NULL);
    piton_bigint_free(abs_x); piton_bigint_free(abs_y);
    if (x->sign * y->sign < 0) {
        PitonBigInt *ax = piton_bigint_neg(x); ax->sign = 1;
        PitonBigInt *ay = piton_bigint_neg(y); ay->sign = 1;
        void *rem = NULL;
        piton_bigint_free(q);
        q = bi_div_mod(ax, ay, &rem);
        PitonBigInt *rr = rem;
        int has_rem = rr && rr->count > 0;
        piton_bigint_free(ax); piton_bigint_free(ay);
        if (has_rem) {
            PitonBigInt *one = piton_bigint_from_i64_impl(1);
            void *nq = piton_bigint_sub(q, one);
            piton_bigint_free(q); piton_bigint_free(one);
            q = nq;
        }
        if (rr) { free(rr->limbs); free(rr); }
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
    piton_bigint_free(ax); piton_bigint_free(ay); piton_bigint_free(q);
    PitonBigInt *r = rem;
    if (!r) r = piton_bigint_from_i64_impl(0);
    r->sign = y->sign;
    if (r->count == 0) r->sign = 1;
    return r;
}

void piton_bigint_print(void *a) {
    PitonBigInt *bi = a;
    if (!bi || bi->count == 0) { puts("0"); return; }
    char buf[128]; int pos = 128; buf[--pos] = '\0';
    PitonBigInt *ten = piton_bigint_from_i64_impl(10);
    PitonBigInt *work = piton_bigint_from_i64_impl(0);
    free(work->limbs); free(work);
    work = calloc(1, sizeof(*work));
    work->header = (PitonHeader){SUB_TAG_BIGINT, 1};
    bi_ensure(work, bi->count);
    memcpy(work->limbs, bi->limbs, (size_t)bi->count * sizeof(uint64_t));
    work->count = bi->count; work->sign = 1;
    while (work->count > 0) {
        void *rem = NULL;
        void *q = bi_div_mod(work, ten, &rem);
        PitonBigInt *rr = rem;
        int digit = (rr && rr->count > 0) ? (int)rr->limbs[0] : 0;
        if (rr) { free(rr->limbs); free(rr); }
        free(work->limbs); free(work);
        work = q;
        buf[--pos] = '0' + digit;
    }
    free(work->limbs); free(work);
    free(ten->limbs); free(ten);
    if (bi->sign < 0) buf[--pos] = '-';
    puts(buf + pos);
}

/* ── Exception handler stack (flag-based, no longjmp) ──────────────────── */

#define PITON_MAX_HANDLERS 64

typedef struct {
    const char *accepted;  /* NULL = catch-all, or exception type name */
} PitonHandler;

static PitonHandler handler_stack[PITON_MAX_HANDLERS];
static int handler_sp = 0;

/* Current exception state */
static int piton_exception_active = 0;
static const char *piton_exception_type = NULL;
static const char *piton_exception_message = NULL;

/* Register a handler. Always returns 0. */
int64_t piton_try_push(void) {
    if (handler_sp >= PITON_MAX_HANDLERS) {
        fprintf(stderr, "RuntimeError: too many nested try blocks\n");
        exit(1);
    }
    handler_stack[handler_sp].accepted = NULL;
    ++handler_sp;
    return 0;
}

/* Pop the current handler. */
void piton_try_pop(void) {
    if (handler_sp > 0) --handler_sp;
}

/* Set the accepted type for the current handler. */
void piton_try_set_accepted(const char *type) {
    if (handler_sp > 0)
        handler_stack[handler_sp - 1].accepted = type;
}

/* Raise an exception. If a matching handler exists, set the flag. Otherwise exit. */
void piton_raise(const char *type, const char *message) {
    /* Search handler stack in reverse (most recent first) */
    for (int i = handler_sp - 1; i >= 0; --i) {
        PitonHandler *h = &handler_stack[i];
        if (h->accepted == NULL ||
            strcmp(h->accepted, type) == 0 ||
            strcmp(h->accepted, "Exception") == 0) {
            piton_exception_active = 1;
            piton_exception_type = type;
            piton_exception_message = message;
            return;
        }
    }
    /* No handler found — print and exit */
    fprintf(stderr, "%s", type ? type : "Exception");
    if (message && *message) fprintf(stderr, ": %s", message);
    fputc('\n', stderr);
    exit(1);
}

/* Check if an exception was caught (for the emitter to test after try_push). */
int64_t piton_catch_flag(void) {
    return piton_exception_active ? 1 : 0;
}

/* Get the caught exception type name (for exception binding). */
const char *piton_catch_type(void) {
    return piton_exception_type;
}

/* Get the caught exception message (for exception binding). */
const char *piton_catch_message(void) {
    return piton_exception_message;
}

/* Clear the catch state after handling. */
void piton_catch_clear(void) {
    piton_exception_active = 0;
    piton_exception_type = NULL;
    piton_exception_message = NULL;
}

void piton_print_float(double value) {
    if (isfinite(value) && trunc(value) == value)
        printf("%.1f\n", value);
    else
        printf("%.15g\n", value);
}

/* ── Stdlib: abs, min, max, sum, type ────────────────────────────────── */

int64_t piton_abs_int(int64_t x) {
    return x < 0 ? -x : x;
}

double piton_abs_float(double x) {
    return fabs(x);
}

int64_t piton_min_int(int64_t a, int64_t b) {
    return a < b ? a : b;
}

int64_t piton_max_int(int64_t a, int64_t b) {
    return a > b ? a : b;
}

double piton_min_float(double a, double b) {
    return a < b ? a : b;
}

double piton_max_float(double a, double b) {
    return a > b ? a : b;
}

int64_t piton_sum_collection(void *raw) {
    PitonCollection *c = raw;
    if (!c) return 0;
    int64_t total = 0;
    for (int64_t i = 0; i < c->length; ++i) {
        int64_t v = c->items[i];
        if (pv_tag(v) == PITON_TAG_INT)
            total += pv_payload_signed(v);
        else if (pv_tag(v) == PITON_TAG_FLOAT) {
            double *fp = (double *)(uintptr_t)pv_payload(v);
            total += (int64_t)*fp;
        }
    }
    return total;
}

int64_t piton_sum_dict(void *raw) {
    PitonDict *d = raw;
    if (!d) return 0;
    int64_t total = 0;
    for (int64_t i = 0; i < d->length; ++i) {
        int64_t v = d->entries[i].value;
        if (pv_tag(v) == PITON_TAG_INT)
            total += pv_payload_signed(v);
    }
    return total;
}

int64_t piton_sum_set(void *raw) {
    PitonSet *s = raw;
    if (!s) return 0;
    int64_t total = 0;
    for (int64_t i = 0; i < s->length; ++i) {
        int64_t v = s->items[i];
        if (pv_tag(v) == PITON_TAG_INT)
            total += pv_payload_signed(v);
    }
    return total;
}

const char *piton_type_name(int64_t value) {
    uint8_t tag = (uint8_t)((uint64_t)value >> 61);
    switch (tag) {
        case PITON_TAG_NONE:   return "NoneType";
        case PITON_TAG_BOOL:   return "bool";
        case PITON_TAG_INT:    return "int";
        case PITON_TAG_FLOAT:  return "float";
        case PITON_TAG_OBJECT: {
            int64_t ptr = (int64_t)((uint64_t)value & 0x1FFFFFFFFFFFFFFFULL);
            if (!ptr) return "NoneType";
            PitonHeader *h = (PitonHeader *)(uintptr_t)ptr;
            switch (h->sub_tag) {
                case SUB_TAG_STR:      return "str";
                case SUB_TAG_LIST:     return "list";
                case SUB_TAG_TUPLE:    return "tuple";
                case SUB_TAG_DICT:     return "dict";
                case SUB_TAG_SET:      return "set";
                case SUB_TAG_BIGINT:   return "int";
                default:               return "object";
            }
        }
        default: return "unknown";
    }
}

/* Type name from raw pointer + explicit type tag (for emitter-passed values) */
const char *piton_type_from_raw(int64_t raw_ptr, int64_t type_tag) {
    switch (type_tag) {
        case 0: return "<class 'NoneType'>";
        case 1: return "<class 'bool'>";
        case 2: return "<class 'int'>";
        case 3: return "<class 'float'>";
        case 5: return "<class 'str'>";
        case 6: return "<class 'list'>";
        case 7: return "<class 'tuple'>";
        case 8: return "<class 'dict'>";
        case 9: return "<class 'set'>";
        case 10: return "<class 'int'>";
        default: return "<class 'object'>";
    }
}

/* ── Live count totals ────────────────────────────────────────────────── */

int64_t piton_total_live_count(void) {
    return live_collections + live_objects + live_dicts + live_sets;
}
