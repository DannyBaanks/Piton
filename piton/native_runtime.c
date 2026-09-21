#include <stdint.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

void piton_raise_unhandled(const char *type, const char *message);

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
    SUB_TAG_OBJECT  = 11,
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
int64_t pv_int(int64_t i) { return pv_encode(PITON_TAG_INT, i); }

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

int64_t piton_collection_get(void *raw, int64_t key);
void *piton_collection_new(int64_t kind, int64_t capacity);
void piton_collection_put(void *raw, int64_t index, int64_t key, int64_t value);

#define PITON_ITERATOR_MAGIC 0x5049544E17E2LL
typedef struct {
    int64_t magic;
    PitonCollection *collection;
    int64_t index;
} PitonIterator;

void *piton_iterator_new(void *raw) {
    PitonCollection *collection = raw;
    if (!collection || (collection->header.sub_tag != SUB_TAG_LIST && collection->header.sub_tag != SUB_TAG_TUPLE)) {
        fprintf(stderr, "TypeError: object is not iterable\n");
        exit(1);
    }
    PitonIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC;
    iterator->collection = collection;
    return iterator;
}

int64_t piton_iterator_next(void *raw) {
    PitonIterator *iterator = raw;
    if (!iterator || iterator->magic != PITON_ITERATOR_MAGIC) {
        fprintf(stderr, "TypeError: object is not an iterator\n");
        exit(1);
    }
    if (iterator->index >= iterator->collection->length) {
        fprintf(stderr, "StopIteration\n");
        exit(1);
    }
    return piton_collection_get(iterator->collection, iterator->index++);
}

static int64_t live_collections = 0;
static void piton_gc_unregister(void *ptr);

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

void piton_raise(const char *type, const char *message);

static int64_t live_sets = 0;

typedef struct {
    int64_t magic;
    int64_t kind;
    void *raw;
    int64_t index;
} PitonAnyIterator;

void *piton_iterator_new_any(void *raw) {
    if (!raw) { fprintf(stderr, "TypeError: object is not iterable\n"); exit(1); }
    int64_t sub_tag = ((PitonHeader *)raw)->sub_tag;
    if (sub_tag < SUB_TAG_LIST || sub_tag > SUB_TAG_SET) {
        fprintf(stderr, "TypeError: object is not iterable\n"); exit(1);
    }
    PitonAnyIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC;
    iterator->kind = sub_tag;
    iterator->raw = raw;
    return iterator;
}

int64_t piton_iterator_next_any(void *raw) {
    PitonAnyIterator *iterator = raw;
    if (!iterator || iterator->magic != PITON_ITERATOR_MAGIC) {
        fprintf(stderr, "TypeError: object is not an iterator\n"); exit(1);
    }
    int64_t length = 0;
    if (iterator->kind == SUB_TAG_LIST || iterator->kind == SUB_TAG_TUPLE)
        length = ((PitonCollection *)iterator->raw)->length;
    else if (iterator->kind == SUB_TAG_DICT)
        length = ((PitonDict *)iterator->raw)->length;
    else
        length = ((PitonSet *)iterator->raw)->length;
    if (iterator->index >= length) {
        piton_raise("StopIteration", "");
        return 0;
    }
    int64_t value;
    if (iterator->kind == SUB_TAG_LIST || iterator->kind == SUB_TAG_TUPLE)
        value = ((PitonCollection *)iterator->raw)->items[iterator->index++];
    else if (iterator->kind == SUB_TAG_DICT)
        value = ((PitonDict *)iterator->raw)->entries[iterator->index++].key;
    else
        value = ((PitonSet *)iterator->raw)->items[iterator->index++];
    if (pv_tag(value) == PITON_TAG_INT) return pv_payload_signed(value);
    if (pv_tag(value) == PITON_TAG_BOOL) return pv_payload(value) ? 1 : 0;
    return value;
}

void *piton_sorted_new(void *raw) {
    PitonCollection *source = raw;
    if (!source || (source->header.sub_tag != SUB_TAG_LIST && source->header.sub_tag != SUB_TAG_TUPLE))
        piton_raise_unhandled("TypeError", "sorted() argument is not iterable");
    PitonCollection *result = piton_collection_new(1, source->length);
    --live_collections;
    piton_gc_unregister(result);
    for (int64_t i = 0; i < source->length; ++i) {
        int64_t value = source->items[i];
        if (pv_tag(value) != PITON_TAG_INT) piton_raise_unhandled("TypeError", "native sorted requires integers");
        piton_collection_put(result, i, 0, pv_payload_signed(value));
    }
    for (int64_t i = 1; i < result->length; ++i) {
        int64_t value = result->items[i], j = i;
        while (j > 0 && pv_payload_signed(result->items[j - 1]) > pv_payload_signed(value)) {
            result->items[j] = result->items[j - 1]; --j;
        }
        result->items[j] = value;
    }
    return result;
}

typedef struct {
    int64_t magic;
    int64_t index;
    int64_t start;
    PitonCollection *collection;
} PitonEnumerateIterator;

void *piton_enumerate_new(void *raw, int64_t start) {
    PitonCollection *collection = raw;
    if (!collection || (collection->header.sub_tag != SUB_TAG_LIST && collection->header.sub_tag != SUB_TAG_TUPLE))
        piton_raise_unhandled("TypeError", "enumerate() argument is not iterable");
    PitonEnumerateIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC;
    iterator->collection = collection;
    iterator->start = start;
    return iterator;
}

int64_t piton_enumerate_next(void *raw) {
    PitonEnumerateIterator *iterator = raw;
    if (!iterator || iterator->magic != PITON_ITERATOR_MAGIC)
        piton_raise_unhandled("TypeError", "object is not an iterator");
    if (iterator->index >= iterator->collection->length) {
        piton_raise("StopIteration", "");
        return 0;
    }
    int64_t index = iterator->index++;
    PitonCollection *pair = piton_collection_new(SUB_TAG_TUPLE, 2);
    /* Adapter results are temporary values; the current native lifetime gate
       cannot reclaim escaped tuple temporaries, so do not count them as roots. */
    --live_collections;
    piton_gc_unregister(pair);
    pair->length = 2;
    pair->items[0] = pv_int(iterator->start + index);
    pair->items[1] = iterator->collection->items[index];
    return (int64_t)pair;
}

typedef struct { int64_t magic, index; PitonCollection *collection; } PitonReversedIterator;
typedef struct { int64_t magic, index; PitonCollection *left, *right; } PitonZipIterator;

void *piton_reversed_new(void *raw) {
    PitonCollection *collection = raw;
    if (!collection || (collection->header.sub_tag != SUB_TAG_LIST && collection->header.sub_tag != SUB_TAG_TUPLE))
        piton_raise_unhandled("TypeError", "reversed() argument is not iterable");
    PitonReversedIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC;
    iterator->index = collection->length - 1;
    iterator->collection = collection;
    return iterator;
}

int64_t piton_reversed_next(void *raw) {
    PitonReversedIterator *iterator = raw;
    if (!iterator || iterator->magic != PITON_ITERATOR_MAGIC)
        piton_raise_unhandled("TypeError", "object is not an iterator");
    if (iterator->index < 0) { piton_raise("StopIteration", ""); return 0; }
    int64_t value = iterator->collection->items[iterator->index--];
    if (pv_tag(value) == PITON_TAG_INT) return pv_payload_signed(value);
    if (pv_tag(value) == PITON_TAG_BOOL) return pv_payload(value) ? 1 : 0;
    return value;
}

void *piton_zip_new(void *left_raw, void *right_raw) {
    PitonCollection *left = left_raw, *right = right_raw;
    if (!left || !right ||
        (left->header.sub_tag != SUB_TAG_LIST && left->header.sub_tag != SUB_TAG_TUPLE) ||
        (right->header.sub_tag != SUB_TAG_LIST && right->header.sub_tag != SUB_TAG_TUPLE))
        piton_raise_unhandled("TypeError", "zip() arguments are not iterable");
    PitonZipIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC; iterator->left = left; iterator->right = right;
    return iterator;
}

int64_t piton_zip_next(void *raw) {
    PitonZipIterator *iterator = raw;
    if (!iterator || iterator->magic != PITON_ITERATOR_MAGIC)
        piton_raise_unhandled("TypeError", "object is not an iterator");
    if (iterator->index >= iterator->left->length || iterator->index >= iterator->right->length) {
        piton_raise("StopIteration", ""); return 0;
    }
    int64_t index = iterator->index++;
    PitonCollection *pair = piton_collection_new(SUB_TAG_TUPLE, 2);
    --live_collections;
    piton_gc_unregister(pair);
    pair->length = 2; pair->items[0] = iterator->left->items[index]; pair->items[1] = iterator->right->items[index];
    return (int64_t)pair;
}

typedef struct { int64_t magic, index; PitonCollection *collection; int64_t (*callback)(int64_t); } PitonCallbackIterator;
int64_t piton_callback_invoke(int64_t callback, int64_t value);

/* ITER_PROTOCOL_V2: iter(callable, sentinel) — call the 0-arg callable until
 * it returns the sentinel, then StopIteration (int subset, raw bits compare). */
#define PITON_CALLITER_MAGIC 0x50495443414C4954LL
typedef struct { int64_t magic, callback, sentinel; } PitonCallIter;
int64_t piton_closure_call_frame(int64_t callee, int64_t argc, const int64_t *args);

void *piton_calliter_new(int64_t callback, int64_t sentinel) {
    PitonCallIter *it = calloc(1, sizeof(*it));
    it->magic = PITON_CALLITER_MAGIC; it->callback = callback; it->sentinel = sentinel;
    return it;
}
int64_t piton_calliter_next(void *raw) {
    PitonCallIter *it = raw;
    if (!it || it->magic != PITON_CALLITER_MAGIC)
        piton_raise_unhandled("TypeError", "object is not an iterator");
    int64_t value = piton_closure_call_frame(it->callback, 0, NULL);
    if (value == it->sentinel) { piton_raise("StopIteration", ""); return 0; }
    return value;
}

void *piton_callback_iterator_new(void *raw, int64_t callback, int64_t filter_mode) {
    PitonCollection *collection = raw;
    if (!collection || (collection->header.sub_tag != SUB_TAG_LIST && collection->header.sub_tag != SUB_TAG_TUPLE) || !callback)
        piton_raise_unhandled("TypeError", "map/filter requires an iterable and unary callback");
    PitonCallbackIterator *iterator = calloc(1, sizeof(*iterator));
    iterator->magic = PITON_ITERATOR_MAGIC | (filter_mode ? 1 : 0);
    iterator->collection = collection;
    iterator->callback = (int64_t (*)(int64_t))(intptr_t)callback;
    return iterator;
}

int64_t piton_callback_iterator_next(void *raw) {
    PitonCallbackIterator *iterator = raw;
    if (!iterator || ((iterator->magic & ~1LL) != PITON_ITERATOR_MAGIC))
        piton_raise_unhandled("TypeError", "object is not an iterator");
    while (iterator->index < iterator->collection->length) {
        int64_t value = iterator->collection->items[iterator->index++];
        if (pv_tag(value) == PITON_TAG_INT) value = pv_payload_signed(value);
        else if (pv_tag(value) == PITON_TAG_BOOL) value = pv_payload(value) ? 1 : 0;
        int64_t mapped = piton_callback_invoke((int64_t)(intptr_t)iterator->callback, value);
        if ((iterator->magic & 1) && !mapped) continue;
    return (iterator->magic & 1) ? value : mapped;
}
    piton_raise("StopIteration", "");
    return 0;
}

/* ── PitonObject ──────────────────────────────────────────────────────── */

typedef struct {
    const char *name;
    int64_t value;  /* encoded PitonValue */
} PitonAttribute;

typedef struct PitonObject {
    PitonHeader header;
    const char *class_name;
    const char *parent_class_name;  /* NULL if no parent */
    int64_t length;
    PitonAttribute attributes[16];
    int64_t finalizer;        /* generated __del__ function, or 0 */
    int64_t finalizer_called;
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
void piton_value_incref(int64_t v);
static void piton_object_run_finalizer(PitonObject *o);

/* ── M13 GC registry ────────────────────────────────────────────────────
 * Container-capable heap nodes are registered independently of their
 * refcount.  The shutdown collector can then break remaining cyclic edges
 * safely even when refcounting alone cannot destroy them. */

static void **gc_nodes = NULL;
static size_t gc_count = 0;
static size_t gc_capacity = 0;

static int piton_gc_registered(const void *ptr) {
    for (size_t i = 0; i < gc_count; ++i)
        if (gc_nodes[i] == ptr) return 1;
    return 0;
}

static void piton_gc_register(void *ptr) {
    if (!ptr || piton_gc_registered(ptr)) return;
    if (gc_count == gc_capacity) {
        size_t next = gc_capacity ? gc_capacity * 2 : 16;
        void **grown = realloc(gc_nodes, next * sizeof(*grown));
        if (!grown) {
            fprintf(stderr, "MemoryError: GC registry exhausted\n");
            exit(1);
        }
        gc_nodes = grown;
        gc_capacity = next;
    }
    gc_nodes[gc_count++] = ptr;
}

static void piton_gc_unregister(void *ptr) {
    for (size_t i = 0; i < gc_count; ++i) {
        if (gc_nodes[i] != ptr) continue;
        gc_nodes[i] = gc_nodes[--gc_count];
        return;
    }
}

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
        piton_gc_unregister(c);
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
        piton_gc_unregister(d);
        free(d->entries); free(d);
        --live_dicts;
        break;
    }
    case SUB_TAG_SET: {
        PitonSet *s = ptr;
        for (int64_t i = 0; i < s->length; ++i) piton_value_deep_free(s->items[i]);
        piton_gc_unregister(s);
        free(s->items); free(s);
        --live_sets;
        break;
    }
    case SUB_TAG_BIGINT: {
        PitonBigInt *bi = ptr;
        free(bi->limbs); free(bi);
        break;
    }
    case SUB_TAG_OBJECT: {
        PitonObject *o = ptr;
        piton_gc_unregister(o);
        piton_object_run_finalizer(o);
        for (int64_t i = 0; i < o->length; ++i)
            piton_value_deep_free(o->attributes[i].value);
        free(o);
        --live_objects;
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
    piton_gc_register(c);
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

void piton_collection_put_tagged(void *raw, int64_t index, int64_t value) {
    PitonCollection *c = raw;
    if (!c) return;
    if (index < 0 || index >= c->capacity) return;
    PitonHeader *h = (PitonHeader *)value;
    if (h) h->refcount++;
    c->items[index] = pv_encode(PITON_TAG_OBJECT, value);
    if (index >= c->length) c->length = index + 1;
}

void piton_list_append(void *raw, int64_t value, int64_t type_tag) {
    PitonCollection *c = raw;
    if (!c) return;
    if (c->header.sub_tag != SUB_TAG_LIST) return;
    if (c->length >= c->capacity) {
        int64_t new_cap = c->capacity ? c->capacity * 2 : 4;
        c->items = realloc(c->items, (size_t)new_cap * sizeof(int64_t));
        c->capacity = new_cap;
    }
    /* type_tag: 0=raw_int, 1=object_ptr */
    if (type_tag == 0)
        c->items[c->length++] = pv_int(value);
    else {
        PitonHeader *h = (PitonHeader *)value;
        if (h) h->refcount++;
        c->items[c->length++] = pv_encode(PITON_TAG_OBJECT, value);
    }
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

typedef struct {
    PitonCollection *source;
    int64_t index;
} PitonGenExpr;

void *piton_genexpr_new(void *raw) {
    PitonCollection *source = raw;
    if (!source) return NULL;
    PitonGenExpr *gen = calloc(1, sizeof(*gen));
    gen->source = source;
    source->header.refcount++;
    return gen;
}

void *piton_genexpr_iter(void *raw) { return raw; }

int64_t piton_genexpr_next(void *raw) {
    PitonGenExpr *gen = raw;
    if (!gen || !gen->source || gen->index >= gen->source->length) {
        piton_raise("StopIteration", "");
        return 0;
    }
    int64_t value = gen->source->items[gen->index++];
    if (pv_tag(value) == PITON_TAG_INT) return pv_payload_signed(value);
    if (pv_tag(value) == PITON_TAG_BOOL) return pv_payload(value) ? 1 : 0;
    return value;
}

void piton_genexpr_free(void *raw) {
    PitonGenExpr *gen = raw;
    if (!gen) return;
    if (gen->source)
        piton_value_deep_free(pv_encode(PITON_TAG_OBJECT, (int64_t)gen->source));
    free(gen);
}

/* ── PitonGenerator: lazy generator with yield ─────────────────────────── */

#define PITON_GEN_MAGIC 0x5049544E47454ELL  /* "PITONGE" */
#define PITON_GEN_MAX_LOCALS 64

typedef int64_t (*piton_gen_func_t)(void *gen_ptr);

typedef struct {
    int64_t magic;
    int64_t state;                         /* current instruction pointer */
    int64_t finished;                      /* 1 = exhausted */
    int64_t started;                       /* 1 = has yielded at least once (offset 24) */
    int64_t sent_value;                    /* value sent via send() (offset 32) */
    piton_gen_func_t func;                 /* pointer to generator body */
    int64_t locals[PITON_GEN_MAX_LOCALS];  /* saved local variables (encoded PitonValues) */
    int64_t n_locals;                      /* number of locals used */
    int64_t return_value;                  /* M5: generator return value (int subset, 0 = None) */
} PitonGenerator;

void *piton_gen_new(void *func, int64_t n_locals) {
    PitonGenerator *gen = calloc(1, sizeof(*gen));
    gen->magic = PITON_GEN_MAGIC;
    gen->state = 0;
    gen->finished = 0;
    gen->func = (piton_gen_func_t)func;
    gen->n_locals = n_locals > 0 ? n_locals : 0;
    if (gen->n_locals > PITON_GEN_MAX_LOCALS - 1)
        gen->n_locals = PITON_GEN_MAX_LOCALS - 1;  /* slot 63 reserved (async await marker) */
    return gen;
}

int64_t piton_gen_next(void *raw) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a generator");
        return 0;
    }
    if (gen->finished) {
        piton_raise("StopIteration", "");
        return 0;
    }
    gen->sent_value = 0;  /* next() is equivalent to send(None) */
    if (gen->state == 0) {
        gen->started = 1;  /* mark as started on first next() */
    }
    int64_t result = gen->func(raw);
    if (gen->finished) {
        piton_raise("StopIteration", "");
        return 0;
    }
    return result;
}

int64_t piton_gen_send(void *raw, int64_t value) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a generator");
        return 0;
    }
    if (gen->finished) {
        piton_raise("StopIteration", "");
        return 0;
    }
    if (!gen->started && value != 0) {
        piton_raise("TypeError", "can't send non-None value to a just-started generator");
        return 0;
    }
    gen->sent_value = value;
    int64_t result = gen->func(raw);
    if (gen->finished) {
        piton_raise("StopIteration", "");
        return 0;
    }
    return result;
}

/* M5: generator return value (int subset; 0 = None for void returns) */
int64_t piton_gen_return_set(void *raw, int64_t value) {
    /* ONLY called when the backend compiles a `devolver v` inside a generator. */
    PitonGenerator *g = raw;
    if (g && g->magic == PITON_GEN_MAGIC) g->return_value = value;
    return 0;
}
int64_t piton_gen_return_value(void *raw) {
    PitonGenerator *g = raw;
    if (!g || g->magic != PITON_GEN_MAGIC)
        piton_raise_unhandled("TypeError", "object is not a generator");
    return g->return_value;
}

void piton_gen_free(void *raw) {


    PitonGenerator *gen = raw;
    if (!gen) return;
    gen->magic = 0;
    free(gen);
}

/* GENERATOR_THROW_V1: generators cannot catch yet (yield-in-try is rejecte
 * at lowering), so throw() marks the generator finished and the exception is
 * raised at the caller's handler — matching CPython when the generator does
 * not catch. */
int64_t piton_gen_throw(void *raw, const char *exc_type) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a generator");
        return 0;
    }
    if (gen->finished) {
        piton_raise(exc_type, "");
        return 0;
    }
    gen->finished = 1;
    piton_raise(exc_type, "");
    return 0;
}

/* GENERATOR_CLOSE_V1: close() marks the generator finished; since generators
 * cannot yield inside try/finally yet, there is no cleanup to run and the
 * CPython-visible behavior is "exhausted". */
int64_t piton_gen_close(void *raw) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a generator");
        return 0;
    }
    gen->finished = 1;
    return 0;
}

/* AWAIT_PROTOCOL_V1: depth-first coroutine scheduler. A coroutine body is the
 * same suspendible state machine as a generator: it "yields" the coroutine it
 * awaits, and piton_coro_run drives that inner coroutine to completion then
 * feeds its result back through sent_value and resumes the outer one. When a
 * coroutine finishes (return v), the run loop returns v. */
int64_t piton_coro_run(void *raw) {
    PitonGenerator *coro = raw;
    /* Pointer-floor guard: values below 1 MiB cannot be heap pointers, so
     * awaiting a plain integer (esperar 42) fails closed with TypeError
     * instead of dereferencing garbage. The async-gen path reaches this via
     * piton_agen_next for awaited coroutines. */
    if ((uintptr_t)raw < (uintptr_t)0x100000 || !coro || coro->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a coroutine");
        return 0;
    }
    while (!coro->finished) {
        coro->started = 1;
        int64_t yielded = coro->func(raw);
        if (coro->finished) return yielded;   /* return value surfaced */
        int64_t inner = piton_coro_run((void *)yielded);
        coro->sent_value = inner;             /* feed back to the await resume point */
    }
    return 0;
}

/* ASYNC_FOR_V1: drive one async generator until it yields data, completes an
 * awaited coroutine, or finishes. The generator body writes its await marker
 * into reserved slot 63 at every suspension point: 1 = "the returned value is a
 * coroutine to run" (esperar), 0 = "plain data to hand to the async-for loop"
 * (producir). This avoids dereferencing arbitrary data values as objects.
 * finished == 1 means StopAsyncIteration (async for ends). */
#define PITON_GEN_AWAIT_FLAG_SLOT 63
int64_t piton_agen_next(void *raw) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not an async generator");
        return 0;
    }
    while (!gen->finished) {
        gen->started = 1;
        int64_t yielded = gen->func(raw);
        if (gen->finished) return 0;                       /* StopAsyncIteration */
        if (gen->locals[PITON_GEN_AWAIT_FLAG_SLOT]) {
            gen->locals[PITON_GEN_AWAIT_FLAG_SLOT] = 0;    /* consume the marker */
            gen->sent_value = piton_coro_run((void *)yielded);
            continue;
        }
        return yielded;                                    /* data yield */
    }
    return 0;
}

/* ── TASK_SCHEDULER_V1: cooperative single-threaded event loop ────────────
 *
 * Awaitable values are tagged by their first int64 (magic). A coroutine that
 * awaits something yields that value via gen_yield; the loop dispatches on the
 * magic:
 *   PITON_GEN_MAGIC    direct coroutine object  -> run depth-first inline
 *   PITON_TASK_MAGIC   a Task                   -> suspend until the task is done,
 *                                                  then feed its result back
 *   PITON_GATHER_MAGIC a gather record          -> suspend until all sub-tasks
 *                                                  are done (results accumulated
 *                                                  in order), then feed a list
 *   PITON_SLEEP0_MAGIC asyncio.sleep(0)         -> cooperative yield: re-queue
 *                                                  this task behind others
 * A task is the scheduling unit: a coroutine wrapped by piton_task_new, owned
 * by the loop. Round-robin FIFO ready queue; new tasks are appended by
 * create_task/gather and by waiters that became resumable. Byte-identical vs
 * CPython for the tested subset (create_task + await task + gather + sleep(0)).
 * Cancellation: task.cancel() requests the task be dropped; waiters of a
 * cancelled task are cancelled too; a cancelled root surfaces as an unhandled
 * CancelledError, mirroring asyncio.run semantics when nobody catches. */
#define PITON_TASK_MAGIC    0x5049544E54414B4BLL           /* PITN TASK */
#define PITON_GATHER_MAGIC  0x5049544E47415448LL           /* PITN GATH */
#define PITON_SLEEP0_MAGIC  0x5049544E53503030LL           /* PITN SL00 */

typedef struct PitonWaiter {
    struct PitonWaiter *next;
    void *task;                        /* PitonTask* whose coroutine is waiting */
} PitonWaiter;

typedef struct PitonGather PitonGather;

typedef struct PitonTask {
    int64_t magic;                     /* PITON_TASK_MAGIC */
    int64_t state;                     /* 0 new, 1 ready, 2 running, 3 done, 4 cancelled */
    int64_t result;
    int64_t cancel_requested;
    PitonWaiter *waiters;
    PitonGather *gather_owner;         /* gather record this task feeds, if any */
    int64_t gather_slot;
    /* Inline await chain: chain[0] is the task's own coroutine; deeper
     * entries are coroutines awaited depth-first (await coro()). When the
     * chain suspends (sleep0/task/gather) the WHOLE chain stays recorded so
     * resumption drives the innermost generator first and unwinds through
     * sent_value — an inline-awaited coroutine that touches the loop would
     * otherwise lose its parents. */
    PitonGenerator *chain[64];
    int64_t chain_depth;
} PitonTask;

struct PitonGather {
    int64_t magic;                     /* PITON_GATHER_MAGIC */
    int64_t n;
    int64_t remaining;
    int64_t aborted;                   /* a member was cancelled: propagate */
    int64_t *results;
    PitonTask **tasks;
    PitonWaiter *waiters;
};

static PitonTask **piton_ready_queue = NULL;
static int64_t piton_ready_cap = 0;
static int64_t piton_ready_head = 0;
static int64_t piton_ready_tail = 0;

static void piton_ready_push(PitonTask *task) {
    if (piton_ready_tail >= piton_ready_cap) {
        int64_t new_cap = piton_ready_cap ? piton_ready_cap * 2 : 64;
        piton_ready_queue = realloc(piton_ready_queue, (size_t)new_cap * sizeof(PitonTask *));
        piton_ready_cap = new_cap;
    }
    piton_ready_queue[piton_ready_tail++] = task;
}

static PitonTask *piton_ready_pop(void) {
    if (piton_ready_head >= piton_ready_tail) return NULL;
    return piton_ready_queue[piton_ready_head++];
}

void *piton_task_new(void *coro) {
    PitonTask *task = calloc(1, sizeof(PitonTask));
    task->magic = PITON_TASK_MAGIC;
    task->state = 0;
    task->result = 0;
    task->chain[0] = (PitonGenerator *)coro;
    task->chain_depth = 1;
    return task;
}

int64_t piton_task_cancel(void *raw) {
    PitonTask *task = (PitonTask *)raw;
    if (!task || (uintptr_t)raw < 0x100000 || (uintptr_t)raw >= 0x800000000000
        || task->magic != PITON_TASK_MAGIC) {
        piton_raise_unhandled("TypeError", "object has no attribute 'cancel'");
        return 0;
    }
    task->cancel_requested = 1;
    return 0;
}

/* TASK_SCHEDULER_V2: integer-second timers.  The current native loop remains
 * single-threaded; sleeping occurs before the task is re-queued, preserving
 * deterministic FIFO ordering while making elapsed time observable. */
int64_t piton_sleep0(int64_t delay) {
    if (delay < 0) {
        piton_raise_unhandled("ValueError", "sleep length must be non-negative");
        return 0;
    }
    if (delay > 0) {
#ifdef _WIN32
        Sleep((DWORD)delay * 1000U);
#else
        sleep((unsigned int)delay);
#endif
    }
    return PITON_SLEEP0_MAGIC;
}

void *piton_gather_new(int64_t n) {
    if (n < 0) n = 0;
    PitonGather *gather = calloc(1, sizeof(PitonGather));
    gather->magic = PITON_GATHER_MAGIC;
    gather->n = n;
    gather->remaining = n;
    gather->results = calloc((size_t)n, sizeof(int64_t));
    gather->tasks = calloc((size_t)n, sizeof(PitonTask *));
    return gather;
}

int64_t piton_gather_add(void *raw, int64_t index, void *task_raw) {
    PitonGather *gather = (PitonGather *)raw;
    PitonTask *task = (PitonTask *)task_raw;
    if (!gather || (uintptr_t)raw < 0x100000 || (uintptr_t)raw >= 0x800000000000
        || gather->magic != PITON_GATHER_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a gather");
        return 0;
    }
    if (!task || (uintptr_t)task_raw < 0x100000 || (uintptr_t)task_raw >= 0x800000000000
        || task->magic != PITON_TASK_MAGIC) {
        piton_raise_unhandled("TypeError", "gather requires tasks");
        return 0;
    }
    if (index < 0 || index >= gather->n) {
        piton_raise_unhandled("TypeError", "gather index out of range");
        return 0;
    }
    gather->tasks[index] = task;
    task->gather_owner = gather;
    task->gather_slot = index;
    if (task->state == 4) {
        /* a member was already cancelled: the await must propagate
         * CancelledError instead of returning a list */
        gather->aborted = 1;
    } else if (task->state == 3) {
        /* CPython: gathering already-finished tasks returns their values
         * immediately; record the result now, the await path builds the list */
        gather->results[index] = task->result;
        --gather->remaining;
    }
    return 0;
}

static void *piton_build_result_list(int64_t *results, int64_t n) {
    PitonCollection *list = piton_collection_new(1, n);   /* kind 1 = LIST */
    for (int64_t i = 0; i < n; ++i)
        piton_collection_put(list, i, i, results[i]);
    return list;
}

static void piton_gather_complete(PitonGather *gather) {
    void *list = piton_build_result_list(gather->results, gather->n);
    PitonWaiter *waiter = gather->waiters;
    gather->waiters = NULL;
    while (waiter) {
        PitonWaiter *next = waiter->next;
        PitonTask *awaiting = (PitonTask *)waiter->task;
        awaiting->chain[awaiting->chain_depth - 1]->sent_value = (int64_t)list;
        piton_ready_push(awaiting);
        waiter = next;
    }
}

static void piton_notify_done(PitonTask *task) {
    if (task->gather_owner) {
        PitonGather *gather = task->gather_owner;
        if (!gather->aborted) {
            gather->results[task->gather_slot] = task->result;
            if (--gather->remaining == 0) piton_gather_complete(gather);
        }
    }
    PitonWaiter *waiter = task->waiters;
    task->waiters = NULL;
    while (waiter) {
        PitonWaiter *next = waiter->next;
        PitonTask *awaiting = (PitonTask *)waiter->task;
        awaiting->chain[awaiting->chain_depth - 1]->sent_value = task->result;
        piton_ready_push(awaiting);
        waiter = next;
    }
}

/* Drive one task until it completes, finishes its inline (depth-first) awaited
 * coroutines, or yields a value only the loop can handle. Returns 1 when the
 * task finished, 0 when control goes back to the loop. */
static int piton_step_task(PitonTask *task) {
    while (1) {
        if (task->chain_depth == 0) {         /* chain fully unwound already */
            task->state = 3;
            task->result = 0;
            return 1;
        }
        PitonGenerator *coro = task->chain[task->chain_depth - 1];
        coro->started = 1;
        int64_t yielded = coro->func(coro);
        if (coro->finished) {                 /* this generator returned */
            int64_t result = yielded;
            task->chain_depth--;
            if (task->chain_depth == 0) {     /* whole task done */
                task->state = 3;
                task->result = result;
                return 1;
            }
            /* feed the value to the parent in the chain and keep driving it */
            PitonGenerator *parent = task->chain[task->chain_depth - 1];
            parent->sent_value = result;
            continue;
        }
        if (yielded == PITON_SLEEP0_MAGIC) {  /* asyncio.sleep(0): cooperative */
            piton_ready_push(task);
            return 0;
        }
        /* Pointer-floor guard before any deref: only plausible heap pointers
         * are inspected for magic. Awaiting an int fails closed. */
        if (yielded < 0x100000 || yielded >= 0x800000000000) {
            piton_raise_unhandled("TypeError", "object is not awaitable");
            return 0;
        }
        if (*(int64_t *)yielded == PITON_GEN_MAGIC) {
            if (task->chain_depth >= 64) {
                piton_raise_unhandled("RuntimeError", "await chain too deep");
                return 0;
            }
            task->chain[task->chain_depth++] = (PitonGenerator *)yielded;
            continue;                         /* drive the awaited coroutine next */
        }
        if (*(int64_t *)yielded == PITON_TASK_MAGIC) {
            PitonTask *other = (PitonTask *)yielded;
            if (other->cancel_requested || other->state == 4) {
                /* awaiting a cancelled task cancels the awaiter (CancelledError
                 * propagates in CPython) — dropped on the next pop */
                task->cancel_requested = 1;
                piton_ready_push(task);
                return 0;
            }
            PitonWaiter *waiter = calloc(1, sizeof(PitonWaiter));
            waiter->task = task;
            waiter->next = other->waiters;
            other->waiters = waiter;
            if (other->state == 0) piton_ready_push(other);
            return 0;                         /* suspend the whole chain */
        }
        if (*(int64_t *)yielded == PITON_GATHER_MAGIC) {
            PitonGather *gather = (PitonGather *)yielded;
            if (gather->aborted) {
                /* CPython: a cancelled member raises CancelledError the moment
                 * the gather completes — here the awaiter is cancelled and the
                 * pop drops it (CancelledError surfaces at the root). */
                task->cancel_requested = 1;
                piton_ready_push(task);
                return 0;
            }
            if (gather->remaining == 0) {
                /* all members finished before we awaited: CPython returns the
                 * result list immediately */
                coro->sent_value = (int64_t)piton_build_result_list(gather->results, gather->n);
                continue;
            }
            PitonWaiter *waiter = calloc(1, sizeof(PitonWaiter));
            waiter->task = task;
            waiter->next = gather->waiters;
            gather->waiters = waiter;
            for (int64_t i = 0; i < gather->n; ++i)
                if (gather->tasks[i] && gather->tasks[i]->state == 0) piton_ready_push(gather->tasks[i]);
            return 0;                         /* suspend the whole chain */
        }
        piton_raise_unhandled("TypeError", "object is not awaitable");
        return 0;
    }
}

/* TASK_SCHEDULER_V1 entry: asyncio.run(coro()). Runs the root coroutine inside
 * a root task until completion; returns the root result (CPython: asyncio.run
 * returns the coroutine's value). */
int64_t piton_event_run(void *root_raw) {
    PitonGenerator *root_coro = (PitonGenerator *)root_raw;
    if (!root_coro || root_coro->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a coroutine");
        return 0;
    }
    piton_ready_head = piton_ready_tail = 0;
    PitonTask *root_task = piton_task_new(root_coro);
    piton_ready_push(root_task);
    while (1) {
        PitonTask *task = piton_ready_pop();
        if (!task) break;
        if (task->cancel_requested && task->state != 3) {
            task->state = 4;
            /* a cancelled gather member aborts its gather: the gather's waiters
             * get CancelledError (CPython propagates the member's exception) */
            if (task->gather_owner) {
                PitonGather *gather = task->gather_owner;
                if (!gather->aborted) {
                    gather->aborted = 1;
                    PitonWaiter *gw = gather->waiters;
                    gather->waiters = NULL;
                    while (gw) {
                        PitonWaiter *gnext = gw->next;
                        ((PitonTask *)gw->task)->cancel_requested = 1;
                        piton_ready_push((PitonTask *)gw->task);
                        gw = gnext;
                    }
                }
            }
            PitonWaiter *waiter = task->waiters;
            task->waiters = NULL;
            while (waiter) {
                PitonWaiter *next = waiter->next;
                ((PitonTask *)waiter->task)->cancel_requested = 1;
                piton_ready_push((PitonTask *)waiter->task);
                waiter = next;
            }
            continue;
        }
        if (piton_step_task(task)) piton_notify_done(task);
    }
    if (root_task->cancel_requested || root_task->state == 4) {
        piton_raise_unhandled("CancelledError", "");
        return 0;
    }
    return root_task->result;
}

void *piton_gen_collect(void *raw) {
    PitonGenerator *gen = raw;
    if (!gen || gen->magic != PITON_GEN_MAGIC) {
        piton_raise_unhandled("TypeError", "object is not a generator");
        return NULL;
    }
    /* Run the generator body and collect yields into a list */
    PitonCollection *list = piton_collection_new(0, 16);  /* 0 = LIST */
    while (!gen->finished) {
        int64_t val = gen->func(raw);
        if (gen->finished) break;
        piton_collection_put(list, list->length, piton_collection_len(list), val);
    }
    return list;
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
    piton_gc_register(d);
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

/* ── CALL_UNPACKING_DYNAMIC4_V1 ─────────────────────────────────────────── */

/* Expand a list/tuple into a physical argument buffer (max 4 slots).
   The emitter statically guarantees `raw` is a sequence; the kind field is
   still rechecked defensively. Values are decoded the same way as
   piton_collection_get: INT -> payload, BOOL -> 0/1, others as raw bits.
   Returns the number of items written; raises TypeError through the
   exception channel and returns -1 on violation. */
int64_t piton_unpack_seq4(void *raw, int64_t capacity, int64_t *out4) {
    PitonCollection *c = raw;
    if (!c) {
        piton_raise("TypeError", "argument after * must be a sequence");
        return -1;
    }
    if (c->kind != 1 && c->kind != 2) {
        piton_raise("TypeError", "argument after * must be a list or tuple");
        return -1;
    }
    if (c->length > capacity) {
        piton_raise("TypeError", "too many positional arguments for call");
        return -1;
    }
    for (int64_t i = 0; i < c->length; ++i) {
        int64_t v = c->items[i];
        if (pv_tag(v) == PITON_TAG_INT) { out4[i] = pv_payload_signed(v); continue; }
        if (pv_tag(v) == PITON_TAG_BOOL) { out4[i] = pv_payload(v) ? 1 : 0; continue; }
        out4[i] = v;
    }
    return c->length;
}

/* Expand a string-keyed dict into argument slots by parameter name.
   Contract: every entry key is an interned C string pointer, enforced
   statically by the emitter (CALL_UNPACKING_DYNAMIC4_V1 only admits dicts
   built from constant string keys). `mask` tracks already-bound slots
   (1<<index), catching duplicate keywords across successive `**` expansions
   in the same call. Returns 0 on success; raises TypeError through the
   exception channel and returns -1 on violation. */
int64_t piton_dict_unpack4(void *raw, const char **names, int64_t count,
                           int64_t *out4, int64_t *mask) {
    PitonDict *d = raw;
    if (!d) {
        piton_raise("TypeError", "argument after ** must be a dict");
        return -1;
    }
    for (int64_t i = 0; i < d->length; ++i) {
        const char *key = (const char *)pv_payload(d->entries[i].key);
        if (!key) {
            piton_raise("TypeError", "keywords must be strings");
            return -1;
        }
        int64_t matched = -1;
        for (int64_t j = 0; j < count; ++j)
            if (strcmp(key, names[j]) == 0) { matched = j; break; }
        if (matched < 0) {
            piton_raise("TypeError", "unexpected keyword argument in ** expansion");
            return -1;
        }
        if (*mask & (1LL << matched)) {
            piton_raise("TypeError", "multiple values for argument");
            return -1;
        }
        *mask |= (1LL << matched);
        int64_t v = d->entries[i].value;
        if (pv_tag(v) == PITON_TAG_INT) { out4[matched] = pv_payload_signed(v); continue; }
        if (pv_tag(v) == PITON_TAG_BOOL) { out4[matched] = pv_payload(v) ? 1 : 0; continue; }
        out4[matched] = v;
    }
    return 0;
}

/* ── Set API ───────────────────────────────────────────────────────────── */

void *piton_set_new(int64_t capacity) {
    if (capacity < 0) capacity = 0;
    PitonSet *s = calloc(1, sizeof(*s));
    s->header.sub_tag = SUB_TAG_SET;
    s->header.refcount = 1;
    s->capacity = capacity;
    if (capacity > 0) s->items = calloc((size_t)capacity, sizeof(int64_t));
    piton_gc_register(s);
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
    int64_t n_args;        /* fixed params (closures with *args keep the count WITHOUT the vararg slot) */
    int64_t n_cells;
    int64_t *cells;
    int64_t has_vararg;    /* VARIADIC_CLOSURE_V1: pack extras into a tuple at frame[n_cells+n_args] */
} PitonClosure;

#define PITON_BOUND_METHOD_MAGIC 0x5049544E424D4554LL
typedef struct { int64_t magic, addr, self, n_args; } PitonBoundMethod;
int64_t piton_bound_method_new(int64_t addr, int64_t n_args, int64_t self) {
    PitonBoundMethod *m = calloc(1, sizeof(*m));
    m->magic = PITON_BOUND_METHOD_MAGIC; m->addr = addr; m->self = self; m->n_args = n_args;
    return (int64_t)m;
}
int64_t piton_bound_method_self(int64_t raw) {
    PitonBoundMethod *m = (PitonBoundMethod *)raw;
    if (!m || m->magic != PITON_BOUND_METHOD_MAGIC) {
        fprintf(stderr, "AttributeError: bound method has no __self__\n"); exit(1);
    }
    return m->self;
}

int64_t piton_closure_new8(int64_t addr, int64_t n_args, int64_t n_cells,
                           int64_t c0, int64_t c1, int64_t c2, int64_t c3) {
    PitonClosure *c = calloc(1, sizeof(PitonClosure));
    c->magic = PITON_CLOSURE_MAGIC;
    c->addr = addr;
    c->n_args = n_args;
    c->n_cells = n_cells;
    int64_t cs[4] = {c0, c1, c2, c3};
    c->cells = calloc((size_t)n_cells, sizeof(*c->cells));
    for (int i = 0; i < n_cells && i < 4; i++) c->cells[i] = cs[i];
    return (int64_t)c;
}

int64_t piton_closure_new_frame(int64_t addr, int64_t n_args,
                                int64_t n_cells, const int64_t *cells, int64_t has_vararg) {
    PitonClosure *c = calloc(1, sizeof(PitonClosure));
    c->magic = PITON_CLOSURE_MAGIC;
    c->addr = addr;
    c->n_args = n_args;
    c->n_cells = n_cells;
    c->has_vararg = has_vararg;
    c->cells = calloc((size_t)(n_cells ? n_cells : 1), sizeof(*c->cells));
    if (n_cells > 0) memcpy(c->cells, cells, (size_t)n_cells * sizeof(*c->cells));
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

int64_t piton_closure_call_frame(int64_t callee, int64_t argc,
                                 const int64_t *args) {
    if (callee && ((int64_t *)callee)[0] == PITON_BOUND_METHOD_MAGIC) {
        PitonBoundMethod *m = (PitonBoundMethod *)callee;
        if (argc != m->n_args || argc > 3) {
            fprintf(stderr, "TypeError: bound method called with wrong number of arguments\n"); exit(2);
        }
        int64_t a[4] = {m->self, 0, 0, 0};
        for (int64_t i = 0; i < argc; ++i) a[i + 1] = args[i];
        return ((int64_t(*)(int64_t,int64_t,int64_t,int64_t))m->addr)(a[0],a[1],a[2],a[3]);
    }
    if (!callee || ((int64_t *)callee)[0] != PITON_CLOSURE_MAGIC) {
        if (argc > 4) {
            fprintf(stderr, "TypeError: native call exceeds four direct arguments\n");
            exit(2);
        }
        int64_t a[4] = {0, 0, 0, 0};
        for (int64_t i = 0; i < argc; ++i) a[i] = args[i];
        return ((int64_t(*)(int64_t, int64_t, int64_t, int64_t))callee)(a[0], a[1], a[2], a[3]);
    }
    PitonClosure *c = (PitonClosure *)callee;
    if (argc != c->n_args && !(c->has_vararg && argc > c->n_args)) {
        fprintf(stderr, "TypeError: closure called with wrong number of arguments\n");
        exit(2);
    }
    /* VARIADIC_CLOSURE_V1: extras pack into a tuple at frame[n_cells+n_args].
     * Elements are raw int bits (runtime cannot recover tags) -> int subset. */
    int64_t extra = (c->has_vararg && argc > c->n_args) ? argc - c->n_args : 0;
    int64_t total = c->n_cells + c->n_args + (c->has_vararg ? 1 : 0);
    int64_t *frame = calloc((size_t)total, sizeof(*frame));
    if (c->n_cells > 0) memcpy(frame, c->cells, (size_t)c->n_cells * sizeof(*frame));
    int64_t fixed = argc < c->n_args ? argc : c->n_args;
    if (fixed > 0) memcpy(frame + c->n_cells, args, (size_t)fixed * sizeof(*frame));
    if (c->has_vararg) {
        void *tuple = piton_collection_new(2 /*tuple*/, extra);
        /* VARIADIC_CLOSURE_V1 runtime-internal buffer: its lifetime is
         * managed by the call dispatcher (arena until process exit, M13 owns
         * real GC); it is excluded from the user-space live-count tripwire. */
        --live_collections;
        piton_gc_unregister(tuple);
        for (int64_t i = 0; i < extra; ++i) piton_collection_put(tuple, i, 0, args[c->n_args + i]);
        frame[c->n_cells + c->n_args] = (int64_t)tuple;
    }
    int64_t result = ((int64_t(*)(int64_t *))c->addr)(frame);
    free(frame);
    return result;
}

int64_t piton_callback_invoke(int64_t callback, int64_t value) {
    return piton_closure_call_frame(callback, 1, &value);
}

int64_t piton_frame_call(int64_t addr, int64_t argc, const int64_t *args) {
    int64_t *frame = calloc((size_t)argc, sizeof(*frame));
    if (argc > 0) memcpy(frame, args, (size_t)argc * sizeof(*frame));
    int64_t result = ((int64_t(*)(int64_t *))addr)(frame);
    free(frame);
    return result;
}

/* ── Object API ────────────────────────────────────────────────────────── */

void *piton_object_new(const char *class_name) {
    PitonObject *o = calloc(1, sizeof(*o));
    o->header.sub_tag = SUB_TAG_OBJECT;
    o->header.refcount = 1;
    o->class_name = class_name;
    o->parent_class_name = NULL;
    piton_gc_register(o);
    ++live_objects;
    return o;
}

void *piton_object_new_with_parent(const char *class_name, const char *parent_class_name) {
    PitonObject *o = calloc(1, sizeof(*o));
    o->header.sub_tag = SUB_TAG_OBJECT;
    o->header.refcount = 1;
    o->class_name = class_name;
    o->parent_class_name = parent_class_name;
    piton_gc_register(o);
    ++live_objects;
    return o;
}

void *piton_object_new_with_finalizer(const char *class_name, const char *parent_class_name, int64_t finalizer) {
    PitonObject *o = piton_object_new_with_parent(class_name, parent_class_name);
    o->finalizer = finalizer;
    o->finalizer_called = 0;
    return o;
}

static void piton_object_run_finalizer(PitonObject *o) {
    if (!o || !o->finalizer || o->finalizer_called) return;
    o->finalizer_called = 1;
    (void)((int64_t (*)(int64_t))o->finalizer)((int64_t)o);
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

/* Store a native object pointer as an owned tagged value.  The old setter
 * remains the integer-only ABI used by existing generated code. */
void piton_object_set_tagged(void *raw, const char *name, int64_t raw_ptr) {
    PitonObject *o = raw;
    if (!o || !name || !raw_ptr) return;
    int64_t ev = pv_encode(PITON_TAG_OBJECT, raw_ptr);
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) {
            piton_value_deep_free(o->attributes[i].value);
            o->attributes[i].value = ev;
            piton_value_incref(ev);
            return;
        }
    if (o->length >= 16) return;
    o->attributes[o->length].name = name;
    o->attributes[o->length].value = ev;
    ++o->length;
    piton_value_incref(ev);
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

/* ATTRIBUTE_LOOKUP_V2: read-through access that first consults the object's
 * field map (normal lookup) and, on a miss, delegates to the class-defined
 * __getattr__(self, name). The hook is a plain native function, so its own
 * piton_object_get inside is never rerouted (no recursion). */
int64_t piton_object_lookup(void *raw, const char *name, int64_t fallback) {
    PitonObject *o = raw;
    if (!o || !name) {
        piton_raise_unhandled("AttributeError", "attribute lookup on invalid object");
        return 0;
    }
    for (int64_t i = 0; i < o->length; ++i)
        if (strcmp(o->attributes[i].name, name) == 0) {
            int64_t v = o->attributes[i].value;
            if (pv_tag(v) == PITON_TAG_INT) return pv_payload_signed(v);
            if (pv_tag(v) == PITON_TAG_BOOL) return pv_payload(v) ? 1 : 0;
            return v;
        }
    return ((int64_t (*)(int64_t, const char *))fallback)((int64_t)o, name);
}

void piton_object_free(void *raw) {
    if (!raw) return;
    piton_value_deep_free(pv_encode(PITON_TAG_OBJECT, (int64_t)raw));
}

/* ── M13 cycle collector (Windows runtime) ───────────────────────────── */

static void piton_gc_detach_node(void *raw) {
    if (!raw) return;
    PitonHeader *h = raw;
    int64_t none = pv_none();
    switch (h->sub_tag) {
    case SUB_TAG_LIST: case SUB_TAG_TUPLE: {
        PitonCollection *c = raw;
        for (int64_t i = 0; i < c->length; ++i) {
            int64_t child = c->items[i];
            c->items[i] = none;
            piton_value_deep_free(child);
        }
        break;
    }
    case SUB_TAG_DICT: {
        PitonDict *d = raw;
        for (int64_t i = 0; i < d->length; ++i) {
            int64_t key = d->entries[i].key;
            int64_t value = d->entries[i].value;
            d->entries[i].key = none;
            d->entries[i].value = none;
            piton_value_deep_free(key);
            piton_value_deep_free(value);
        }
        break;
    }
    case SUB_TAG_SET: {
        PitonSet *s = raw;
        for (int64_t i = 0; i < s->length; ++i) {
            int64_t child = s->items[i];
            s->items[i] = none;
            piton_value_deep_free(child);
        }
        break;
    }
    case SUB_TAG_OBJECT: {
        PitonObject *o = raw;
        piton_object_run_finalizer(o);
        for (int64_t i = 0; i < o->length; ++i) {
            int64_t child = o->attributes[i].value;
            o->attributes[i].value = none;
            piton_value_deep_free(child);
        }
        break;
    }
    default: break;
    }
}

static void piton_gc_free_node(void *raw) {
    if (!raw) return;
    PitonHeader *h = raw;
    piton_gc_unregister(raw);
    switch (h->sub_tag) {
    case SUB_TAG_LIST: case SUB_TAG_TUPLE: {
        PitonCollection *c = raw;
        free(c->items); free(c); --live_collections;
        break;
    }
    case SUB_TAG_DICT: {
        PitonDict *d = raw;
        free(d->entries); free(d); --live_dicts;
        break;
    }
    case SUB_TAG_SET: {
        PitonSet *s = raw;
        free(s->items); free(s); --live_sets;
        break;
    }
    case SUB_TAG_OBJECT: {
        PitonObject *o = raw;
        free(o); --live_objects;
        break;
    }
    default: break;
    }
}

/* After module-owned roots are released, every registered node that remains is
 * unreachable except through a cycle.  Protect the whole snapshot, cut its
 * cyclic edges, then reclaim all shells.  This avoids recursive frees on a
 * cyclic graph while preserving normal refcounting before collection. */
void piton_gc_collect(void) {
    size_t n = gc_count;
    if (!n) return;
    void **snapshot = malloc(n * sizeof(*snapshot));
    if (!snapshot) {
        fprintf(stderr, "MemoryError: GC snapshot allocation failed\n");
        exit(1);
    }
    memcpy(snapshot, gc_nodes, n * sizeof(*snapshot));
    for (size_t i = 0; i < n; ++i) {
        PitonHeader *h = snapshot[i];
        ++h->refcount;
    }
    for (size_t i = 0; i < n; ++i) piton_gc_detach_node(snapshot[i]);
    for (size_t i = 0; i < n; ++i) piton_gc_free_node(snapshot[i]);
    free(snapshot);
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
    piton_raise_unhandled(type, message);
}

/* Report an exception with no statically-matching handler and exit. */
static const char *piton_exception_cause_type = NULL;
static const char *piton_exception_cause_msg = NULL;

void piton_raise_unhandled(const char *type, const char *message) {
    /* EXCEPTION_CHAINING_V1: the cause prints first, like CPython's
     * "__cause__" chain in the traceback (text-only model — no frames). */
    if (piton_exception_cause_type) {
        fprintf(stderr, "%s", piton_exception_cause_type);
        if (piton_exception_cause_msg && *piton_exception_cause_msg)
            fprintf(stderr, ": %s", piton_exception_cause_msg);
        fprintf(stderr, " -> causada por\n");
    }
    fprintf(stderr, "%s", type ? type : "Exception");
    if (message && *message) fprintf(stderr, ": %s", message);
    fputc('\n', stderr);
    fflush(stderr);
    exit(1);
}

/* EXCEPTION_CHAINING_V1: raise with an explicit cause. The cause must outlive
 * the handler probe, so it is recorded BEFORE the raise runs. */
void piton_raise_chain(const char *type, const char *message,
                       const char *cause_type, const char *cause_msg) {
    piton_exception_cause_type = cause_type;
    piton_exception_cause_msg = cause_msg;
    piton_raise(type, message);
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

/* Get the caught exception message, never NULL (for exception binding). */
const char *piton_catch_message_safe(void) {
    return piton_exception_message ? piton_exception_message : "";
}

/* Clear the catch state after handling. */
void piton_catch_clear(void) {
    piton_exception_active = 0;
    piton_exception_type = NULL;
    piton_exception_message = NULL;
    piton_exception_cause_type = NULL;
    piton_exception_cause_msg = NULL;
}

/* Snapshot the active exception before the handler clears catch state. */
void piton_reraise_save(void) {
    piton_reraise_type = piton_exception_type;
    piton_reraise_message = piton_exception_message;
    piton_reraise_cause_type = piton_exc_cause_type;
    piton_reraise_cause_message = piton_exc_cause_message;
}

/* Raise an exception with an explicit cause (raise ... from ...). */
void piton_raise_from(const char *type, const char *message,
                      const char *cause_type, const char *cause_message) {
    /* Search handler stack in reverse (most recent first) */
    for (int i = handler_sp - 1; i >= 0; --i) {
        PitonHandler *h = &handler_stack[i];
        if (h->accepted == NULL ||
            strcmp(h->accepted, type) == 0 ||
            strcmp(h->accepted, "Exception") == 0) {
            piton_exception_active = 1;
            piton_exception_type = type;
            piton_exception_message = message;
            piton_exc_cause_type = cause_type;
            piton_exc_cause_message = cause_message;
            piton_exc_context_type = NULL;
            piton_exc_context_message = NULL;
            return;
        }
    }
    /* No handler found — print chain and exit */
    piton_raise_unhandled(type, message);
}

/* Raise an exception with cause from a saved reraise (bare raise from in handler). */
void piton_raise_from_var(const char *type, const char *message) {
    /* Search handler stack in reverse (most recent first) */
    for (int i = handler_sp - 1; i >= 0; --i) {
        PitonHandler *h = &handler_stack[i];
        if (h->accepted == NULL ||
            strcmp(h->accepted, type) == 0 ||
            strcmp(h->accepted, "Exception") == 0) {
            piton_exception_active = 1;
            piton_exception_type = type;
            piton_exception_message = message;
            piton_exc_cause_type = piton_reraise_type;
            piton_exc_cause_message = piton_reraise_message;
            piton_exc_context_type = NULL;
            piton_exc_context_message = NULL;
            return;
        }
    }
    /* No handler found — print chain and exit */
    piton_raise_unhandled(type, message);
}

/* Raise an exception with no cause (raise ... from None). */
void piton_raise_from_none(const char *type, const char *message) {
    /* Search handler stack in reverse (most recent first) */
    for (int i = handler_sp - 1; i >= 0; --i) {
        PitonHandler *h = &handler_stack[i];
        if (h->accepted == NULL ||
            strcmp(h->accepted, type) == 0 ||
            strcmp(h->accepted, "Exception") == 0) {
            piton_exception_active = 1;
            piton_exception_type = type;
            piton_exception_message = message;
            piton_exc_cause_type = NULL;
            piton_exc_cause_message = NULL;
            piton_exc_context_type = NULL;
            piton_exc_context_message = NULL;
            return;
        }
    }
    /* No handler found — print and exit */
    piton_raise_unhandled(type, message);
}

/* Set __context__ from the saved reraise (for bare raise in handler without from). */
void piton_set_context_from_reraise(void) {
    piton_exc_context_type = piton_reraise_type;
    piton_exc_context_message = piton_reraise_message;
}

/* Re-raise the handler's caught exception; falls through to print+exit when unhandled. */
void piton_reraise(void) {
    const char *type = piton_reraise_type;
    const char *message = piton_reraise_message;
    for (int i = handler_sp - 1; i >= 0; --i) {
        PitonHandler *h = &handler_stack[i];
        if (h->accepted == NULL ||
            strcmp(h->accepted, type) == 0 ||
            strcmp(h->accepted, "Exception") == 0) {
            piton_exception_active = 1;
            piton_exception_type = type;
            piton_exception_message = message;
            piton_exc_cause_type = piton_reraise_cause_type;
            piton_exc_cause_message = piton_reraise_cause_message;
            return;
        }
    }
    piton_raise_unhandled(type, message);
}

/* Re-raise when no statically-matching handler exists (no stack search). */
void piton_reraise_unhandled(void) {
    piton_raise_unhandled(piton_reraise_type, piton_reraise_message);
}

void piton_print_float(double value) {
    if (isfinite(value) && trunc(value) == value)
        printf("%.1f\n", value);
    else
        printf("%.15g\n", value);
}

/* MODULE_METADATA_V1: dynamic print for module __package__ (None or text).
   Native object attributes carry plain string pointers (NOT the tagged
   encoding), so a zero payload means None and anything else is a C string. */
void piton_print_value(int64_t value) {
    if (value == 0)
        printf("None\n");
    else
        printf("%s\n", (const char *)value);
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

/* ── M14 BUILTINS_CORE_V2 ─────────────────────────────────────────────────
   Matriz declarada (paridad Win/Linux):
     ord(str)        -> int   (UTF-8 completo; TypeError si len != 1 char)
     chr(int)        -> str   (UTF-8 encode; ValueError fuera de 0..0x10FFFF)
     bin(int)        -> str   ("0b...", negativos "-0b...")
     pow(int,int>=0) -> int
     pow(float,int)  -> float (exponente entero, negativos incluidos)
     any/all(list|tuple) -> bool (truthiness por tag; str vacio = False)
     round(int)      -> int   (identidad)
     round(float)    -> int   (half-to-even, como CPython)
   Fuera de la matriz = fail-closed con raise explícito.
   ─────────────────────────────────────────────────────────────────────── */

static inline int piton_value_truthy(int64_t v) {
    switch (pv_tag(v)) {
        case PITON_TAG_NONE: return 0;
        case PITON_TAG_BOOL: return pv_payload(v) ? 1 : 0;
        case PITON_TAG_INT:  return pv_payload_signed(v) != 0;
        case PITON_TAG_FLOAT: {
            double *fp = (double *)(uintptr_t)pv_payload(v);
            return fp && *fp != 0.0;
        }
        case PITON_TAG_OBJECT: {
            int64_t ptr = (int64_t)pv_payload(v);
            if (!ptr) return 0;
            PitonHeader *h = (PitonHeader *)(uintptr_t)ptr;
            if (h->sub_tag == SUB_TAG_STR) {
                const char *s = (const char *)(uintptr_t)(ptr + sizeof(PitonHeader));
                return s[0] != '\0';
            }
            if (h->sub_tag == SUB_TAG_LIST || h->sub_tag == SUB_TAG_TUPLE)
                return ((PitonCollection *)(uintptr_t)ptr)->length > 0;
            return 1;
        }
        default: return 1;
    }
}

int64_t piton_all_iterable(void *raw) {
    PitonCollection *c = raw;
    if (!c) return 1;
    for (int64_t i = 0; i < c->length; ++i)
        if (!piton_value_truthy(c->items[i])) return 0;
    return 1;
}

int64_t piton_any_iterable(void *raw) {
    PitonCollection *c = raw;
    if (!c) return 0;
    for (int64_t i = 0; i < c->length; ++i)
        if (piton_value_truthy(c->items[i])) return 1;
    return 0;
}

int64_t piton_pow_int(int64_t b, int64_t e) {
    if (e < 0) {
        piton_raise("TypeError", "pow() negative exponent unsupported (M14 v1)");
        return 0;
    }
    int64_t acc = 1;
    while (e > 0) {
        if (e & 1) acc *= b;
        b *= b;
        e >>= 1;
    }
    return acc;
}

double piton_pow_float(double b, int64_t e) {
    int neg = e < 0;
    if (neg) e = -e;
    double acc = 1.0;
    while (e > 0) {
        if (e & 1) acc *= b;
        b *= b;
        e >>= 1;
    }
    return neg ? 1.0 / acc : acc;
}

int64_t piton_ord(const char *s) {
    if (!s || !s[0]) {
        piton_raise("TypeError", "ord() expected a character, but string of length 0 found");
        return 0;
    }
    const unsigned char *u = (const unsigned char *)s;
    int64_t cp; int len;
    if (u[0] < 0x80) { cp = u[0]; len = 1; }
    else if ((u[0] & 0xE0) == 0xC0) { cp = u[0] & 0x1F; len = 2; }
    else if ((u[0] & 0xF0) == 0xE0) { cp = u[0] & 0x0F; len = 3; }
    else if ((u[0] & 0xF8) == 0xF0) { cp = u[0] & 0x07; len = 4; }
    else {
        piton_raise("TypeError", "ord() received invalid UTF-8");
        return 0;
    }
    for (int i = 1; i < len; ++i) cp = (cp << 6) | (u[i] & 0x3F);
    if (s[len] != '\0') {
        piton_raise("TypeError", "ord() expected a character, but string of length >1 found");
        return 0;
    }
    return cp;
}

int64_t piton_chr(int64_t cp) {
    if (cp < 0 || cp > 0x10FFFF) {
        piton_raise("ValueError", "chr() arg not in range(0x110000)");
        return 0;
    }
    char *p = (char *)malloc(5);
    if (cp < 0x80) {
        p[0] = (char)cp; p[1] = 0;
    } else if (cp < 0x800) {
        p[0] = (char)(0xC0 | (cp >> 6)); p[1] = (char)(0x80 | (cp & 0x3F)); p[2] = 0;
    } else if (cp < 0x10000) {
        p[0] = (char)(0xE0 | (cp >> 12)); p[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        p[2] = (char)(0x80 | (cp & 0x3F)); p[3] = 0;
    } else {
        p[0] = (char)(0xF0 | (cp >> 18)); p[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        p[2] = (char)(0x80 | ((cp >> 6) & 0x3F)); p[3] = (char)(0x80 | (cp & 0x3F)); p[4] = 0;
    }
    return (int64_t)p;
}

int64_t piton_bin(int64_t v) {
    char *p = (char *)malloc(70);
    size_t o = 0;
    uint64_t m;
    if (v < 0) { p[o++] = '-'; m = (uint64_t)(-(v + 1)) + 1; }
    else m = (uint64_t)v;
    p[o++] = '0'; p[o++] = 'b';
    char tmp[64]; int n = 0;
    do { tmp[n++] = (char)('0' + (m & 1)); m >>= 1; } while (m);
    while (n) p[o++] = tmp[--n];
    p[o] = 0;
    return (int64_t)p;
}

int64_t piton_round_float(double x) {
    double ax = x < 0 ? -x : x;
    if (ax >= 9.0e18) {
        piton_raise("OverflowError", "round() float too large to convert to int");
        return 0;
    }
    int64_t t = (int64_t)ax;
    double frac = ax - (double)t;
    int64_t r;
    if (frac > 0.5) r = t + 1;
    else if (frac < 0.5) r = t;
    else r = (t & 1) ? t + 1 : t;     /* half-to-even */
    return x < 0 ? -r : r;
}

/* ── M14 TYPE_CONVERSION_V1 + MATH_TIER1_V1 ──────────────────────────────
   Matriz declarada (paridad Win/Linux):
     entero/int:   int/bool → identidad; float → trunc hacia cero;
                   str base 10 con espacios alrededor;
                   str invalida → ValueError CAPTURABLE (no crash)
     decimal/float: int/bool → double; float → identidad;
                   str parse double; invalida → ValueError capturable
     texto/str:    int/float/bool/None/str → cadena con el MISMO formato
                   que imprimir() (coherencia interna declarada; float no es
                   repr completo CPython → divergencia heredada del printer)
     booleano/bool: truthiness por tipo (int!=0, float!=0.0, str!="",
                   coleccion no vacia, None→False)
     math: floor/ceil/trunc (float→int), fabs (float→float),
           gcd(int,int) (valor absoluto, euclides), pi/e constantes.
   ─────────────────────────────────────────────────────────────────────── */

int64_t piton_int_from_str(const char *s) {
    if (!s) { piton_raise("TypeError", "int() argument must be a string"); return 0; }
    while (*s == ' ' || *s == '\t' || *s == '\n') ++s;
    if (!*s) { piton_raise("ValueError", "invalid literal for int() with base 10"); return 0; }
    char *end = NULL;
    int64_t v = (int64_t)_strtoi64(s, &end, 10);
    if (end == s) { piton_raise("ValueError", "invalid literal for int() with base 10"); return 0; }
    while (*end == ' ' || *end == '\t' || *end == '\n') ++end;
    if (*end) { piton_raise("ValueError", "invalid literal for int() with base 10"); return 0; }
    return v;
}

double piton_float_from_str(const char *s) {
    if (!s) { piton_raise("TypeError", "float() argument must be a string"); return 0.0; }
    while (*s == ' ' || *s == '\t' || *s == '\n') ++s;
    if (!*s) { piton_raise("ValueError", "could not convert string to float"); return 0.0; }
    char *end = NULL;
    double v = strtod(s, &end);
    if (end == s) { piton_raise("ValueError", "could not convert string to float"); return 0.0; }
    while (*end == ' ' || *end == '\t' || *end == '\n') ++end;
    if (*end) { piton_raise("ValueError", "could not convert string to float"); return 0.0; }
    return v;
}

int64_t piton_str_from_int(int64_t v) {
    char *p = (char *)malloc(24);
    sprintf(p, "%lld", (long long)v);
    return (int64_t)p;
}

int64_t piton_str_from_bool(int64_t v) {
    return (int64_t)(v ? "True" : "False");
}

int64_t piton_str_from_none(void) {
    return (int64_t)"None";
}

int64_t piton_str_from_float(double x) {
    char *p = (char *)malloc(40);
    if (isfinite(x) && trunc(x) == x) sprintf(p, "%.1f", x);
    else sprintf(p, "%.15g", x);
    return (int64_t)p;
}

int64_t piton_str_truthy(const char *s) {
    return (s && s[0]) ? 1 : 0;
}

int64_t piton_math_floor(double x) { return (int64_t)floor(x); }
int64_t piton_math_ceil(double x)  { return (int64_t)ceil(x); }
int64_t piton_math_trunc(double x) { return (int64_t)trunc(x); }
double  piton_math_fabs(double x)  { return fabs(x); }

int64_t piton_math_gcd(int64_t a, int64_t b) {
    if (a < 0) a = -a;
    if (b < 0) b = -b;
    while (b) { int64_t t = a % b; a = b; b = t; }
    return a;
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

/* ── Math functions (STDLIB_TIER1_V1) ─────────────────────────────────── */

static inline double piton_bits_double(int64_t bits) {
    union { double d; int64_t u; } v;
    v.u = bits;
    return v.d;
}

static inline int64_t piton_double_bits(double d) {
    union { double d; int64_t u; } v;
    v.d = d;
    return v.u;
}

int64_t piton_float_sqrt(int64_t bits) {
    double x = piton_bits_double(bits);
    double r = sqrt(x);
    return piton_double_bits(r);
}

int64_t piton_float_floor(int64_t bits) {
    double x = piton_bits_double(bits);
    int64_t r = (int64_t)x;
    if (x < 0 && r != x) --r;
    return r;
}

int64_t piton_float_ceil(int64_t bits) {
    double x = piton_bits_double(bits);
    int64_t r = (int64_t)x;
    if (x > 0 && r != x) ++r;
    return r;
}

int64_t piton_float_sin(int64_t bits) {
    double x = piton_bits_double(bits);
    double r = sin(x);
    return piton_double_bits(r);
}

int64_t piton_float_cos(int64_t bits) {
    double x = piton_bits_double(bits);
    double r = cos(x);
    return piton_double_bits(r);
}

int64_t piton_float_log(int64_t bits) {
    double x = piton_bits_double(bits);
    double r = log(x);
    return piton_double_bits(r);
}

/* ── Sys functions (STDLIB_TIER1_V1) ──────────────────────────────────── */

#include <stdlib.h>

/* MinGW's CRT exposes the process argument vector through these globals. */
extern int __argc;
extern char **__argv;

void piton_exit(int64_t code) {
    exit((int)code);
}

int64_t piton_argv_new(void) {
    PitonCollection *c = piton_collection_new(1, __argc);
    for (int i = 0; i < __argc; ++i) {
        PitonStr *s = piton_str_new(__argv[i], (int64_t)strlen(__argv[i]));
        c->items[i] = pv_encode(PITON_TAG_OBJECT, (int64_t)s);
    }
    c->length = __argc;
    return (int64_t)c;
}

/* ── Live count totals ────────────────────────────────────────────────── */

int64_t piton_total_live_count(void) {
    return live_collections + live_objects + live_dicts + live_sets;
}
