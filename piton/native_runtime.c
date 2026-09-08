#include <stdint.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    PITON_LIST = 1,
    PITON_TUPLE = 2,
    PITON_DICT = 3,
    PITON_SET = 4
};

typedef struct {
    int64_t key;
    int64_t value;
} PitonEntry;

typedef struct {
    int64_t kind;
    int64_t length;
    int64_t capacity;
    PitonEntry entries[];
} PitonCollection;

static int64_t live_collections = 0;
static int64_t live_objects = 0;

typedef struct {
    const char *name;
    int64_t value;
} PitonAttribute;

typedef struct {
    const char *class_name;
    int64_t length;
    PitonAttribute attributes[16];
} PitonObject;

void *piton_object_new(const char *class_name) {
    PitonObject *object = calloc(1, sizeof(*object));
    if (!object) return NULL;
    object->class_name = class_name;
    ++live_objects;
    return object;
}

void piton_object_set(void *raw, const char *name, int64_t value) {
    PitonObject *object = raw;
    if (!object || !name) return;
    for (int64_t i = 0; i < object->length; ++i) {
        if (strcmp(object->attributes[i].name, name) == 0) {
            object->attributes[i].value = value;
            return;
        }
    }
    if (object->length >= 16) return;
    object->attributes[object->length].name = name;
    object->attributes[object->length].value = value;
    ++object->length;
}

int64_t piton_object_get(void *raw, const char *name) {
    PitonObject *object = raw;
    if (!object || !name) return 0;
    for (int64_t i = 0; i < object->length; ++i) {
        if (strcmp(object->attributes[i].name, name) == 0) return object->attributes[i].value;
    }
    return 0;
}

void piton_object_free(void *raw) {
    if (raw) --live_objects;
    free(raw);
}

void *piton_collection_new(int64_t kind, int64_t capacity) {
    if (capacity < 0) return NULL;
    PitonCollection *value = calloc(1, sizeof(*value) + (size_t)capacity * sizeof(PitonEntry));
    if (!value) return NULL;
    value->kind = kind;
    value->capacity = capacity;
    ++live_collections;
    return value;
}

void piton_collection_put(void *raw, int64_t index, int64_t key, int64_t value) {
    PitonCollection *collection = raw;
    if (!collection) return;
    if (collection->kind == PITON_DICT || collection->kind == PITON_SET) {
        for (int64_t i = 0; i < collection->length; ++i) {
            if (collection->entries[i].key == key) {
                if (collection->kind == PITON_DICT) collection->entries[i].value = value;
                return;
            }
        }
        index = collection->length;
    }
    if (index < 0 || index >= collection->capacity) return;
    collection->entries[index].key = key;
    collection->entries[index].value = value;
    if (index >= collection->length) collection->length = index + 1;
}

int64_t piton_collection_len(void *raw) {
    PitonCollection *collection = raw;
    return collection ? collection->length : 0;
}

int64_t piton_collection_get(void *raw, int64_t key) {
    PitonCollection *collection = raw;
    if (!collection) return 0;
    if (collection->kind == PITON_LIST || collection->kind == PITON_TUPLE) {
        if (key < 0) key += collection->length;
        if (key < 0 || key >= collection->length) return 0;
        return collection->entries[key].value;
    }
    if (collection->kind == PITON_DICT) {
        for (int64_t i = 0; i < collection->length; ++i) {
            if (collection->entries[i].key == key) return collection->entries[i].value;
        }
    }
    return 0;
}

void piton_collection_print(void *raw) {
    PitonCollection *collection = raw;
    if (!collection) return;
    const char open = collection->kind == PITON_LIST ? '[' : collection->kind == PITON_TUPLE ? '(' : '{';
    const char close = collection->kind == PITON_LIST ? ']' : collection->kind == PITON_TUPLE ? ')' : '}';
    putchar(open);
    for (int64_t i = 0; i < collection->length; ++i) {
        if (i) fputs(", ", stdout);
        if (collection->kind == PITON_DICT) {
            printf("%lld: %lld", (long long)collection->entries[i].key,
                   (long long)collection->entries[i].value);
        } else {
            printf("%lld", (long long)collection->entries[i].value);
        }
    }
    if (collection->kind == PITON_TUPLE && collection->length == 1) putchar(',');
    putchar(close);
    putchar('\n');
}

void piton_collection_free(void *raw) {
    if (raw) --live_collections;
    free(raw);
}

int64_t piton_collection_live_count(void) {
    return live_collections;
}

int64_t piton_object_live_count(void) {
    return live_objects;
}

void piton_raise(const char *type, const char *message) {
    fprintf(stderr, "%s", type ? type : "Exception");
    if (message && *message) fprintf(stderr, ": %s", message);
    fputc('\n', stderr);
    exit(1);
}

void piton_print_float(double value) {
    if (isfinite(value) && trunc(value) == value) {
        printf("%.1f\n", value);
    } else {
        printf("%.15g\n", value);
    }
}
