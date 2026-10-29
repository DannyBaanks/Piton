"""Backend ELF x86-64 Linux para el subconjunto MIR escalar."""
from __future__ import annotations

import json
import gzip
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .lower import LoweringError, lower_cst_to_hir
from .mir import (
    _BUILTIN_EXCEPTIONS,
    MIRFunction,
    MIRInstruction,
    MIRLoweringError,
    MIRModule,
    lower_hir_to_mir,
)
from .parser import parse
from .x86 import NativeBuildError, _BUILTINS, _scan_native_modules, generator_slot_layout


# FLOAT_REPR_V1: shortest round-trip float -> text, shared VERBATIM with
# piton/native_runtime.c (single source of truth: piton/float_repr.h).
# Read at import time and spliced BEFORE the runtime below, because the
# float printers live inside the runtime string.
_FLOAT_REPR_C = Path(__file__).with_name("float_repr.h").read_text(encoding="utf-8")

_RICH_FREESTANDING_C = r"""
enum{PK_NONE,PK_BOOL,PK_INT,PK_FLOAT,PK_STR,PK_LIST,PK_TUPLE,PK_DICT,PK_SET,PK_OBJECT,PK_BIGINT,PK_RANGE,PK_BYTES,PK_BYTEARRAY,PK_COMPLEX,PK_FROZENSET,PK_SLICE,PK_MEMORYVIEW};
typedef struct{long bits;int kind;}PitonSlot;
/* COMPLEX_V1: número complejo (re + im j). Vive en la arena (bump allocator,
 * sin refcount): los escalares que ocupan puntero se tratan como inmutables y
 * nunca entran a incref/decref — PK_COMPLEX queda FUERA del rango que
 * piton_slot_incref/decref recorren. La memoria se reclama al salir; documentado
 * como KNOWN_GAP del gate. */
typedef struct{long magic;double re;double im;}PitonComplex;
typedef struct{double re;double im;}PitonComplexVal;
typedef struct{long refcount;long kind;long start;long stop;long step;}PitonRange;
/* SLICE_VALUE_V1: objeto slice como valor. None (-1) significa bound ausente. */
typedef struct{long refcount;long kind;long start;long stop;long step;int has_start;int has_stop;int has_step;}PitonSlice;
typedef struct{long refcount;long kind;long length;unsigned char*data;}PitonBytes;
typedef struct{long refcount;long kind;long length;long capacity;unsigned char*data;}PitonByteArray;
typedef struct{long magic;void*bytes_obj;long index;}PitonBytesIterator;
/* MEMORYVIEW_V1: vista sobre bytes/bytearray. Referencia el buffer original
 * (no copia): la mutación a través de la vista se ve en el buffer. */
typedef struct{long refcount;long kind;long length;int is_bytearray;void*buf;long offset;long stride;}PitonMemoryView;
static long piton_range_len(PitonRange*r);
static long piton_range_eq(PitonRange*a,PitonRange*b);
static unsigned char piton_arena[8*1024*1024];
static usize piton_arena_used=0;
static void piton_memzero(void*p,usize n){unsigned char*b=p;for(usize i=0;i<n;++i)b[i]=0;}
static void piton_memcpy(void*d,const void*s,usize n){unsigned char*dd=d;const unsigned char*ss=s;for(usize i=0;i<n;++i)dd[i]=ss[i];}
static void piton_memmove(void*d,const void*s,usize n){unsigned char*dd=d;const unsigned char*ss=s;if(dd==ss||n==0)return;if(dd<ss){for(usize i=0;i<n;++i)dd[i]=ss[i];}else{for(usize i=n;i>0;--i)dd[i-1]=ss[i-1];}}
static int piton_memcmp(const void*a,const void*b,usize n){const unsigned char*p=(const unsigned char*)a,*q=(const unsigned char*)b;for(usize i=0;i<n;++i){if(p[i]!=q[i])return p[i]<q[i]?-1:1;}return 0;}
static void*piton_alloc(usize n){usize p=(piton_arena_used+15)&~15UL;if(n>sizeof(piton_arena)-p){piton_write(2,"MemoryError\n",12);piton_exit(1);}void*r=piton_arena+p;piton_arena_used=p+n;piton_memzero(r,n);return r;}
/* ── Freelist heap for refcounted objects (GC_CYCLES_V1) ────────────── */
static unsigned char piton_heap[4*1024*1024];
static long piton_heap_initialized=0;
typedef struct piton_heap_block{usize size;int free;struct piton_heap_block*next;}piton_heap_block;
static piton_heap_block*piton_heap_freelist=0;
static void piton_heap_init(void){
    if(piton_heap_initialized)return;piton_heap_initialized=1;
    piton_heap_freelist=(piton_heap_block*)piton_heap;
    piton_heap_freelist->size=sizeof(piton_heap)-sizeof(piton_heap_block);
    piton_heap_freelist->free=1;piton_heap_freelist->next=0;
}
static void*piton_heap_alloc(usize n){
    if(!piton_heap_initialized)piton_heap_init();
    usize req=n+sizeof(piton_heap_block);
    if(req<16)req=16;
    piton_heap_block**prev=&piton_heap_freelist;piton_heap_block*cur=piton_heap_freelist;
    while(cur){
        if(cur->free&&cur->size>=req){
            if(cur->size>=req+sizeof(piton_heap_block)+16){
                piton_heap_block*rest=(piton_heap_block*)((char*)cur+req);
                rest->size=cur->size-req;rest->free=1;rest->next=cur->next;*prev=rest;
            }else{*prev=cur->next;}
            cur->free=0;cur->next=0;
            piton_memzero((char*)cur+sizeof(piton_heap_block),n);
            return(char*)cur+sizeof(piton_heap_block);
        }
        prev=&cur->next;cur=cur->next;
    }
    piton_write(2,"MemoryError: GC heap exhausted\n",31);piton_exit(1);return 0;
}
static void piton_heap_free(void*ptr){
    if(!ptr)return;
    piton_heap_block*b=(piton_heap_block*)((char*)ptr-sizeof(piton_heap_block));
    b->free=1;b->next=piton_heap_freelist;piton_heap_freelist=b;
}
/* GC node registry */
static void**gc_nodes=0;static long gc_count=0;static long gc_capacity=0;
static long live_collections=0;static long live_dicts=0;static long live_sets=0;static long live_objects=0;
static void piton_gc_register(void*raw){
    if(!raw)return;
    for(long i=0;i<gc_count;++i)if(gc_nodes[i]==raw)return;
    if(gc_count>=gc_capacity){long nc=gc_capacity?gc_capacity*2:16;
        void**g=(void**)piton_heap_alloc((usize)nc*sizeof(void*));
        if(gc_count)piton_memcpy(g,gc_nodes,(usize)gc_count*sizeof(void*));
        gc_nodes=g;gc_capacity=nc;}
    gc_nodes[gc_count++]=raw;
}
static void piton_gc_unregister(void*raw){
    for(long i=0;i<gc_count;++i){if(gc_nodes[i]!=raw)continue;
        gc_nodes[i]=gc_nodes[--gc_count];return;}
}
/* ── Container struct definitions (needed by GC functions below) ─────── */
static void piton_raise_set(const char*,const char*);
typedef struct{long refcount;long kind;long length;long capacity;PitonSlot*items;}PitonSeq;
typedef struct{long magic;PitonSeq*seq;long index;}PitonIterator;
typedef struct{PitonSeq*source;long index;}PitonGenExpr;
typedef struct{PitonSlot key;PitonSlot value;}PitonDictEntry;
typedef struct{long refcount;long kind;long length;long capacity;PitonDictEntry*items;}PitonDict;
typedef struct{long refcount;long kind;long length;long capacity;PitonSlot*items;}PitonSet;
typedef struct{const char*name;PitonSlot value;}PitonAttr;
typedef struct PitonObject{long refcount;long kind;const char*class_name;const char*parent_name;long length;PitonAttr attrs[32];long finalizer;long finalizer_called;struct PitonObject*next_all;}PitonObject;

static int g_piton_ascii_mode=0;
/* OBJECT_REPR_HOOK_V1: tabla clase -> __repr__ que el emitter llena. */
#define PITON_REPR_HOOK_MAX 64
static const char* g_repr_hook_cls[PITON_REPR_HOOK_MAX];
static long g_repr_hook_fn[PITON_REPR_HOOK_MAX];
static long g_repr_hook_n=0;
static void piton_object_repr_hook_set(const char*cls,long fn){
  for(long i=0;i<g_repr_hook_n;++i)if(g_repr_hook_cls[i]&&piton_strcmp(g_repr_hook_cls[i],cls)==0){g_repr_hook_fn[i]=fn;return;}
  if(g_repr_hook_n>=PITON_REPR_HOOK_MAX)return;
  g_repr_hook_cls[g_repr_hook_n]=cls;g_repr_hook_fn[g_repr_hook_n]=fn;++g_repr_hook_n;
}
static long piton_object_repr_hook(long obj,const char*cls){
  if(!cls)return 0;
  for(long i=0;i<g_repr_hook_n;++i)if(g_repr_hook_cls[i]&&piton_strcmp(g_repr_hook_cls[i],cls)==0){
    if(!g_repr_hook_fn[i])return 0;
    return((long(*)(long))g_repr_hook_fn[i])(obj);
  }
  return 0;
}
/* REPR_V1: repr() byte-identico a CPython. str usa comillas; escalares y
 * colecciones reimitan el camino del printer de slot, pero a un buffer. */
typedef struct{long len;long cap;char*d;}PitonStrBuf;
static void sb_init(PitonStrBuf*b){b->cap=64;b->len=0;b->d=(char*)piton_alloc((usize)b->cap);b->d[0]=0;}
static void sb_putc(PitonStrBuf*b,char c){
  if(b->len+2>b->cap){long nc=b->cap*2;char*nd=(char*)piton_alloc((usize)nc);
    piton_memcpy(nd,b->d,(usize)b->len);b->d=nd;b->cap=nc;}
  b->d[b->len++]=c;b->d[b->len]=0;
}
static void sb_puts(PitonStrBuf*b,const char*s){if(!s)return;while(*s)sb_putc(b,*s++);}
static void sb_put_int(PitonStrBuf*b,long v){char t[24];long n=0;unsigned long u;
  if(v<0){sb_putc(b,'-');u=(unsigned long)(-(v+1))+1;}else u=(unsigned long)v;
  do{t[n++]=(char)('0'+u%10);u/=10;}while(u);
  while(n)sb_putc(b,t[--n]);}
static void piton_slot_repr_into(PitonStrBuf*b,PitonSlot v);
/* SLOT_REPR_FWD_V1: los metodos __repr__ de clases (emitidos antes del
   cuerpo del prelude que define piton_slot_repr) necesitan el prototipo
   (__future__.py: implicit declaration -> ld fallaba). */
static long piton_slot_repr(long bits,int kind);
static void piton_seq_repr_into(PitonStrBuf*b,PitonSeq*s){
  sb_putc(b,s->kind==PK_TUPLE?'(':'[');
  for(long i=0;i<s->length;++i){if(i)sb_puts(b,", ");piton_slot_repr_into(b,s->items[i]);}
  if(s->kind==PK_TUPLE&&s->length==1)sb_putc(b,',');
  sb_putc(b,s->kind==PK_TUPLE?')':']');
}
/* STR_FROM_SEQ_V1: el str() de una lista/tupla (y el `%s` de una coleccion en
   piton_str_of_value) se usa en el emisor desde STR_SEQ_V1, pero la funcion
   nunca se definio: era una declaracion implicita que solo rompia el enlace
   cuando piton_str_of_value quedaba viva (html/entities.py, xml/sax). */
static long piton_str_from_seq(PitonSeq*s){PitonStrBuf b;sb_init(&b);piton_seq_repr_into(&b,s);return(long)b.d;}
static void piton_dict_repr_into(PitonStrBuf*b,PitonDict*d){
  sb_putc(b,'{');
  for(long i=0;i<d->length;++i){if(i)sb_puts(b,", ");piton_slot_repr_into(b,d->items[i].key);sb_puts(b,": ");piton_slot_repr_into(b,d->items[i].value);}
  sb_putc(b,'}');
}
static void piton_set_repr_into(PitonStrBuf*b,PitonSet*s){
  sb_putc(b,'{');
  for(long i=0;i<s->length;++i){if(i)sb_puts(b,", ");piton_slot_repr_into(b,s->items[i]);}
  sb_putc(b,'}');
}
static void piton_str_repr_ascii_into(PitonStrBuf*b,const char*s);
static void piton_str_repr_into(PitonStrBuf*b,const char*s){
  if(g_piton_ascii_mode){piton_str_repr_ascii_into(b,s);return;}
  /* comillas simples salvo que el texto tenga ' y no " (regla CPython) */
  int has_sq=0,has_dq=0;
  for(usize i=0;s&&s[i];++i){if(s[i]=='\'')has_sq=1;else if(s[i]=='"')has_dq=1;}
  char qc=(has_sq&&!has_dq)?'"':'\'';
  sb_putc(b,qc);
  for(usize i=0;s&&s[i];++i){unsigned char c=(unsigned char)s[i];
    if(c=='\\')sb_puts(b,"\\\\");
    else if(c=='\n')sb_puts(b,"\\n");
    else if(c=='\t')sb_puts(b,"\\t");
    else if(c=='\r')sb_puts(b,"\\r");
    else if((char)c==qc){char e[2];e[0]='\\';e[1]=qc;sb_putc(b,e[0]);sb_putc(b,e[1]);}
    else sb_putc(b,(char)c);}
  sb_putc(b,qc);
}
/* Refcount helpers */
static long piton_slot_rc(PitonSlot v){
    if((v.kind>=PK_LIST&&(v.kind<=PK_BYTEARRAY))){long*rc=(long*)v.bits;return*rc;}
    return-1;
}
static void piton_slot_incref(PitonSlot v){if((v.kind>=PK_LIST&&(v.kind<=PK_BYTEARRAY))){long*rc=(long*)v.bits;++(*rc);}}
static void piton_slot_decref(PitonSlot v);
/* Deep free: decrements refcount; if zero, detach children and free struct */
static void piton_slot_decref(PitonSlot v){
    if(v.kind<PK_LIST||v.kind>PK_BYTEARRAY)return;
    long*rc=(long*)v.bits;if(!rc)return;
    if(--(*rc)>0)return;
    switch(v.kind){
    case PK_LIST:case PK_TUPLE:{PitonSeq*s=(PitonSeq*)v.bits;
        for(long i=0;i<s->length;++i)piton_slot_decref(s->items[i]);
        piton_gc_unregister(s);piton_heap_free(s);
        if(s->kind==PK_LIST)--live_collections;else--live_collections;break;}
    case PK_DICT:{PitonDict*d=(PitonDict*)v.bits;
        for(long i=0;i<d->length;++i){piton_slot_decref(d->items[i].key);piton_slot_decref(d->items[i].value);}
        piton_gc_unregister(d);piton_heap_free(d);--live_dicts;break;}
    case PK_SET:{PitonSet*s=(PitonSet*)v.bits;
        for(long i=0;i<s->length;++i)piton_slot_decref(s->items[i]);
        piton_gc_unregister(s);piton_heap_free(s);--live_sets;break;}
    case PK_RANGE:{PitonRange*r=(PitonRange*)v.bits;
        piton_gc_unregister(r);piton_heap_free(r);break;}
    case PK_BYTES:{PitonBytes*b=(PitonBytes*)v.bits;
        piton_gc_unregister(b);piton_heap_free(b);break;}
    case PK_BYTEARRAY:{PitonByteArray*ba=(PitonByteArray*)v.bits;
        piton_gc_unregister(ba);piton_heap_free(ba);break;}
    case PK_OBJECT:{PitonObject*o=(PitonObject*)v.bits;
        if(o->finalizer&&!o->finalizer_called){o->finalizer_called=1;((long(*)(long))o->finalizer)((long)o);}
        for(long i=0;i<o->length;++i)piton_slot_decref(o->attrs[i].value);
        piton_gc_unregister(o);piton_heap_free(o);--live_objects;break;}
    default:break;
    }
}
/* GC cycle collector: snapshot / protect / detach / free */
static void piton_gc_detach_node(void*raw){
    if(!raw)return;long kind=((long*)raw)[1];
    PitonSlot none={0,PK_NONE};
    switch(kind){
    case PK_LIST:case PK_TUPLE:{PitonSeq*s=(PitonSeq*)raw;
        for(long i=0;i<s->length;++i){PitonSlot old=s->items[i];s->items[i]=none;piton_slot_decref(old);}break;}
    case PK_DICT:{PitonDict*d=(PitonDict*)raw;
        for(long i=0;i<d->length;++i){PitonSlot ok=d->items[i].key,ov=d->items[i].value;
            d->items[i].key=none;d->items[i].value=none;piton_slot_decref(ok);piton_slot_decref(ov);}break;}
    case PK_SET:{PitonSet*s=(PitonSet*)raw;
        for(long i=0;i<s->length;++i){PitonSlot old=s->items[i];s->items[i]=none;piton_slot_decref(old);}break;}
    case PK_OBJECT:{PitonObject*o=(PitonObject*)raw;
        for(long i=0;i<o->length;++i){PitonSlot old=o->attrs[i].value;o->attrs[i].value=none;piton_slot_decref(old);}break;}
    default:break;
    }
}
static void piton_gc_free_node(void*raw){
    if(!raw)return;long kind=((long*)raw)[1];
    piton_gc_unregister(raw);
    switch(kind){
    case PK_LIST:case PK_TUPLE:{PitonSeq*s=(PitonSeq*)raw;piton_heap_free(s);--live_collections;break;}
    case PK_DICT:{PitonDict*d=(PitonDict*)raw;piton_heap_free(d);--live_dicts;break;}
    case PK_SET:{PitonSet*s=(PitonSet*)raw;piton_heap_free(s);--live_sets;break;}
    case PK_RANGE:{PitonRange*r=(PitonRange*)raw;piton_heap_free(r);break;}
    case PK_BYTES:case PK_BYTEARRAY:{piton_heap_free(raw);break;}
    case PK_OBJECT:{PitonObject*o=(PitonObject*)raw;piton_heap_free(o);--live_objects;break;}
    default:break;
    }
}
static void piton_gc_collect(void){
    long n=gc_count;if(!n)return;
    void**snapshot=(void**)piton_heap_alloc((usize)n*sizeof(void*));
    piton_memcpy(snapshot,gc_nodes,(usize)n*sizeof(void*));
    for(long i=0;i<n;++i){long*k=(long*)snapshot[i];++(*k);}
    for(long i=0;i<n;++i)piton_gc_detach_node(snapshot[i]);
    for(long i=0;i<n;++i)piton_gc_free_node(snapshot[i]);
    piton_heap_free(snapshot);
}
static long piton_total_live_count(void){return live_collections+live_dicts+live_sets+live_objects;}
/* C-harness API wrappers for GC tests */
static void*piton_collection_new(long kind,long cap){
    piton_heap_init();
    PitonSeq*s=(PitonSeq*)piton_heap_alloc(sizeof(PitonSeq));
    s->refcount=1;s->kind=kind;s->length=0;s->capacity=cap;
    s->items=cap>0?(PitonSlot*)piton_heap_alloc((usize)cap*sizeof(PitonSlot)):0;
    piton_gc_register(s);++live_collections;return s;
}
static void piton_list_append(void*raw,long val,long tag){
    PitonSeq*s=(PitonSeq*)raw;if(!s)return;
    if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;
        PitonSlot*na=(PitonSlot*)piton_heap_alloc((usize)nc*sizeof(PitonSlot));
        if(s->items){for(long i=0;i<s->length;++i)na[i]=s->items[i];}
        s->items=na;s->capacity=nc;}
    PitonSlot v={val,(int)tag};piton_slot_incref(v);s->items[s->length++]=v;
}
static void piton_collection_free(void*raw){
    if(!raw)return;PitonSlot v={0,PK_NONE};
    long kind=((long*)raw)[1];v.bits=(long)raw;v.kind=kind;piton_slot_decref(v);
}
static void*piton_gc_object_new(const char*name){
    piton_heap_init();
    PitonObject*o=(PitonObject*)piton_heap_alloc(sizeof(PitonObject));
    o->refcount=1;o->kind=PK_OBJECT;o->class_name=name;o->parent_name=0;o->length=0;
    o->finalizer=0;o->finalizer_called=0;o->next_all=0;
    piton_gc_register(o);++live_objects;return o;
}
static void piton_object_set_tagged(void*raw,const char*name,long val,long tag){
    PitonObject*o=(PitonObject*)raw;if(!raw)return;
    for(long i=0;i<o->length;++i){
        if(piton_strcmp(o->attrs[i].name,name)==0){
            piton_slot_decref(o->attrs[i].value);
            PitonSlot v={val,(int)tag};piton_slot_incref(v);o->attrs[i].value=v;return;}
    }
    if(o->length>=32){piton_write(2,"AttributeError\n",15);piton_exit(1);}
    o->attrs[o->length].name=name;
    PitonSlot v={val,(int)tag};piton_slot_incref(v);o->attrs[o->length].value=v;o->length++;
}
static void piton_object_free(void*raw){piton_collection_free(raw);}
static PitonSlot piton_slot(long bits,int kind){PitonSlot v={bits,kind};return v;}
static long piton_double_bits(double d){union{double d;unsigned long u;}v={d};return(long)v.u;}
static double piton_bits_double(long bits){union{double d;unsigned long u;}v;v.u=(unsigned long)bits;return v.d;}
static double piton_round_h(double x){return __builtin_floor(x + (x < 0.0 ? -0.5 : 0.5));}
static long piton_float_add(long a,long b){return piton_double_bits(piton_bits_double(a)+piton_bits_double(b));}
static long piton_float_sub(long a,long b){return piton_double_bits(piton_bits_double(a)-piton_bits_double(b));}
static long piton_float_mul(long a,long b){return piton_double_bits(piton_bits_double(a)*piton_bits_double(b));}
static long piton_float_neg(long a){return(long)((unsigned long)a^(1UL<<63));}
static long piton_float_sqrt(long a){double x=piton_bits_double(a),r;__asm__ volatile("sqrtsd %1,%0":"=x"(r):"x"(x));return piton_double_bits(r);}
static double piton_pow10(int p){if(p<0)p=0;if(p>15)p=15;double r=1.0;for(int i=0;i<p;++i)r*=10.0;return r;}
static int piton_frac_digits(long whole_scaled,int width,char*out){long w=whole_scaled;char rev[64];int rn=0;if(w==0)rev[rn++]='0';while(w){rev[rn++]=(char)('0'+w%10);w/=10;}for(int i=0;i<rn;++i)out[i]=rev[rn-1-i];out[rn]=0;return rn;}
static long piton_float_fmt_fixed(long bits,int prec){double x=piton_bits_double(bits);int p=prec>=0?prec:6;int neg=x<0;if(neg)x=-x;double scaled=piton_round_h(x*piton_pow10(p));char digits[80];int rn=piton_frac_digits((long)scaled,p+2,digits);char*out=piton_alloc(128);int o=0;if(neg)out[o++]='-';if(rn>p){for(int i=0;i<rn-p;++i)out[o++]=digits[i];if(p>0){out[o++]='.';for(int i=rn-p;i<rn;++i)out[o++]=digits[i];}}else{out[o++]='0';if(p>0){out[o++]='.';for(int i=0;i<p-rn;++i)out[o++]='0';for(int i=0;i<rn;++i)out[o++]=digits[i];}}out[o]=0;return(long)out;}
static long piton_float_fmt_pct(long bits,int prec){double x=piton_bits_double(bits)*100.0;long s=piton_float_fmt_fixed(piton_double_bits(x),prec>=0?prec:6);char*p=(char*)s;usize n=piton_strlen(p);char*r=piton_alloc(n+2);piton_memcpy(r,p,n+1);r[n]='%';r[n+1]=0;return(long)r;}
static long piton_float_fmt_exp(long bits,int prec,int upper){double x=piton_bits_double(bits);int p=prec>=0?prec:6;int neg=x<0;if(neg)x=-x;int exp=0;double m=x;while(m>=10.0){m/=10.0;++exp;}while(m<1.0&&m>0.0){m*=10.0;--exp;}double scaled=piton_round_h(m*piton_pow10(p));char digits[80];int rn=piton_frac_digits((long)scaled,p+2,digits);char*out=piton_alloc(64);int o=0;if(neg)out[o++]='-';if(rn>p+1){out[o++]='1';if(p>0){out[o++]='.';for(int i=0;i<p;++i)out[o++]=digits[1+i];}++exp;}else{out[o++]=rn>0?digits[0]:'0';if(p>0){out[o++]='.';for(int i=1;i<=p;++i)out[o++]=i<rn?digits[i]:'0';}}out[o++]=upper?'E':'e';out[o++]=exp<0?'-':'+';int ae=exp<0?-exp:exp;if(ae<10){out[o++]='0';out[o++]=(char)('0'+ae);}else{out[o++]=(char)('0'+ae/10);out[o++]=(char)('0'+ae%10);}out[o]=0;return(long)out;}
static long piton_float_fmt_g(long bits,int upper){double x=piton_bits_double(bits);if(x==0.0){char*z=piton_alloc(2);z[0]='0';z[1]=0;return(long)z;}int neg=x<0;if(neg)x=-x;int exp=0;double m=x;while(m>=10.0){m/=10.0;++exp;}while(m<1.0){m*=10.0;--exp;}if(exp<-4||exp>=6){char*s=(char*)piton_float_fmt_exp(bits,5,upper);/* trim zeros */char*d=s+1;while(d<s+50&&*d&&*d!='e'&&*d!='E')++d;char*e=d;while(e>s&&*(e-1)=='0')--e;if(e>s&&*(e-1)=='.')--e;for(char*q=d;*q;++q)e++[0]=q[0];return(long)s;}int p2=6-1-exp;if(p2<0)p2=0;char*s=(char*)piton_float_fmt_fixed(bits,p2);char*d=s;while(*d&&*d!='.')++d;if(*d=='.'){char*q=d+piton_strlen(d)-1;while(q>d&&*q=='0')--q;if(*q=='.')*q++=0;else *(q+1)=0;}else {}return(long)s;}
static long piton_float_floor(long a){double x=piton_bits_double(a);if(x!=x){piton_write(2,"ValueError: cannot convert float NaN to integer\n",48);piton_exit(1);}double f=__builtin_floor(x);if(f>9.2233720368547758e18||f<-9.2233720368547758e18){piton_write(2,"OverflowError: cannot convert float infinity to integer\n",56);piton_exit(1);}return(long)f;}
static long piton_float_ceil(long a){double x=piton_bits_double(a);if(x!=x){piton_write(2,"ValueError: cannot convert float NaN to integer\n",48);piton_exit(1);}double f=__builtin_ceil(x);if(f>9.2233720368547758e18||f<-9.2233720368547758e18){piton_write(2,"OverflowError: cannot convert float infinity to integer\n",56);piton_exit(1);}return(long)f;}
/* ── freestanding math: PITON's own sin/cos/log (no libm, -nostdlib) ──────
   __builtin_sin/cos/log have no definition to call under -nostdlib, so these
   are provided here. Argument reduction carries every intermediate rounding
   explicitly (Dekker two-sum / two-prod) over a 106-bit pi/2, which is exact
   for |x| < 1.4e16 -- beyond that n = round(2x/pi) no longer fits a double and
   a correct answer would need the Payne-Hanek algorithm; we return NaN rather
   than a plausible-but-wrong number. */
static void piton_dsplit(double a,double*hi,double*lo){double t=134217729.0*a;double h=t-(t-a);*lo=a-h;*hi=h;}
static double piton_dsum(double a,double b,double*e){double s=a+b;double bb=s-a;*e=(a-(s-bb))+(b-bb);return s;}
static double piton_dprod(double a,double b,double*e){double p=a*b;double ah,al,bh,bl;piton_dsplit(a,&ah,&al);piton_dsplit(b,&bh,&bl);*e=((ah*bh-p)+ah*bl+al*bh)+al*bl;return p;}
/* y = x - n*(pi/2) */
static double piton_pi2_delta(double n,double x){double ph,e1,pl,pe,t,te,s,se,d,de;ph=piton_dprod(n,0x1.921fb54442d18p+0,&e1);pl=piton_dprod(n,0x1.1a62633145c07p-54,&pe);t=piton_dsum(e1,pl,&te);s=piton_dsum(x,-ph,&se);d=piton_dsum(s,-t,&de);return d+(de+se-te-pe);}
/* x = n*(pi/2) + y with |y| <= pi/4; returns n mod 4, or -1 with y = NaN */
static int piton_pi2_reduce(double x,double*y){const double IPI=0x1.45f306dc9c883p-1;const double B=0.7853981633974483;double q,n;int i,c;if(!(x>-1.4e16&&x<1.4e16)){*y=__builtin_nan("");return -1;}q=x*IPI;n=(double)(long long)(q+(q<0?-0.5:0.5));for(i=0;i<4;i++){*y=piton_pi2_delta(n,x);if(*y<=B&&*y>=-B)break;c=(int)(*y*IPI+(*y<0?-0.5:0.5));if(!c)break;n+=(double)c;}if(!(*y<=B&&*y>=-B)){*y=__builtin_nan("");return -1;}{long nn=(long)n;int r=(int)(nn%4);if(r<0)r+=4;return r;}}
static const double PITON_SIN_C[11]={0x1.0000000000000p+0,-0x1.5555555555555p-3,0x1.1111111111111p-7,-0x1.a01a01a01a01ap-13,0x1.71de3a556c734p-19,-0x1.ae64567f544e4p-26,0x1.6124613a86d09p-33,-0x1.ae7f3e733b81fp-41,0x1.952c77030ad4ap-49,-0x1.2f49b46814157p-57,0x1.71b8ef6dcf572p-66};
static const double PITON_COS_C[11]={0x1.0000000000000p+0,-0x1.0000000000000p-1,0x1.5555555555555p-5,-0x1.6c16c16c16c17p-10,0x1.a01a01a01a01ap-16,-0x1.27e4fb7789f5cp-22,0x1.1eed8eff8d898p-29,-0x1.93974a8c07c9dp-37,0x1.ae7f3e733b81fp-45,-0x1.6827863b97d97p-53,0x1.e542ba4020225p-62};
static double piton_sin_y(double y){double t=y*y,p=PITON_SIN_C[10];int k;for(k=9;k>=0;--k)p=PITON_SIN_C[k]+t*p;return y*p;}
static double piton_cos_y(double y){double t=y*y,p=PITON_COS_C[10];int k;for(k=9;k>=0;--k)p=PITON_COS_C[k]+t*p;return p;}
static double piton_sin(double x){double y;int q=piton_pi2_reduce(x,&y);if(q<0)return y;return q==0?piton_sin_y(y):q==1?piton_cos_y(y):q==2?-piton_sin_y(y):-piton_cos_y(y);}
static double piton_cos(double x){double y;int q=piton_pi2_reduce(x,&y);if(q<0)return y;return q==0?piton_cos_y(y):q==1?-piton_sin_y(y):q==2?-piton_cos_y(y):piton_sin_y(y);}
static double piton_log(double x){double m,u,u2,p,lm;long b;int be,k,n;if(x!=x)return x;if(x==0.0)return -__builtin_inf();if(x<0.0)return __builtin_nan("");if(x==__builtin_inf())return x;__builtin_memcpy(&b,&x,8);be=(int)((b>>52)&0x7FF);if(be==0){x=x*18446744073709551616.0;__builtin_memcpy(&b,&x,8);be=(int)((b>>52)&0x7FF);k=be-1023-64;}else k=be-1023;b=(b&0x800FFFFFFFFFFFFFL)|((long)1023<<52);__builtin_memcpy(&m,&b,8);u=(m-1.0)/(m+1.0);u2=u*u;p=1.0/41.0;for(n=19;n>=0;--n)p=1.0/(double)(2*n+1)+u2*p;lm=2.0*u*p;return (lm+(double)k*0x1.62e42fefa39efp-1)+(double)k*0x1.abc9e3b39803fp-56;}
static long piton_float_sin(long a){return piton_double_bits(piton_sin(piton_bits_double(a)));}
static long piton_float_cos(long a){return piton_double_bits(piton_cos(piton_bits_double(a)));}
static long piton_float_log(long a){return piton_double_bits(piton_log(piton_bits_double(a)));}
static void piton_write_uint(unsigned long v){char b[32];usize i=sizeof(b);do{b[--i]=(char)('0'+v%10);v/=10;}while(v);piton_write(piton_print_fd,b+i,sizeof(b)-i);}
static void piton_write_uint_big(double d){unsigned long u;double t=d;int i,n=0,be;unsigned long frac,m;int e;unsigned int w[32];unsigned int c;unsigned long cur,rem;int nz;char buf[400];__builtin_memcpy(&u,&t,8);frac=u&0xFFFFFFFFFFFFFULL;be=(int)((u>>52)&0x7FF);m=be?frac|0x10000000000000UL:frac;e=be?be-1075:-1074;for(i=0;i<32;++i)w[i]=0;w[0]=(unsigned int)m;w[1]=(unsigned int)(m>>32);while(e>0){c=0;for(i=0;i<32;++i){cur=((unsigned long)w[i]<<1)|c;w[i]=(unsigned int)cur;c=(unsigned int)(cur>>32);}--e;}for(;;){rem=0;nz=0;for(i=31;i>=0;--i){cur=(rem<<32)|w[i];w[i]=(unsigned int)(cur/10);rem=cur%10;if(w[i])nz=1;}buf[n++]=(char)('0'+(char)rem);if(!nz)break;}while(n>0){--n;piton_write(piton_print_fd,buf+n,1);}}
static void piton_print_float_bits_raw(long bits){char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)bits);piton_write(piton_print_fd,r,(usize)n);}
static void piton_print_float_bits(long bits){piton_print_float_bits_raw(bits);piton_write(piton_print_fd,"\n",1);}
static int piton_bytes_cmp(PitonBytes* a, PitonBytes* b);
static long piton_set_issubset(PitonSet*a,PitonSet*b);
static int piton_dict_eq(PitonDict*a,PitonDict*b);
static int piton_slot_eq(PitonSlot a,PitonSlot b){
  /* CPython: 1 == 1.0 es True (int/float se comparan por valor). */
  if((a.kind==PK_INT||a.kind==PK_BOOL)&&b.kind==PK_FLOAT)return (double)a.bits==piton_bits_double(b.bits);
  if(a.kind==PK_FLOAT&&(b.kind==PK_INT||b.kind==PK_BOOL))return piton_bits_double(a.bits)==(double)b.bits;
  if((a.kind==PK_FROZENSET||a.kind==PK_SET)&&(b.kind==PK_FROZENSET||b.kind==PK_SET))return piton_set_issubset((PitonSet*)a.bits,(PitonSet*)b.bits)&&piton_set_issubset((PitonSet*)b.bits,(PitonSet*)a.bits);if(a.kind!=b.kind)return 0;if(a.kind==PK_LIST||a.kind==PK_TUPLE){PitonSeq*x=(PitonSeq*)a.bits,*y=(PitonSeq*)b.bits;if(x->length!=y->length)return 0;for(long i=0;i<x->length;++i)if(!piton_slot_eq(x->items[i],y->items[i]))return 0;return 1;}if(a.kind==PK_DICT)return piton_dict_eq((PitonDict*)a.bits,(PitonDict*)b.bits);if(a.kind==PK_RANGE)return piton_range_eq((PitonRange*)a.bits,(PitonRange*)b.bits);if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)==0;if(a.kind==PK_BYTES||a.kind==PK_BYTEARRAY)return piton_bytes_cmp((PitonBytes*)a.bits,(PitonBytes*)b.bits)==0;if(a.kind==PK_COMPLEX){PitonComplex*x=(PitonComplex*)a.bits,*y=(PitonComplex*)b.bits;return x->re==y->re&&x->im==y->im;}return a.bits==b.bits;}
static PitonSeq*piton_seq_new(int kind,long n){PitonSeq*s=piton_alloc(sizeof(*s));s->refcount=1;s->kind=(long)kind;s->length=n;s->capacity=n;s->items=n>0?piton_alloc((usize)n*sizeof(PitonSlot)):0;return s;}
static void piton_seq_put(PitonSeq*s,long i,PitonSlot v){if(i>=0&&i<s->length)s->items[i]=v;}
static long piton_str_repeat(const char*s,long n){if(n<=0){char*p=piton_alloc(1);p[0]=0;return(long)p;}usize sl=piton_strlen(s);char*p=piton_alloc(sl*(usize)n+1);for(long i=0;i<n;++i)piton_memcpy(p+i*sl,s,sl);p[sl*(usize)n]=0;return(long)p;}
static PitonSeq* piton_seq_repeat_n(PitonSeq*s,long n){if(!s)return 0;if(n<0)n=0;long total=s->length*n;PitonSeq*r=piton_seq_new((int)s->kind,total);for(long i=0;i<n;++i)for(long j=0;j<s->length;++j)r->items[i*s->length+j]=s->items[j];return r;}
static long piton_seq_concat(PitonSeq*a,PitonSeq*b){if(!a||!b||a->kind!=b->kind||((a->kind!=PK_LIST)&&(a->kind!=PK_TUPLE))){piton_write(2,"TypeError: cannot concatenate\n",30);piton_exit(1);}PitonSeq*s=piton_seq_new((int)a->kind,a->length+b->length);for(long i=0;i<a->length;++i)s->items[i]=a->items[i];for(long i=0;i<b->length;++i)s->items[a->length+i]=b->items[i];return(long)s;}
static long piton_str_contains(const char*h,const char*n){usize hl=piton_strlen(h),nl=piton_strlen(n);if(nl==0)return 1;if(nl>hl)return 0;for(usize i=0;i+nl<=hl;++i){usize j=0;while(j<nl&&h[i+j]==n[j])++j;if(j==nl)return 1;}return 0;}
static long piton_seq_contains(PitonSeq*s,PitonSlot v){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return 1;return 0;}
static long piton_dict_contains(PitonDict*d,PitonSlot v){if(!d)return 0;for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,v))return 1;return 0;}
static long piton_set_contains(PitonSet*s,PitonSlot v){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return 1;return 0;}
static void piton_seq_append(PitonSeq*s,PitonSlot v){if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*na=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(na,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=na;s->capacity=nc;}s->items[s->length++]=v;}
static int piton_ws(unsigned char c){return c==' '||c=='\t'||c=='\n'||c=='\r'||c=='\v'||c=='\f';}
static long piton_str_case(const char*s,int upper){usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c>=0x80){piton_write(2,"ValueError: str case conversion on non-ASCII text is not supported in the native subset\n",88);piton_exit(1);}p[i]=(char)(upper?((c>='a'&&c<='z')?c-32:c):((c>='A'&&c<='Z')?c+32:c));}p[n]=0;return(long)p;}
static long piton_str_find(const char*s,const char*n){usize hl=piton_strlen(s),nl=piton_strlen(n);if(nl==0)return 0;if(nl>hl)return -1;for(usize i=0;i+nl<=hl;++i){usize j=0;while(j<nl&&s[i+j]==n[j])++j;if(j==nl)return(long)i;}return -1;}
/* STR_FIND_OFFSET_V1: str.find(sub, start) — busqueda desde el offset
   start con la misma receta que startswith/endswith: negativo se mide
   desde el final y se recorta a 0; start > len devuelve -1 (CPython). */
static long piton_str_find_at(const char*s,const char*n,long start){
  usize hl=piton_strlen(s),nl=piton_strlen(n);
  long st=start;
  if(st<0)st=(long)hl+st;
  if(st<0)st=0;
  if(st>(long)hl)return -1;
  if(nl==0)return st;
  for(usize i=(usize)st;i+nl<=hl;++i){usize k=0;while(k<nl&&s[i+k]==n[k])++k;if(k==nl)return(long)i;}
  return -1;
}
static long piton_str_startswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[i]!=p[i])return 0;return 1;}
static long piton_str_endswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[hl-nl+i]!=p[i])return 0;return 1;}
/* STR_OFFSET_PREFIX_V1: s.startswith(p, start) / s.endswith(p, start) — la
   comparacion ocurre sobre s[start:] (CPython), con start fuera de rango
   -> False (startswith) / True solo si el sufijo cabe (endswith). */
static long piton_str_startswith_at(const char*s,const char*p,long start){usize hl=piton_strlen(s),nl=piton_strlen(p);if(start<0)start=0;if((usize)start>hl)return 0;if(nl>hl-(usize)start)return 0;for(usize i=0;i<nl;++i)if(s[start+i]!=p[i])return 0;return 1;}
static long piton_str_endswith_at(const char*s,const char*p,long start){usize hl=piton_strlen(s),nl=piton_strlen(p);if(start<0)start=0;if((usize)start>hl)return 0;if(nl>hl-(usize)start)return 0;for(usize i=0;i<nl;++i)if(s[hl-nl+i]!=p[i])return 0;return 1;}
/* STR_TRANSLATE_V1: translate(dict) — cada carácter se sustituye por el
 * mapeo; una clave ausente deja el carácter; valor None/'' borra (V1: valor
 * string vacío). */
static long piton_str_translate(const char*s,PitonDict*map){
  if(!s)return 0;
  usize n=piton_strlen(s);
  char*p=piton_alloc(n*4+1);usize o=0;
  for(usize i=0;i<n;){
    unsigned char c=(unsigned char)s[i];
    if(c<0x80){
      long found=0;
      /* CPython: las claves son enteros (ord); una clave cadena de longitud 1
       * NO sustituye (str.maketrans usa ord como clave). */
      if(map)for(long j=0;j<map->length;++j){
        if(map->items[j].key.kind==PK_INT&&map->items[j].key.bits==(long)c){
          const char*v=(const char*)map->items[j].value.bits;
          if(v){usize vl=piton_strlen(v);piton_memcpy(p+o,v,vl);o+=vl;}
          found=1;break;
        }
      }
      if(!found)p[o++]=(char)c;
      ++i;continue;
    }
    /* multibyte: se copia tal cual (las claves del mapa son ASCII) */
    int k;if((c&0xE0)==0xC0)k=2;else if((c&0xF0)==0xE0)k=3;else if((c&0xF8)==0xF0)k=4;else k=1;
    piton_memcpy(p+o,s+i,(usize)k);o+=(usize)k;i+=(usize)k;
  }
  p[o]=0;
  return(long)p;
}
static long piton_str_replace(const char*s,const char*a,const char*b){usize sl=piton_strlen(s),al=piton_strlen(a),bl=piton_strlen(b);usize count=0;if(al==0){count=sl+1;}else{for(usize i=0;i+al<=sl;){usize k=0;while(k<al&&s[i+k]==a[k])++k;if(k==al){++count;i+=al;}else++i;}}usize total=sl+count*bl-(al==0?0:count*al);char*p=piton_alloc(total+1);usize o=0;if(al==0){for(usize i=0;i<sl;++i){piton_memcpy(p+o,b,bl);o+=bl;p[o++]=s[i];}piton_memcpy(p+o,b,bl);o+=bl;}else{for(usize i=0;i<sl;){usize k=0;while(k<al&&i+k<sl&&s[i+k]==a[k])++k;if(k==al){piton_memcpy(p+o,b,bl);o+=bl;i+=al;}else p[o++]=s[i++];}}p[o]=0;return(long)p;}
static int piton_chrinstr(const char*set,char c){if(!set)return piton_ws((unsigned char)c);for(usize i=0;set[i];++i)if(set[i]==c)return 1;return 0;}
static long piton_str_strip_chrs(const char*s,const char*chrs,int mode){usize n=piton_strlen(s),a=0,b=n;if(mode!=2){while(a<n&&piton_chrinstr(chrs,s[a]))++a;}if(mode!=1){while(b>a&&piton_chrinstr(chrs,s[b-1]))--b;}char*p=piton_alloc(b-a+1);piton_memcpy(p,s+a,b-a);p[b-a]=0;return(long)p;}
static long piton_str_capitalize(const char*s){usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(!i){if(c>='a'&&c<='z')c-=32;}else{if(c>='A'&&c<='Z')c+=32;}p[i]=(char)c;}p[n]=0;return(long)p;}
/* HASH_V1: hash() observable. El contrato del corpus es que VALORES IGUALES
 * den hashes iguales (hash(42)==hash(42), hash('a')==hash('a'),
 * hash(Verdadero)==hash(1)). El valor absoluto no es portable en CPython
 * (aleatorio por semilla), así que solo se garantiza la consistencia. */
static long piton_hash_long(long v);
static long piton_hash_long(long v){
  /* mezcla del entero; 1 y Verdadero deben coincidir (ambos son 1) */
  unsigned long h=1469598103934665603UL;
  for(int i=0;i<8;++i){h^=(unsigned long)((v>>(i*8))&0xFF);h*=1099511628211UL;}
  return(long)h;
}
/* STR_CASEFOLD_V1: casefold() coincide con lower() para ASCII (el caso
 * turco/alemán completo necesita tablas Unicode fuera del subset: se declara
 * como hueco conocido si aparece un caso no-ASCII). */
static long piton_str_casefold(const char*s){if(!s)s="";usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c>='A'&&c<='Z')c=(unsigned char)(c+32);p[i]=(char)c;}p[n]=0;return(long)p;}
static long piton_str_isascii(const char*s){if(!s)return 1;for(usize i=0;s[i];++i)if((unsigned char)s[i]>=0x80)return 0;return 1;}
/* STR_ISPRINTABLE_V1 / STR_ISIDENTIFIER_V1 (ASCII + multibyte bien formado). */
static long piton_str_isprintable(const char*s){
  if(!s)return 1;
  for(usize i=0;s[i];){
    unsigned char c=(unsigned char)s[i];
    if(c<0x80){if(c<0x20||c==0x7F)return 0;++i;continue;}
    int n;if((c&0xE0)==0xC0)n=2;else if((c&0xF0)==0xE0)n=3;else if((c&0xF8)==0xF0)n=4;else return 0;
    for(int k=1;k<n;++k)if(((unsigned char)s[i+k]&0xC0)!=0x80)return 0;
    i+=(usize)n;
  }
  return 1;
}
static long piton_str_isidentifier(const char*s){
  if(!s||!s[0])return 0;
  for(usize i=0;s[i];){
    unsigned char c=(unsigned char)s[i];
    if(c<0x80){
      int alpha=(c>='a'&&c<='z')||(c>='A'&&c<='Z')||c=='_';
      int digit=(c>='0'&&c<='9');
      if(i==0?!alpha:!(alpha||digit))return 0;
      ++i;continue;
    }
    int n;if((c&0xE0)==0xC0)n=2;else if((c&0xF0)==0xE0)n=3;else if((c&0xF8)==0xF0)n=4;else return 0;
    for(int k=1;k<n;++k)if(((unsigned char)s[i+k]&0xC0)!=0x80)return 0;
    i+=(usize)n;
  }
  return 1;
}
static long piton_str_title(const char*s){usize n=piton_strlen(s);char*p=piton_alloc(n+1);int wordbreak=1;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];int letter=(c>='a'&&c<='z')||(c>='A'&&c<='Z');if(letter){if(wordbreak&&c>='a'&&c<='z')c-=32;else if(!wordbreak&&c>='A'&&c<='Z')c+=32;wordbreak=0;}else wordbreak=1;p[i]=(char)c;}p[n]=0;return(long)p;}
static long piton_str_swapcase(const char*s){usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c>='a'&&c<='z')p[i]=(char)(c-32);else if(c>='A'&&c<='Z')p[i]=(char)(c+32);else p[i]=(char)c;}p[n]=0;return(long)p;}
static long piton_str_zfill(const char*s,long width){usize n=piton_strlen(s);usize sign=(n&&(s[0]=='+'||s[0]=='-'))?1:0;if((long)n>=width){char*p=piton_alloc(n+1);piton_memcpy(p,s,n+1);return(long)p;}usize pad=(usize)width-n;char*p=piton_alloc((usize)width+1);usize o=0;if(sign){p[o++]=s[0];}for(usize i=0;i<pad;++i)p[o++]='0';piton_memcpy(p+o,s+sign,n-sign+1);return(long)p;}
static long piton_str_padw(const char*s,long width,int left,char fill){usize n=piton_strlen(s);if((long)n>=width){char*p=piton_alloc(n+1);piton_memcpy(p,s,n+1);return(long)p;}usize pad=(usize)width-n;char*p=piton_alloc((usize)width+1);usize o=0;if(!left){piton_memcpy(p+o,s,n);for(usize i=0;i<pad;++i)p[o+n+i]=fill;p[width]=0;}else{for(usize i=0;i<pad;++i)p[o++]=fill;piton_memcpy(p+pad,s,n);p[width]=0;}return(long)p;}
static long piton_str_center(const char*s,long width,char fill){usize n=piton_strlen(s);if((long)n>=width){char*p=piton_alloc(n+1);piton_memcpy(p,s,n+1);return(long)p;}usize pad=(usize)width-n,left=pad/2+((pad&(usize)width)&1);char*p=piton_alloc((usize)width+1);for(usize i=0;i<left;++i)p[i]=fill;piton_memcpy(p+left,s,n);for(usize i=left+n;i<(usize)width;++i)p[i]=fill;p[width]=0;return(long)p;}
static long piton_str_count(const char*s,const char*sub){usize n=piton_strlen(s),m=piton_strlen(sub);long cnt=0;if(m==0)return (long)(n+1);for(usize i=0;i+m<=n;){usize k=0;while(k<m&&s[i+k]==sub[k])++k;if(k==m){++cnt;i+=m;}else ++i;}return cnt;}
static long piton_str_rfind(const char*s,const char*sub){usize n=piton_strlen(s),m=piton_strlen(sub);if(m==0)return(long)n;if(m>n)return -1;for(long i=(long)(n-m);i>=0;--i){usize k=0;while(k<m&&s[i+k]==sub[k])++k;if(k==m)return i;}return -1;}
static void piton_str_index_err(void){piton_write(2,"ValueError: substring not found\n",32);piton_exit(1);}
static long piton_str_index_f(const char*s,const char*sub){long v=piton_str_find(s,sub);if(v<0)piton_str_index_err();return v;}
static long piton_str_rindex_f(const char*s,const char*sub){long v=piton_str_rfind(s,sub);if(v<0)piton_str_index_err();return v;}
static int piton_str_islapha_impl(const char*s,int mode){usize n=piton_strlen(s);if(n==0)return 0;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];int letter=(c>='a'&&c<='z')||(c>='A'&&c<='Z');int digit=(c>='0'&&c<='9');switch(mode){case 0:if(!letter)return 0;break;case 1:if(!digit)return 0;break;case 2:if(!letter&&!digit)return 0;break;case 3:if(c!=' '&&c!='\t'&&c!='\n'&&c!='\v'&&c!='\f'&&c!='\r')return 0;break;}}return 1;}
static int piton_str_isupper_impl(const char*s,int upper){usize n=piton_strlen(s);int hascase=0;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(upper){if(c>='a'&&c<='z')return 0;if(c>='A'&&c<='Z')hascase=1;}else{if(c>='A'&&c<='Z')return 0;if(c>='a'&&c<='z')hascase=1;}}return hascase;}
static int piton_str_istitle_impl(const char*s){usize n=piton_strlen(s);int hascase=0,newword=1;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];int letter=(c>='a'&&c<='z')||(c>='A'&&c<='Z');if(letter){hascase=1;if(newword){if(!(c>='A'&&c<='Z'))return 0;newword=0;}else{if(!(c>='a'&&c<='z'))return 0;}}else newword=1;}return hascase;}
static long piton_str_from_int_base(long v,int base,int upper){char*p=piton_alloc(70);usize o=0;unsigned long u;if(v<0){p[o++]='-';u=(unsigned long)(-(v+1))+1;}else u=(unsigned long)v;const char*digits=upper?"0123456789ABCDEF":"0123456789abcdef";char tmp[64];long n=0;do{tmp[n++]=digits[u%(unsigned)base];u/=(unsigned)base;}while(u);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static usize piton_utf8_chars(const char*s,usize maxbytes){usize i=0;long cc=0;while(s[i]&&(usize)cc<maxbytes){unsigned char c=(unsigned char)s[i];usize adv=1;if(c>=0x80){if((c&0xE0)==0xC0)adv=2;else if((c&0xF0)==0xE0)adv=3;else if((c&0xF8)==0xF0)adv=4;}i+=adv;++cc;}return i;}
static char* piton_str_apply_spec(const char*s,const char*spec){
    if(!s||!spec||!*spec)return 0;
    const char*p=spec;char fill=' ';int left=0,center=0;
    if(p[1]&&(p[1]=='<'||p[1]=='>'||p[1]=='^'||p[1]=='=')){fill=p[0];p+=2;}
    else if(*p=='<'||*p=='>'||*p=='^'||*p=='='){p+=1;}
    if(p[-1]=='<')left=1;else if(p[-1]=='^')center=1;
    int sign=0;if(*p=='+'){sign=1;p+=1;}else if(*p=='-'){p+=1;}else if(*p==' '){sign=2;p+=1;}
    if(*p=='#')p+=1;
    int zero=0;if(*p=='0'){zero=1;p+=1;}
    long width=0;while(*p>='0'&&*p<='9'){width=width*10+(*p-'0');p+=1;}
    int comma=0;if(*p==','){comma=1;p+=1;}
    long prec=-1;if(*p=='.'){p+=1;prec=0;while(*p>='0'&&*p<='9'){prec=prec*10+(*p-'0');p+=1;}}
    if(*p)return 0;
    int neg=0;const char*digits=s;if(s[0]=='-'){neg=1;digits=s+1;}
    usize dl=piton_strlen(digits);
    char*core=0;usize core_len=0;
    if(comma){
        usize int_len=0;while(int_len<dl&&digits[int_len]!='.')int_len++;
        usize groups=(int_len+2)/3;core_len=int_len+(groups-1)+(dl-int_len);
        core=piton_alloc(core_len+1);usize o=0,gi=0;
        for(usize i=0;i<int_len;++i){if(gi&&(int_len-gi)%3==0)core[o++]=',';core[o++]=digits[gi++];}
        for(usize i=int_len;i<dl;++i)core[o++]=digits[i];core[o]=0;
    } else {core_len=dl;core=piton_alloc(dl+1);piton_memcpy(core,digits,dl);core[dl]=0;}
    if(prec>=0&&core_len>(usize)prec){core[prec]=0;core_len=(usize)prec;}
    const char*sign_str="";if(neg)sign_str="-";else if(sign==1)sign_str="+";else if(sign==2)sign_str=" ";
    usize sign_len=piton_strlen(sign_str);
    usize total=sign_len+core_len;
    long pad=(width>0&&total<(usize)width)?(width-(long)total):0;
    char*out=piton_alloc(total+(usize)pad+1);usize o=0;
    if(left){piton_memcpy(out+o,sign_str,sign_len);o+=sign_len;piton_memcpy(out+o,core,core_len);o+=core_len;for(long i=0;i<pad;++i)out[o++]=fill;}
    else if(center){long lp=pad/2,rp=pad-lp;for(long i=0;i<lp;++i)out[o++]=fill;piton_memcpy(out+o,sign_str,sign_len);o+=sign_len;piton_memcpy(out+o,core,core_len);o+=core_len;for(long i=0;i<rp;++i)out[o++]=fill;}
    else{if(zero&&!center){piton_memcpy(out+o,sign_str,sign_len);o+=sign_len;for(long i=0;i<pad;++i)out[o++]='0';}else{for(long i=0;i<pad;++i)out[o++]=fill;piton_memcpy(out+o,sign_str,sign_len);o+=sign_len;}piton_memcpy(out+o,core,core_len);o+=core_len;}
    out[o]=0;return out;
}
static long piton_str_pad(const char*s,long width,long prec,long flags,int isnum){int neg=0;const char*digits=s;if(isnum&&s[0]=='-'){neg=1;digits=s+1;}usize dl=piton_strlen(digits);const char*core=digits;usize cl=dl;if(prec>=0){if(isnum){usize need=(usize)prec;if(dl==1&&digits[0]=='0'&&prec==0)need=0;if(dl<need){char*pad=piton_alloc(need+1);for(usize i=0;i<need-dl;++i)pad[i]='0';piton_memcpy(pad+need-dl,digits,dl);pad[need]=0;core=pad;cl=need;}}else{usize cut=piton_utf8_chars(s,(usize)prec);char*tr=piton_alloc(cut+1);piton_memcpy(tr,s,cut);tr[cut]=0;core=tr;cl=cut;neg=0;}}usize totallen=cl+(neg?1:0);long pad=(width>0&&totallen<(usize)width)?(width-(long)totallen):0;int left=(flags&1)!=0;int zero=(flags&2)&&!left&&isnum&&prec<0;char*out=piton_alloc(totallen+(usize)pad+1);usize o=0;if(!left){if(zero){if(neg)out[o++]='-';for(long i=0;i<pad;++i)out[o++]='0';}else{for(long i=0;i<pad;++i)out[o++]=' ';if(neg)out[o++]='-';}}else if(neg){out[o++]='-';}piton_memcpy(out+o,core,cl);o+=cl;if(left){for(long i=0;i<pad;++i)out[o++]=' ';}out[o]=0;return(long)out;}
static long piton_str_pred(const char*s,int mode){if(!s||!s[0])return 0;long r=1;int saw_cased=0;int i=0;while(s[i]){unsigned char c=(unsigned char)s[i];int alpha=(c>='a'&&c<='z')||(c>='A'&&c<='Z');int dig=(c>='0'&&c<='9');if(alpha)saw_cased=1;if(mode==0){if(!alpha){r=0;break;}}else if(mode==1){if(!dig){r=0;break;}}else if(mode==2){if(!alpha&&!dig){r=0;break;}}else if(mode==3){if(!(c==' '||c=='\t'||c=='\n'||c=='\r'||c=='\v'||c=='\f')){r=0;break;}}i++;}if(mode>=4&&mode<=6){if(!saw_cased)return 0;if(mode==4){int prev_alpha=0;for(usize j=0;s[j];++j){unsigned char c=(unsigned char)s[j];int alpha=(c>='a'&&c<='z')||(c>='A'&&c<='Z');if(alpha){if(!prev_alpha){if(!(c>='A'&&c<='Z'))return 0;}else{if(!(c>='a'&&c<='z'))return 0;}}prev_alpha=alpha;}return 1;}for(usize j=0;s[j];++j){unsigned char c=(unsigned char)s[j];int alpha=(c>='a'&&c<='z')||(c>='A'&&c<='Z');if(!alpha)continue;if(mode==5){if(!(c>='A'&&c<='Z'))return 0;}else{if(!(c>='a'&&c<='z'))return 0;}}return 1;}return r;}
static long piton_str_just(const char*s,long width,long fill,int mode){if(!s)return 0;usize n=piton_strlen(s);long pad=(width>(long)n)?(width-(long)n):0;char f=(char)fill;char*p=piton_alloc((usize)(n+pad)+1);usize o=0;if(mode==0){for(usize i=0;i<n;++i)p[o++]=s[i];for(long i=0;i<pad;++i)p[o++]=f;}else if(mode==1){for(long i=0;i<pad;++i)p[o++]=f;for(usize i=0;i<n;++i)p[o++]=s[i];}else{long lp=pad/2+((pad&width)&1),rp=pad-lp;for(long i=0;i<lp;++i)p[o++]=f;for(usize i=0;i<n;++i)p[o++]=s[i];for(long i=0;i<rp;++i)p[o++]=f;}p[o]=0;return(long)p;}
static long piton_str_subindex(const char*s,const char*n){long i=piton_str_find(s,n);if(i<0){piton_write(2,"ValueError: substring not found\n",32);piton_exit(1);}return i;}
static long piton_str_subrindex(const char*s,const char*n){long i=piton_str_rfind(s,n);if(i<0){piton_write(2,"ValueError: substring not found\n",32);piton_exit(1);}return i;}
static long piton_str_quote(const char*s){usize n=piton_strlen(s);int q=0;for(usize i=0;i<n;++i)if(s[i]=='\'')q=1;char qc=q?'"':'\'';usize extra=0;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c=='\\'||c=='\n'||c=='\t'||c=='\r'||(unsigned char)c==qc)extra+=1;}char*p=piton_alloc(n+extra+3);usize o=0;p[o++]=qc;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c=='\\'){p[o++]='\\';p[o++]='\\';}else if(c=='\n'){p[o++]='\\';p[o++]='n';}else if(c=='\t'){p[o++]='\\';p[o++]='t';}else if(c=='\r'){p[o++]='\\';p[o++]='r';}else if(c==qc){p[o++]='\\';p[o++]=c;}else p[o++]=(char)c;}p[o++]=qc;p[o]=0;return(long)p;}
static long piton_str_single_char(const char*s){if(piton_strlen(s)!=1){piton_raise_set("TypeError","%c requires int or char");return 0;}return(long)s;}
static long piton_str_format(const char*t,long n,const char**av){usize total=0;int auto_idx=0;for(usize i=0;t[i];){if(t[i]=='{'){if(t[i+1]=='{'){total+=1;i+=2;continue;}usize j=i+1;int idx=-2;int has=0;if(t[j]=='}'){idx=auto_idx++;j+=1;has=1;}else{idx=0;while(t[j]>='0'&&t[j]<='9'){idx=idx*10+(t[j]-'0');j+=1;has=1;}if(has&&t[j]=='}'){j+=1;}else{has=0;}}if(!has){piton_write(2,"ValueError: single '{' in format string\n",40);piton_exit(1);}if(idx<0||idx>=n){piton_write(2,"IndexError: replacement index out of range\n",43);piton_exit(1);}total+=piton_strlen(av[idx]);i=j;}else if(t[i]=='}'){if(t[i+1]=='}'){total+=1;i+=2;}else{piton_write(2,"ValueError: single '}' in format string\n",40);piton_exit(1);}}else{total+=1;i+=1;}}char*p=piton_alloc(total+1);usize o=0;auto_idx=0;for(usize i=0;t[i];){if(t[i]=='{'){if(t[i+1]=='{'){p[o++]='{';i+=2;continue;}usize j=i+1;int idx=-2;if(t[j]=='}'){idx=auto_idx++;j+=1;}else{idx=0;while(t[j]>='0'&&t[j]<='9'){idx=idx*10+(t[j]-'0');j+=1;}j+=1;}usize el=piton_strlen(av[idx]);piton_memcpy(p+o,av[idx],el);o+=el;i=j;}else if(t[i]=='}'){p[o++]='}';i+=2;}else{p[o++]=t[i++];}}p[o]=0;return(long)p;}

static long piton_str_split_ws(const char*s){PitonSeq*r=piton_seq_new(PK_LIST,0);usize n=piton_strlen(s),i=0;while(i<n){while(i<n&&piton_ws((unsigned char)s[i]))++i;if(i>=n)break;usize j=i;while(j<n&&!piton_ws((unsigned char)s[j]))++j;usize len=j-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});i=j;}return(long)r;}
static long piton_str_rpartition(const char*s,const char*sep){usize n=piton_strlen(s),m=piton_strlen(sep);long at=-1;if(m>0&&m<=n){for(long i=(long)(n-m);i>=0;--i){usize k=0;while(k<m&&s[i+k]==sep[k])++k;if(k==m){at=i;break;}}}PitonSeq*r=piton_seq_new(PK_TUPLE,3);
  /* sin separador: CPython devuelve ('', '', s) — el original COMPLETO al final */
  long b=at<0?0:at,a=at<0?0:(long)(at+m);
  char*p1=piton_alloc((usize)b+1);piton_memcpy(p1,s,(usize)b);p1[b]=0;
  char*p2=piton_alloc(m+1);if(at<0)p2[0]=0;else piton_memcpy(p2,sep,m+1);
  usize take=at<0?n:(n-(usize)a);
  char*p3=piton_alloc(take+1);piton_memcpy(p3,at<0?s:(s+a),take);p3[take]=0;
  r->items[0]=(PitonSlot){(long)p1,PK_STR};r->items[1]=(PitonSlot){(long)p2,PK_STR};r->items[2]=(PitonSlot){(long)p3,PK_STR};return(long)r;}
static long piton_str_partition(const char*s,const char*sep){usize n=piton_strlen(s),m=piton_strlen(sep);long at=-1;for(usize i=0;i+m<=n;++i){usize k=0;while(k<m&&s[i+k]==sep[k])++k;if(k==m){at=(long)i;break;}}PitonSeq*r=piton_seq_new(PK_TUPLE,3);long b=at<0?(long)n:at,a=at<0?(long)n:(long)(at+m);char*p1=piton_alloc((usize)b+1);piton_memcpy(p1,s,(usize)b);p1[b]=0;char*p2=piton_alloc(m+1);if(at<0)p2[0]=0;else piton_memcpy(p2,sep,m+1);char*p3=piton_alloc(n-(usize)a+1);piton_memcpy(p3,s+a,n-(usize)a);p3[n-(usize)a]=0;r->items[0]=(PitonSlot){(long)p1,PK_STR};r->items[1]=(PitonSlot){(long)p2,PK_STR};r->items[2]=(PitonSlot){(long)p3,PK_STR};return(long)r;}
static long piton_str_rsplit(const char*s,const char*sep,long maxsplit){if(!sep)return piton_str_split_ws(s);usize n=piton_strlen(s),m=piton_strlen(sep);PitonSeq*r=piton_seq_new(PK_LIST,0);if(m==0){for(long k=(long)n;k>=0;--k){char*q=piton_alloc(2);q[0]=s[k];q[1]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});}return(long)r;}usize end=n;long budget=maxsplit<0?(long)n:maxsplit;while(budget>0){long at=-1;for(long i=(long)end-(long)m;i>=0;--i){usize k=0;while(k<m&&s[i+(long)k]==sep[k])++k;if(k==m){at=i;break;}}if(at<0)break;--budget;usize tail=end-(usize)at-m;char*q=piton_alloc(tail+1);piton_memcpy(q,s+(usize)at+m,tail);q[tail]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});end=(usize)at;}char*head=piton_alloc(end+1);piton_memcpy(head,s,end);head[end]=0;piton_seq_append(r,(PitonSlot){(long)head,PK_STR});long total=r->length;for(long i=0;i<total/2;++i){PitonSlot t=r->items[i];r->items[i]=r->items[total-1-i];r->items[total-1-i]=t;}return(long)r;}
static long piton_str_splitlines(const char*s){usize n=piton_strlen(s);PitonSeq*r=piton_seq_new(PK_LIST,0);usize i=0;while(i<n){usize j=i;while(j<n&&s[j]!='\n'&&s[j]!='\r')++j;usize take=j-i;if(take>0&&s[j-1]=='\r')--take;char*q=piton_alloc(take+1);piton_memcpy(q,s+i,take);q[take]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});if(j<n&&s[j]=='\r'&&j+1<n&&s[j+1]=='\n')i=j+2;else i=j+1;}return(long)r;}
static long piton_str_expandtabs(const char*s,long tabsize){usize n=piton_strlen(s);char*out=piton_alloc(n*2+n+1);usize o=0,col=0;if(tabsize<1)tabsize=1;for(usize i=0;i<n;++i){char c=s[i];if(c=='\t'){usize w=(usize)tabsize-(col%(usize)tabsize);for(usize k=0;k<w;++k)out[o++]=' ';col+=w;}else{out[o++]=c;++col;}}out[o]=0;return(long)out;}
/* SPLIT_MAXSPLIT_V1: s.split(sep, n) — a lo mas n splits (n+1 elementos);
   n<0 = sin limite. Busqueda explicita del match: el scan viejo del str_split
   perdia hasta nl-1 chars del ultimo segmento cuando el sep no aparecia
   ('a :b :c '.split(': ') devolvia 'a :b :c' — bug preexistente arreglado). */
static long piton_str_split_max(const char*s,const char*sep,long maxsplit){if(!sep)return piton_str_split_ws(s);PitonSeq*r=piton_seq_new(PK_LIST,0);usize sl=piton_strlen(s),nl=piton_strlen(sep);long budget=maxsplit<0?(long)sl+1:maxsplit;if(nl==0){for(usize k=0;k<=sl;++k){char*q=piton_alloc(2);q[0]=k<sl?s[k]:0;q[1]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});}return(long)r;}usize i=0;while(1){long at=-1;if(budget>0){for(long j=(long)i;(usize)j+nl<=sl;++j){usize k=0;while(k<nl&&s[(usize)j+k]==sep[k])++k;if(k==nl){at=j;break;}}}if(at<0){usize len=sl-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});break;}usize len=(usize)at-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});--budget;i=(usize)at+nl;}return(long)r;}
static long piton_str_split(const char*s,const char*sep){return piton_str_split_max(s,sep,-1);}
static long piton_str_strip(const char*s,int mode){usize n=piton_strlen(s),a=0,b=n;if(mode!=2){while(a<n&&piton_ws((unsigned char)s[a]))++a;}if(mode!=1){while(b>a&&piton_ws((unsigned char)s[b-1]))--b;}char*p=piton_alloc(b-a+1);piton_memcpy(p,s+a,b-a);p[b-a]=0;return(long)p;}
static long piton_str_join(const char*sep,PitonSeq*items){usize sl=piton_strlen(sep);usize total=0;long cnt=items?items->length:0;for(long i=0;i<cnt;++i){if(items->items[i].kind!=PK_STR){piton_write(2,"TypeError: sequence item is not a string\n",41);piton_exit(1);}total+=piton_strlen((const char*)items->items[i].bits);}total+=sl*(usize)(cnt>0?cnt-1:0);char*p=piton_alloc(total+1);usize o=0;for(long i=0;i<cnt;++i){if(i){piton_memcpy(p+o,sep,sl);o+=sl;}usize el=piton_strlen((const char*)items->items[i].bits);piton_memcpy(p+o,(const char*)items->items[i].bits,el);o+=el;}p[o]=0;return(long)p;}
/* Forward declarations: definiciones mas abajo. */
static PitonBytes* piton_bytes_concat(PitonBytes* a,PitonBytes* b);
static int piton_maybe_ptr(long bits);
static void piton_report_unhandled(void);
static double piton_strtod_local(const char*,char**);
/* BYTES_CONCAT_DYN_V1: `bytes + X` donde X puede tener tipo estatico int
   (inferencia de tupla desestructurada) pero ser bytes en runtime. Verifica
   el tipo real antes de concatenar; si no es bytes, TypeError claro. */
static long piton_bytes_concat_dyn(PitonSlot slot,PitonBytes*b){
  if(piton_maybe_ptr(slot.bits)){
    long*ptr=(long*)slot.bits;
    if(ptr[1]==PK_BYTES){
      return(long)piton_bytes_concat((PitonBytes*)slot.bits,b);
    }
  }
  piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'bytes'");
  piton_report_unhandled();piton_exit(1);return 0;
}
/* PCT_DYN_V1: `str % arg` con plantilla no literal (variable/atributo).
   Convierte ambos a str y sustituye el unico %s. Solo valido si la plantilla
   contiene exactamente un %s; si no, TypeError en runtime. */
static long piton_str_percent_dyn(long template_val,long arg_val){
  const char*tmpl=(const char*)template_val;
  const char*arg=(const char*)arg_val;
  if(!tmpl||!arg){piton_raise_set("TypeError","%s argument must be str");piton_report_unhandled();piton_exit(1);return 0;}
  /* Contar %s y verificar que no haya otros especificadores. */
  long n_pct=0;const char*pct=0;
  for(const char*p=tmpl;*p;++p){
    if(*p=='%'){
      if(*(p+1)=='s'){++n_pct;pct=p;}
      else if(*(p+1)!='%'){piton_raise_set("TypeError","str % formatting: non-literal template con especificador no soportado");piton_report_unhandled();piton_exit(1);return 0;}
    }
  }
  if(n_pct!=1){piton_raise_set("TypeError","str % formatting: non-literal template con !=1 %%s");piton_report_unhandled();piton_exit(1);return 0;}
  /* Calcular longitud total: antes + arg + despues */
  usize before=pct-tmpl;
  usize arglen=piton_strlen(arg);
  usize after=piton_strlen(pct+2);
  usize total=before+arglen+after;
  char*buf=(char*)piton_alloc(total+1);
  usize o=0;
  piton_memcpy(buf+o,tmpl,before);o+=before;
  piton_memcpy(buf+o,arg,arglen);o+=arglen;
  piton_memcpy(buf+o,pct+2,after);o+=after;
  buf[o]=0;
  return(long)buf;
}
/* BYTES_JOIN_V1: `sep.join(list_of_bytes)` como en CPython. Cada elemento
   debe ser bytes (PK_BYTES con .data/.length en PitonBytes). Devuelve un
   PitonBytes* con los datos concatenados. */
static long piton_bytes_join(void*sep_obj,void*raw){
  PitonBytes*sep=(PitonBytes*)sep_obj;
  PitonSeq*items=(PitonSeq*)raw;
  if(!items){piton_raise_set("TypeError","can only join an iterable");piton_exit(1);return 0;}
  usize sl=sep->length;
  usize total=0;long cnt=items->length;
  for(long i=0;i<cnt;++i){
    PitonSlot v=items->items[i];
    if(v.kind!=PK_BYTES){
      piton_raise_set("TypeError","sequence item is not a bytes");piton_exit(1);return 0;
    }
    PitonBytes*b=(PitonBytes*)v.bits;
    total+=b->length;
  }
  total+=sl*(usize)(cnt>0?cnt-1:0);
  unsigned char*out=(unsigned char*)piton_alloc(total+1);usize o=0;
  for(long i=0;i<cnt;++i){
    if(i){piton_memcpy(out+o,sep->data,sl);o+=sl;}
    PitonBytes*b=(PitonBytes*)items->items[i].bits;
    piton_memcpy(out+o,b->data,b->length);o+=b->length;
  }
  out[o]=0;
  PitonBytes*result=(PitonBytes*)piton_alloc(sizeof(PitonBytes));
  result->refcount=1;result->kind=PK_BYTES;result->length=total;result->data=out;
  return(long)result;
}
/* CHR_NUL_V1: encoded length of a chr() result. strlen stops at NUL, so
 * `chr(0)` measured 0. A chr() result is always exactly one character; the
 * leading byte determines its UTF-8 width, and a leading NUL means chr(0)
 * itself (width 1). */
static long piton_chr_len(const char*s){unsigned char c=(unsigned char)s[0];
  if(c==0)return 1;if(c<0x80)return 1;if(c<0xE0)return 2;if(c<0xF0)return 3;return 4;}
static long piton_str_index(const char*s,long i){long n=(long)piton_strlen(s);if(i<0)i+=n;if(i<0||i>=n){/* GETITEM_RAISE_V1 */piton_raise_set("IndexError","string index out of range");return 0;}char*p=piton_alloc(2);p[0]=s[i];p[1]=0;return(long)p;}
static long piton_str_apply_slice(const char*s,PitonSlice*sl){
  if(!sl)return 0;
  long n=(long)piton_strlen(s);
  long step=sl->has_step?sl->step:1;
  if(step==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}
  long start,stop;
  if(step>0){start=sl->has_start?sl->start:0;stop=sl->has_stop?sl->stop:n;}
  else{start=sl->has_start?sl->start:n-1;stop=sl->has_stop?sl->stop:-1;}
  if(step>0){
    if(start<0)start+=n;if(stop<0)stop+=n;
    if(start<0)start=0;if(start>n)start=n;
    if(stop<0)stop=0;if(stop>n)stop=n;
  }else{
    if(sl->has_start&&start<0)start+=n;
    if(!sl->has_stop)stop=-1;
    if(!sl->has_start)start=n-1;
    if(start<-1)start=-1;if(start>=n)start=n-1;
    if(stop<-1)stop=-1;
  }
  long count=0;
  if(step>0){for(long i=start;i<stop;i+=step)++count;}
  else{for(long i=start;i>stop;i+=step)++count;}
  if(count<0)count=0;
  char*p=piton_alloc((usize)count+1);
  long k=0;
  if(step>0){for(long i=start;i<stop&&k<count;i+=step)p[k++]=s[i];}
  else{for(long i=start;i>stop&&k<count;i+=step)p[k++]=s[i];}
  p[k]=0;
  return(long)p;
}
static long piton_str_slice(const char*s,long lo,long hi){long n=(long)piton_strlen(s);if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;char*p=piton_alloc((usize)(hi-lo)+1);for(long i=0;i<hi-lo;++i)p[i]=s[lo+i];p[hi-lo]=0;return(long)p;}
/* STR_GET_ITEM_V1: s[i] -> str de UN caracter (CPython). Sin esto el
   subscript de un str caia al fallback generico (que lo leia como
   PitonSeq*) -> SIGSEGV. Indice fuera de rango -> IndexError. */
static long piton_str_get(const char*s,long i){long n=(long)piton_strlen(s);if(i<0)i+=n;if(i<0||i>=n){piton_raise_set("IndexError","string index out of range");return 0;}char*p=piton_alloc(2);p[0]=s[i];p[1]=0;return(long)p;}
static PitonSlot piton_seq_pop(PitonSeq*s,long i){if(!s||s->length<=0){piton_write(2,"IndexError: pop from empty list\n",32);piton_exit(1);}if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError: pop index out of range\n",35);piton_exit(1);}PitonSlot v=s->items[i];for(long j=i;j+1<s->length;++j)s->items[j]=s->items[j+1];--s->length;return v;}
/* DEL_SLICE_V1: borrar el rango [lo,hi) de una secuencia (CPython
   del l[a:b]); bounds ausentes = 0 / longitud. datastructures: del a[2:4],
   del a[:]; imghdr: del sys.argv[1:2]. */
static void piton_seq_del_range(PitonSeq*s,long lo,long hi){
  if(!s)return;
  long n=s->length;
  if(lo<0)lo+=n;if(hi<0)hi+=n;
  if(lo<0)lo=0;if(hi>n)hi=n;
  if(hi<=lo)return;
  long span=hi-lo;
  for(long i=lo;i+span<n;++i)s->items[i]=s->items[i+span];
  s->length-=span;
}
static void piton_seq_reverse(PitonSeq*s){if(!s)return;for(long i=0,j=s->length-1;i<j;++i,--j){PitonSlot t=s->items[i];s->items[i]=s->items[j];s->items[j]=t;}}
static void piton_seq_insert(PitonSeq*s,long i,PitonSlot v){if(!s)return;if(i<0)i+=s->length;if(i<0)i=0;if(i>s->length)i=s->length;if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*na=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(na,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=na;s->capacity=nc;}for(long j=s->length;j>i;--j)s->items[j]=s->items[j-1];s->items[i]=v;++s->length;}
static long piton_seq_count(PitonSeq*s,PitonSlot v){if(!s)return 0;long n=0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))++n;return n;}
/* MINMAX_ITER_V1: min()/max() of a single iterable. Elements compare by
 * slot kind (int bits, str strcmp, float bits), mirroring the two-argument
 * MINMAX_TYPES_V1 rule; a mixed element pair raises TypeError like CPython,
 * and an empty sequence raises ValueError. The flag is set only on error.
 */
static int piton_slot_less(PitonSlot a,PitonSlot b){
  if(a.kind!=b.kind){
    /* MINMAX_ITER_V1: numeric promotion (int/bool/float) like CPython . */
    int an=(a.kind==PK_INT||a.kind==PK_BOOL||a.kind==PK_FLOAT);
    int bn=(b.kind==PK_INT||b.kind==PK_BOOL||b.kind==PK_FLOAT);
    if(an&&bn){double da=a.kind==PK_FLOAT?piton_bits_double(a.bits):(double)a.bits;double db=b.kind==PK_FLOAT?piton_bits_double(b.bits):(double)b.bits;return da<db?1:0;}
    return -1;}
  if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)<0;
  if(a.kind==PK_INT||a.kind==PK_BOOL)return a.bits<b.bits;
  if(a.kind==PK_FLOAT)return piton_bits_double(a.bits)<piton_bits_double(b.bits);
  if(a.kind==PK_LIST||a.kind==PK_TUPLE){PitonSeq*sa=(PitonSeq*)a.bits;PitonSeq*sb=(PitonSeq*)b.bits;long n=sa->length<sb->length?sa->length:sb->length;for(long i=0;i<n;++i){int l=piton_slot_less(sa->items[i],sb->items[i]);if(l<0)return -1;if(l)return 1;int g=piton_slot_less(sb->items[i],sa->items[i]);if(g<0)return -1;if(g)return 0;}return sa->length<sb->length?1:0;}
  return -1;}
static PitonSlot piton_seq_minmax(PitonSeq*s,int want_min){if(!s||s->length<=0){piton_raise_set("ValueError","min() arg is an empty sequence");return (PitonSlot){0,PK_INT};}
  PitonSlot best=s->items[0];
  for(long i=1;i<s->length;++i){PitonSlot v=s->items[i];int less=piton_slot_less(v,best);
    if(less<0){piton_raise_set("TypeError","'<>' not supported between instances");return best;}
    if(want_min?less:!less){int eq;if(v.kind==PK_STR)eq=piton_strcmp((const char*)v.bits,(const char*)best.bits)==0;
      else if(v.kind==PK_FLOAT)eq=piton_bits_double(v.bits)==piton_bits_double(best.bits);
      else eq=v.bits==best.bits;
      if(!eq)best=v;}}
  return best;}

static void piton_seq_sort(PitonSeq*s){if(!s||s->length<2)return;int allint=1,allstr=1;for(long i=0;i<s->length;++i){if(s->items[i].kind!=PK_INT&&s->items[i].kind!=PK_BOOL)allint=0;if(s->items[i].kind!=PK_STR)allstr=0;}if(!allint&&!allstr){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}for(long i=1;i<s->length;++i){PitonSlot k=s->items[i];long j=i-1;if(allstr&&!allint){while(j>=0&&piton_strcmp((const char*)s->items[j].bits,(const char*)k.bits)>0){s->items[j+1]=s->items[j];--j;}}else{while(j>=0&&s->items[j].bits>k.bits){s->items[j+1]=s->items[j];--j;}}s->items[j+1]=k;}}
static PitonSlot piton_dict_get_1(PitonDict*d,PitonSlot k){if(d)for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k))return d->items[i].value;return (PitonSlot){0,PK_NONE};}
static PitonSlot piton_dict_get_d(PitonDict*d,PitonSlot k,PitonSlot dflt){if(d)for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k))return d->items[i].value;return dflt;}
static long piton_seq_slice_step(PitonSeq*s,long lo,long hi,long st){if(!s)return 0;if(st==0){piton_write(2,"ValueError: slice step cannot be zero\n",38);piton_exit(1);}long n=s->length;long len=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;len=(hi-lo+st-1)/st;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);}PitonSeq*r=piton_seq_new((int)s->kind,len);for(long i=0;i<len;++i)r->items[i]=s->items[lo+i*st];return(long)r;}
static long piton_str_slice_step(const char*s,long lo,long hi,long st){if(st==0){piton_write(2,"ValueError: slice step cannot be zero\n",38);piton_exit(1);}long n=(long)piton_strlen(s);long len=0;long a=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;len=(hi-lo+st-1)/st;a=lo;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);a=lo;}char*p=piton_alloc((usize)len+1);for(long i=0;i<len;++i)p[i]=s[a+i*st];p[len]=0;return(long)p;}
static long piton_seq_slice(PitonSeq*s,long lo,long hi){if(!s)return 0;long n=s->length;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;PitonSeq*r=piton_seq_new((int)s->kind,hi-lo);for(long i=0;i<hi-lo;++i)r->items[i]=s->items[lo+i];return(long)r;}
static void piton_seq_sort_rev(PitonSeq*s){if(!s||s->length<2)return;int allint=1,allstr=1;for(long i=0;i<s->length;++i){if(s->items[i].kind!=PK_INT&&s->items[i].kind!=PK_BOOL)allint=0;if(s->items[i].kind!=PK_STR)allstr=0;}if(!allint&&!allstr){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}for(long i=1;i<s->length;++i){PitonSlot k=s->items[i];long j=i-1;if(allstr&&!allint){while(j>=0&&piton_strcmp((const char*)s->items[j].bits,(const char*)k.bits)<0){s->items[j+1]=s->items[j];--j;}}else{while(j>=0&&s->items[j].bits<k.bits){s->items[j+1]=s->items[j];--j;}}s->items[j+1]=k;}}
static PitonSlot piton_seq_get(PitonSeq*s,long i){if(i<0)i+=s->length;if(i<0||i>=s->length){/* GETITEM_RAISE_V1 */piton_raise_set("IndexError","list index out of range");return (PitonSlot){0,PK_NONE};}return s->items[i];}
static long piton_iterator_new(PitonSeq*s){if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->index=0;return(long)i;}
static long piton_iterator_next(long raw){PitonIterator*i=(PitonIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->seq->length){piton_write(2,"StopIteration\n",14);piton_exit(1);}return i->seq->items[i->index++].bits;}
static long piton_genexpr_new(PitonSeq*s){if(!s){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}PitonGenExpr*g=piton_alloc(sizeof(*g));g->source=s;g->index=0;return(long)g;}
static long piton_genexpr_iter(PitonGenExpr*g){return(long)g;}
static long piton_genexpr_to_list(long raw){PitonGenExpr*g=(PitonGenExpr*)raw;if(!g||!g->source){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}return(long)g->source;}
static long piton_genexpr_next(PitonGenExpr*g){if(!g||!g->source){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}if(g->index>=g->source->length){piton_raise_set("StopIteration","");return 0;}return g->source->items[g->index++].bits;}
static PitonDict*piton_dict_new(long n){PitonDict*d=piton_alloc(sizeof(*d));d->refcount=1;d->kind=PK_DICT;d->length=n;d->capacity=n;d->items=n>0?piton_alloc((usize)n*sizeof(PitonDictEntry)):0;return d;}
static void piton_dict_put(PitonDict*d,long i,PitonSlot k,PitonSlot v){if(i>=0&&i<d->length){d->items[i].key=k;d->items[i].value=v;}}
static void piton_dict_append(PitonDict*d,PitonSlot k,PitonSlot v){if(d->length>=d->capacity){long nc=d->capacity?d->capacity*2:4;PitonDictEntry*ni=piton_alloc((usize)nc*sizeof(PitonDictEntry));if(d->items)piton_memcpy(ni,d->items,(usize)d->capacity*sizeof(PitonDictEntry));d->items=ni;d->capacity=nc;}d->items[d->length].key=k;d->items[d->length].value=v;++d->length;}
static PitonSlot piton_dict_get(PitonDict*d,PitonSlot key){for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,key))return d->items[i].value;/* GETITEM_RAISE_V1: antes exit(1) directo — ahora se levanta la excepcion para que el caller la rute a su handler o la reporte. */piton_raise_set("KeyError","");return (PitonSlot){0,PK_NONE};}
static int piton_unpack_seq4(PitonSlot obj,long capacity,long*out4){if(obj.kind!=PK_LIST&&obj.kind!=PK_TUPLE){piton_raise_set("TypeError","argument after * must be a list or tuple");return -1;}PitonSeq*s=(PitonSeq*)obj.bits;if(s->length>capacity){piton_raise_set("TypeError","too many positional arguments for call");return -1;}for(long i=0;i<s->length;++i)out4[i]=s->items[i].bits;return s->length;}
static int piton_dict_unpack4(PitonSlot obj,const char**names,long count,long*out4,long*mask){if(obj.kind!=PK_DICT){piton_raise_set("TypeError","argument after ** must be a dict");return -1;}PitonDict*d=(PitonDict*)obj.bits;for(long i=0;i<d->length;++i){PitonSlot k=d->items[i].key;if(k.kind!=PK_STR){piton_raise_set("TypeError","keywords must be strings");return -1;}const char*key=(const char*)k.bits;long matched=-1;for(long j=0;j<count;++j)if(piton_strcmp(key,names[j])==0){matched=j;break;}if(matched<0){piton_raise_set("TypeError","unexpected keyword argument in ** expansion");return -1;}if(*mask&(1LL<<matched)){piton_raise_set("TypeError","multiple values for argument");return -1;}*mask|=1LL<<matched;out4[matched]=d->items[i].value.bits;}return 0;}
static PitonSet*piton_set_new(long cap){PitonSet*s=piton_alloc(sizeof(*s));s->refcount=1;s->kind=PK_SET;s->capacity=cap;s->items=cap>0?piton_alloc((usize)cap*sizeof(PitonSlot)):0;return s;}
static void piton_set_add(PitonSet*s,PitonSlot v);
/* FROZENSET_V1: constructor y operaciones. Reutiliza PitonSet (mismo layout)
 * con kind PK_FROZENSET; el print/eq/truthy ya lo tratan como conjunto. */
static long piton_frozenset_new_empty(void){PitonSet*s=piton_set_new(0);s->kind=PK_FROZENSET;return(long)s;}
static long piton_frozenset_from_seq(long raw,int kind){
  PitonSet*r=piton_set_new(8);r->kind=PK_FROZENSET;
  if(!raw)return(long)r;
  if(kind==PK_LIST||kind==PK_TUPLE){PitonSeq*sq=(PitonSeq*)raw;for(long i=0;i<sq->length;++i)piton_set_add(r,sq->items[i]);}
  else if(kind==PK_SET||kind==PK_FROZENSET){PitonSet*sq=(PitonSet*)raw;for(long i=0;i<sq->length;++i)piton_set_add(r,sq->items[i]);}
  else if(kind==PK_DICT){PitonDict*d=(PitonDict*)raw;for(long i=0;i<d->length;++i)piton_set_add(r,d->items[i].key);}
  else if(kind==PK_RANGE){PitonRange*rg=(PitonRange*)raw;long n=piton_range_len(rg);for(long i=0;i<n;++i)piton_set_add(r,(PitonSlot){rg->start+i*rg->step,PK_INT});}
  else if(kind==PK_STR){const char*sp=(const char*)raw;for(usize i=0;sp&&sp[i];){long cp;int n;unsigned char c=(unsigned char)sp[i];if(c<0x80){cp=c;n=1;}else if((c&0xE0)==0xC0){cp=c&0x1F;n=2;}else if((c&0xF0)==0xE0){cp=c&0x0F;n=3;}else{cp=c&0x07;n=4;}for(int k=1;k<n;++k)cp=(cp<<6)|((unsigned char)sp[i+k]&0x3F);char*p=piton_alloc((usize)n+1);piton_memcpy(p,sp+i,(usize)n);p[n]=0;piton_set_add(r,(PitonSlot){(long)p,PK_STR});i+=(usize)n;}}
  else{piton_raise_set("TypeError","'frozenset' object is not iterable");return 0;}
  return(long)r;
}
/* RANGE_VALUE_V1: `rango(...)` as a VALUE. Iteration of a bare `rango(...)` call
 * already works because the `for` header consumes the call's arguments directly
 * (counter loop), so making the call produce an object changes nothing there.
 * A range is a tiny refcounted struct; laziness is preserved everywhere (the
 * elements are computed from start/stop/step, never materialized). */
static long piton_range_len(PitonRange*r){if(!r)return 0;long start=r->start,stop=r->stop,step=r->step;
  if(step>0){if(stop<=start)return 0;return (stop-start+(step-1))/step;}
  if(stop>=start)return 0;return (start-stop+(-step-1))/(-step);}
static PitonRange*piton_range_new(long start,long stop,long step){
  if(step==0){piton_raise_set("ValueError","range() arg 3 must not be zero");return 0;}
  PitonRange*r=piton_alloc(sizeof(*r));r->refcount=1;r->kind=PK_RANGE;
  r->start=start;r->stop=stop;r->step=step;return r;}
static PitonBytes* piton_bytes_new(long len,const unsigned char*data);
static long piton_strdup(const char*s);
static long piton_memoryview_new(void*buf,int is_bytearray,long off,long len,long stride);
static void piton_print_memoryview(PitonMemoryView*m);
static long piton_slice_new(long start,long stop,long step,int hs,int hp,int ht);
static void piton_print_slice(PitonSlice*x);
static int piton_slice_eq(PitonSlice*a,PitonSlice*b);
static long piton_range_get(PitonRange*r,long i){long n=piton_range_len(r);
  if(i<0)i+=n;if(i<0||i>=n){piton_write(2,"IndexError\n",11);piton_exit(1);}
  return r->start+i*r->step;}

/* SLICE_VALUE_V1: slice(a,b,c) con bounds ausentes marcados (CPython usa None). */
static long piton_strdup(const char*s){usize n=piton_strlen(s);char*p=piton_alloc(n+1);piton_memcpy(p,s,n+1);return(long)p;}
/* FS_ENCODE_V1: os.fsencode(str) -> bytes (UTF-8 copy of the str). */
static PitonBytes* piton_str_to_bytes(const char*s){usize n=piton_strlen(s);PitonBytes*b=(PitonBytes*)piton_alloc(sizeof(PitonBytes));b->refcount=1;b->kind=PK_BYTES;b->length=(long)n;b->data=(unsigned char*)piton_alloc(n+1);piton_memcpy(b->data,s,n+1);return b;}
/* MEMORYVIEW_V1: constructores y operaciones sobre la vista. */
static unsigned char* piton_mv_buf(PitonMemoryView*m){
  if(m->is_bytearray)return ((PitonByteArray*)m->buf)->data;
  return ((PitonBytes*)m->buf)->data;
}
static long piton_memoryview_new(void*buf,int is_bytearray,long off,long len,long stride){
  if(!buf){piton_raise_set("TypeError","memoryview: a bytes-like object is required");return 0;}
  PitonMemoryView*m=(PitonMemoryView*)piton_alloc(sizeof(PitonMemoryView));
  m->refcount=1;m->kind=PK_MEMORYVIEW;
  m->buf=buf;m->is_bytearray=is_bytearray;m->offset=off;m->length=len;m->stride=stride;
  return(long)m;
}
static long piton_memoryview_get(PitonMemoryView*m,long i){
  if(i<0)i+=m->length;
  if(i<0||i>=m->length){piton_raise_set("IndexError","index out of bounds on memoryview");return 0;}
  return (long)piton_mv_buf(m)[m->offset+i*m->stride];
}
static void piton_memoryview_set(PitonMemoryView*m,long i,long v){
  if(!m->is_bytearray){piton_raise_set("TypeError","cannot modify read-only memoryview");return;}
  if(i<0)i+=m->length;
  if(i<0||i>=m->length){piton_raise_set("IndexError","index out of bounds on memoryview");return;}
  if(v<0||v>255){piton_raise_set("ValueError","memoryview assignment value out of range");return;}
  piton_mv_buf(m)[m->offset+i*m->stride]=(unsigned char)v;
}
static int piton_memoryview_eq(PitonMemoryView*a,PitonMemoryView*b){
  if(a->length!=b->length)return 0;
  unsigned char*pa=piton_mv_buf(a);unsigned char*pb=piton_mv_buf(b);
  for(long i=0;i<a->length;++i)if(pa[a->offset+i*a->stride]!=pb[b->offset+i*b->stride])return 0;
  return 1;
}
static void piton_print_memoryview(PitonMemoryView*m){
  piton_write(piton_print_fd,"<memory at ",11);
  /* dirección estable dentro del buffer: se imprime el offset relativo */
  piton_write_int(m->offset);
  piton_write(piton_print_fd,">",1);
}
static long piton_memoryview_tobytes(PitonMemoryView*m){
  if(m->stride==1)return(long)(PitonBytes*)piton_bytes_new(m->length,piton_mv_buf(m)+m->offset);
  /* MEMORYVIEW_STRIDE_V1: vista no contigua -> coleccion en orden de items. */
  char*p=piton_alloc((usize)(m->length>0?m->length:1));
  unsigned char*b=piton_mv_buf(m);
  for(long i=0;i<m->length;++i)p[i]=(char)b[m->offset+i*m->stride];
  return(long)(PitonBytes*)piton_bytes_new(m->length,p);
}
static long piton_memoryview_tolist(PitonMemoryView*m){
  PitonSeq*r=piton_seq_new(PK_LIST,m->length);
  unsigned char*p=piton_mv_buf(m);
  for(long i=0;i<m->length;++i)r->items[i]=(PitonSlot){(long)p[m->offset+i*m->stride],PK_INT};
  return(long)r;
}
typedef struct{long magic;PitonMemoryView*mv;long index;}PitonMemoryViewIterator;
static long piton_memoryview_iter_new(PitonMemoryView*m){
  PitonMemoryViewIterator*i=piton_alloc(sizeof(*i));
  i->magic=0x5049544E4D564557LL;i->mv=m;i->index=0;
  return(long)i;
}
static long piton_memoryview_iter_next(long raw){
  PitonMemoryViewIterator*i=(PitonMemoryViewIterator*)raw;
  if(!i||i->magic!=0x5049544E4D564557LL){piton_write(2,"TypeError: object is not a memoryview iterator\n",49);piton_exit(1);}
  if(i->index>=i->mv->length){piton_raise_set("StopIteration","");return 0;}
  return (long)piton_mv_buf(i->mv)[i->mv->offset+(i->index++)*i->mv->stride];
}
static long piton_memoryview_slice(PitonMemoryView*m,long lo,long hi){
  long n=m->length;if(lo<0)lo+=n;if(hi<0)hi+=n;
  if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;
  return piton_memoryview_new(m->buf,m->is_bytearray,m->offset+lo*m->stride,hi-lo,m->stride);
}
/* MEMORYVIEW_STRIDE_V1: m[lo:hi:step] -> VISTA con stride (positivo o
 * negativo), sin copia: sigue compartiendo el buffer y refleja mutaciones
 * como en CPython. Los bounds ausentes llegan como los sentinels INT64_MIN /
 * INT64_MAX del emisor (el primer test cubre el literal y su wrap -1). */
static long piton_memoryview_slice_step(PitonMemoryView*m,long lo,long hi,long step){
  long n=m->length;
  if(step==0){piton_write(2,"ValueError: slice step cannot be zero\n",38);piton_exit(1);}
  long start,stop,count;
  if(step>0){
    if(lo==(-0x7FFFFFFFFFFFFFFFL)||lo==(-0x7FFFFFFFFFFFFFFFL-1))start=0;
    else if(lo<0){start=lo+n;if(start<0)start=0;}
    else if(lo>n)start=n;
    else start=lo;
    if(hi==0x7FFFFFFFFFFFFFFFL)stop=n;
    else if(hi<0){stop=hi+n;if(stop<0)stop=0;}
    else if(hi>n)stop=n;
    else stop=hi;
    if(stop<start)stop=start;
    count=(stop-start+step-1)/step;
  }else{
    if(lo==(-0x7FFFFFFFFFFFFFFFL)||lo==(-0x7FFFFFFFFFFFFFFFL-1))start=n-1;
    else if(lo<0){start=lo+n;if(start<0)start=-1;}
    else if(lo>=n)start=n-1;
    else start=lo;
    if(hi==0x7FFFFFFFFFFFFFFFL)stop=-1;
    else if(hi<0){stop=hi+n;if(stop<0)stop=-1;}
    else if(hi>=n)stop=n-1;
    else stop=hi;
    if(stop>start)stop=start;
    count=(start-stop-step-1)/(-step);
  }
  if(count<0)count=0;
  return piton_memoryview_new(m->buf,m->is_bytearray,
     count?m->offset+start*m->stride:m->offset,count,m->stride*step);
}
static long piton_slice_new(long start,long stop,long step,int hs,int hp,int ht){
  PitonSlice*x=(PitonSlice*)piton_alloc(sizeof(PitonSlice));
  x->refcount=1;x->kind=PK_SLICE;
  x->start=start;x->stop=stop;x->step=step;
  x->has_start=hs;x->has_stop=hp;x->has_step=ht;
  return(long)x;
}
static void piton_print_slice(PitonSlice*x){
  piton_write(piton_print_fd,"slice(",6);
  if(x->has_start)piton_write_int(x->start);else piton_write(piton_print_fd,"None",4);
  piton_write(piton_print_fd,", ",2);
  if(x->has_stop)piton_write_int(x->stop);else piton_write(piton_print_fd,"None",4);
  piton_write(piton_print_fd,", ",2);
  if(x->has_step)piton_write_int(x->step);else piton_write(piton_print_fd,"None",4);
  piton_write(piton_print_fd,")",1);
}
/* SLICE_APPLY_V1: aplicar un objeto slice a una secuencia (copia). */
static PitonSeq* piton_seq_apply_slice(PitonSeq*src,PitonSlice*sl){
  if(!sl)return 0;
  long n=src->length;
  long step=sl->has_step?sl->step:1;
  if(step==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}
  long start,stop;
  if(step>0){
    start=sl->has_start?sl->start:0;
    stop=sl->has_stop?sl->stop:n;
  }else{
    start=sl->has_start?sl->start:n-1;
    stop=sl->has_stop?sl->stop:-1;
  }
  /* normalización: en paso negativo, -1 es el centinela "antes del inicio";
   * solo se ajustan los negativos que NO son el centinela. */
  if(step>0){
    if(start<0)start+=n;if(stop<0)stop+=n;
    if(start<0)start=0;if(start>n)start=n;
    if(stop<0)stop=0;if(stop>n)stop=n;
  }else{
    if(sl->has_start&&start<0)start+=n;
    if(sl->has_stop&&stop>=0&&stop<0)stop+=n;
    if(!sl->has_stop)stop=-1;
    if(!sl->has_start)start=n-1;
    if(start<-1)start=-1;if(start>=n)start=n-1;
    if(stop<-1)stop=-1;
  }
  long count=0;
  if(step>0){for(long i=start;i<stop;i+=step)++count;}
  else{for(long i=start;i>stop;i+=step)++count;}
  if(count<0)count=0;
  PitonSeq*r=piton_seq_new(src->kind,count);
  long k=0;
  if(step>0){for(long i=start;i<stop&&k<count;i+=step)r->items[k++]=src->items[i];}
  else{for(long i=start;i>stop&&k<count;i+=step)r->items[k++]=src->items[i];}
  return r;
}
/* CONV_NONE_V1: lista(none). Propaga la excepcion pendiente o levanta
 * TypeError como CPython. */
static int piton_exc_flag_get(void);
static long piton_list_from_none(void){
  if(piton_exc_flag_get())return 0;
  piton_raise_set("TypeError","'NoneType' object is not iterable");
  return 0;
}
static void piton_dict_set(PitonDict*d,PitonSlot k,PitonSlot v);
static long piton_dict_fromkeys(PitonSeq*keys,int has_val,int val_kind,long val){
  PitonDict*d=piton_dict_new(keys->length);
  d->length=0;
  for(long i=0;i<keys->length;++i){
    PitonSlot v;v.bits=val;v.kind=val_kind;
    piton_dict_set(d,keys->items[i],has_val?v:(PitonSlot){0,PK_NONE});
  }
  return(long)d;
}
/* DICT_FROMKEYS_STR_V1: dict.fromkeys(<str>) — CPython acepta cualquier
 * iterable; el str es el caso real (shlex: dict.fromkeys(punctuation_chars)).
 * Cada byte es una clave de un caracter, igual que en CPython. El no-ASCII
 * falla en tiempo de ejecucion: iterar bytes produciria claves distintas de
 * las de CPython y eso es divergir en silencio. */
static long piton_dict_fromkeys_str(const char*s,int has_val,int val_kind,long val){
  usize n=piton_strlen(s);
  PitonDict*d=piton_dict_new((long)n);
  d->length=0;
  for(usize i=0;i<n;++i){
    unsigned char c=(unsigned char)s[i];
    if(c>=0x80){piton_write(2,"ValueError: dict.fromkeys() with non-ASCII str keys is not supported in the native subset\n",90);piton_exit(1);}
    char*k=piton_alloc(2);k[0]=(char)c;k[1]=0;
    PitonSlot v;v.bits=val;v.kind=val_kind;
    piton_dict_set(d,piton_slot((long)k,PK_STR),has_val?v:(PitonSlot){0,PK_NONE});
  }
  return(long)d;
}
/* SLICE_INDICES_V1: (start, stop, step) normalizados a la longitud n. */
static long piton_slice_indices(PitonSlice*sl,long n){
  long step=sl->has_step?sl->step:1;
  if(step==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}
  long start,stop;
  if(step>0){
    start=sl->has_start?sl->start:0;
    stop=sl->has_stop?sl->stop:n;
    if(start<0)start+=n;if(stop<0)stop+=n;
    if(start<0)start=0;if(start>n)start=n;
    if(stop<0)stop=0;if(stop>n)stop=n;
    if(stop<start)stop=start;
  }else{
    start=sl->has_start?sl->start:n-1;
    stop=sl->has_stop?sl->stop:-1;
    if(sl->has_start&&start<0)start+=n;
    if(sl->has_stop&&stop>=0)stop+=n;
    if(start<0)start=-1;if(start>=n)start=n-1;
    if(stop<-1)stop=-1;
    if(stop>start)stop=start;
  }
  PitonSeq*r=piton_seq_new(PK_TUPLE,3);
  r->items[0]=(PitonSlot){start,PK_INT};
  r->items[1]=(PitonSlot){stop,PK_INT};
  r->items[2]=(PitonSlot){step,PK_INT};
  return(long)r;
}
static int piton_slice_eq(PitonSlice*a,PitonSlice*b){
  return a->has_start==b->has_start&&a->has_stop==b->has_stop&&a->has_step==b->has_step&&
         (!a->has_start||a->start==b->start)&&(!a->has_stop||a->stop==b->stop)&&(!a->has_step||a->step==b->step);
}
static long piton_range_contains(PitonRange*r,long v){if(!r)return 0;
  long d=v-r->start;if(r->step>0){if(v<r->start||v>=r->stop)return 0;}else{if(v>r->start||v<=r->stop)return 0;}
  return d%r->step==0;}
static long piton_range_eq(PitonRange*a,PitonRange*b){if(!a||!b)return a==b;
  long na=piton_range_len(a),nb=piton_range_len(b);if(na!=nb)return 0;
  if(na==0)return 1;return a->start==b->start&&a->step==b->step;}
static void piton_range_print(PitonRange*r){if(!r){piton_write(piton_print_fd,"range(0, 0)",11);return;}
  piton_write(piton_print_fd,"range(",6);piton_write_int(r->start);piton_write(piton_print_fd,", ",2);
  piton_write_int(r->stop);
  if(r->step!=1){piton_write(piton_print_fd,", ",2);piton_write_int(r->step);}
  piton_write(piton_print_fd,")",1);}
static long piton_sum_range(PitonRange*r){long total=0;long n=piton_range_len(r);
  for(long i=0;i<n;++i)total+=r->start+i*r->step;return total;}
static void piton_set_add(PitonSet*s,PitonSlot v){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return;if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*ni=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(ni,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=ni;s->capacity=nc;}s->items[s->length++]=v;}
/* CONV_BUILTINS_V1: the conversion builtins as VALUES. They used to be
 * rejected everywhere except directly under a `for` header, failing 19 corpus
 * cases for no reason. Each mirrors CPython; a shallow copy shares element
 * refcounts, following the existing convention. */
static PitonSeq*piton_conv_seq(int kind,PitonSlot src){
  PitonSeq*r=piton_seq_new(kind,0);
  if(src.kind==PK_STR){const char*s=(const char*)src.bits;usize n=piton_strlen(s);
    for(usize i=0;i<n;++i){char*p=piton_alloc(2);p[0]=s[i];p[1]=0;piton_slot_incref((PitonSlot){(long)p,PK_STR});piton_seq_append(r,(PitonSlot){(long)p,PK_STR});}return r;}
  if(src.kind==PK_LIST||src.kind==PK_TUPLE){PitonSeq*a=(PitonSeq*)src.bits;
    for(long i=0;i<a->length;++i){piton_slot_incref(a->items[i]);piton_seq_append(r,a->items[i]);}return r;}
  if(src.kind==PK_SET){PitonSet*a=(PitonSet*)src.bits;
    for(long i=0;i<a->length;++i){piton_slot_incref(a->items[i]);piton_seq_append(r,a->items[i]);}return r;}
  if(src.kind==PK_DICT){PitonDict*a=(PitonDict*)src.bits;
    for(long i=0;i<a->length;++i){piton_slot_incref(a->items[i].key);piton_seq_append(r,a->items[i].key);}return r;}
  if(src.kind==PK_RANGE){PitonRange*g=(PitonRange*)src.bits;long n=piton_range_len(g);
    for(long i=0;i<n;++i)piton_seq_append(r,(PitonSlot){g->start+i*g->step,PK_INT});return r;}
  piton_write(2,"TypeError: cannot convert to a sequence\n",35);piton_exit(1);return r;}
static PitonSet*piton_conv_set(PitonSlot src){
  PitonSet*r=piton_set_new(0);
  if(src.kind==PK_SET){PitonSet*a=(PitonSet*)src.bits;
    for(long i=0;i<a->length;++i){piton_slot_incref(a->items[i]);piton_set_add(r,a->items[i]);}return r;}
  if(src.kind==PK_STR){const char*s=(const char*)src.bits;usize n=piton_strlen(s);
    for(usize i=0;i<n;++i){char*p=piton_alloc(2);p[0]=s[i];p[1]=0;piton_set_add(r,(PitonSlot){(long)p,PK_STR});}return r;}
  if(src.kind==PK_LIST||src.kind==PK_TUPLE){PitonSeq*a=(PitonSeq*)src.bits;
    for(long i=0;i<a->length;++i){piton_slot_incref(a->items[i]);piton_set_add(r,a->items[i]);}return r;}
  if(src.kind==PK_RANGE){PitonRange*g=(PitonRange*)src.bits;long n=piton_range_len(g);
    for(long i=0;i<n;++i)piton_set_add(r,(PitonSlot){g->start+i*g->step,PK_INT});return r;}
  piton_write(2,"TypeError: cannot convert to a set\n",31);piton_exit(1);return r;}
static PitonDict*piton_conv_dict(PitonSlot src){
  if(src.kind!=PK_LIST&&src.kind!=PK_TUPLE){
    piton_write(2,"TypeError: cannot convert to a dict\n",33);piton_exit(1);}
  PitonSeq*a=(PitonSeq*)src.bits;PitonDict*r=piton_dict_new(a->length);
  for(long i=0;i<a->length;++i){
    if(a->items[i].kind!=PK_LIST&&a->items[i].kind!=PK_TUPLE){
      piton_write(2,"ValueError: dictionary update sequence element is not a pair\n",63);piton_exit(1);}
    PitonSeq*pair=(PitonSeq*)a->items[i].bits;
    if(pair->length!=2){piton_write(2,"ValueError: dictionary update sequence element has wrong length\n",67);piton_exit(1);}
    piton_dict_put(r,i,pair->items[0],pair->items[1]);}
   return r;}
/* DICT_FROM_PAIRS_V1: dict(genexpr de pares) — el genexpr del backend ya
   esta materializado como una secuencia de tuplas, asi que se convierte con
   el mismo helper que dict(lista_de_pares). */
static long piton_conv_dict_from_genexpr(long raw){PitonGenExpr*g=(PitonGenExpr*)raw;if(!g||!g->source){piton_write(2,"TypeError: cannot convert to a dict\n",33);piton_exit(1);}return(long)piton_conv_dict((PitonSlot){(long)g->source,PK_LIST});}
typedef struct{long magic;long kind;void*raw;long index;}PitonAnyIterator;
static long piton_iterator_new_any(void*raw,long kind){if(!raw||(kind!=PK_LIST&&kind!=PK_TUPLE&&kind!=PK_DICT&&kind!=PK_SET&&kind!=PK_FROZENSET&&kind!=PK_RANGE)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonAnyIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->raw=raw;i->index=0;i->kind=kind;return(long)i;}
static long piton_iterator_next_any(long raw){PitonAnyIterator*i=(PitonAnyIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}long n=0;if(i->kind==PK_LIST||i->kind==PK_TUPLE)n=((PitonSeq*)i->raw)->length;else if(i->kind==PK_DICT)n=((PitonDict*)i->raw)->length;else if(i->kind==PK_RANGE)n=piton_range_len((PitonRange*)i->raw);else if(i->kind==PK_SET||i->kind==PK_FROZENSET)n=((PitonSet*)i->raw)->length;else{piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}if(i->index>=n){piton_raise_set("StopIteration","");return 0;}PitonSlot v;if(i->kind==PK_LIST||i->kind==PK_TUPLE)v=((PitonSeq*)i->raw)->items[i->index++];else if(i->kind==PK_DICT)v=((PitonDict*)i->raw)->items[i->index++].key;else if(i->kind==PK_RANGE){PitonRange*rr=(PitonRange*)i->raw;v.bits=rr->start+(i->index++)*rr->step;v.kind=PK_INT;}else v=((PitonSet*)i->raw)->items[i->index++];return v.bits;}
typedef struct{long magic;const char*str;long index;long length;}PitonStrIterator;
static long piton_str_iterator_new(const char*str){if(!str){piton_write(2,"TypeError: 'NoneType' object is not iterable\n",42);piton_exit(1);}PitonStrIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E53545249LL;i->str=str;i->index=0;i->length=piton_strlen(str);return(long)i;}
static long piton_str_iterator_next(long raw){PitonStrIterator*i=(PitonStrIterator*)raw;if(!i||i->magic!=0x5049544E53545249LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->length){piton_raise_set("StopIteration","");return 0;}unsigned char c=i->str[i->index++];char*p=piton_alloc(2);p[0]=(char)c;p[1]=0;return(long)p;}
typedef struct{long magic;long index;long start;PitonSeq*seq;}PitonEnumerateIterator;
static long piton_enumerate_new(PitonSlot src,long start){
    long raw=src.bits;
    PitonSeq*s=0;
    if(piton_maybe_ptr(raw)){
        long*ptr=(long*)raw;long k=ptr[1];
        if(k==PK_STR){
            /* ENUM_STR_V1: enumerate() sobre un str explota 1-char */
            const char*txt=(const char*)raw;usize n=piton_strlen(txt);PitonSeq*l=piton_seq_new(PK_LIST,0);
            for(usize i=0;i<n;++i){char*q=piton_alloc(2);q[0]=txt[i];q[1]=0;piton_seq_append(l,(PitonSlot){(long)q,PK_STR});}s=l;
        }else if(k==PK_SET){
            /* ENUM_SET_V1: enumerate(set) convierte a lista primero. */
            PitonSet*st=(PitonSet*)raw;PitonSeq*lst=piton_seq_new(PK_LIST,0);
            for(long i=0;i<st->length;++i)piton_seq_append(lst,st->items[i]);s=lst;
        }else if(k==PK_LIST||k==PK_TUPLE){
            s=(PitonSeq*)raw;
        }
    }
    if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){
        piton_write(2,"TypeError: enumerate() argument is not iterable\n",48);piton_exit(1);
    }
    PitonEnumerateIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->start=start;return(long)i;
}
static long piton_enumerate_next(long raw){PitonEnumerateIterator*i=(PitonEnumerateIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->seq->length){piton_raise_set("StopIteration","");return 0;}long n=i->index++;PitonSeq*p=piton_seq_new(PK_TUPLE,2);piton_seq_put(p,0,(PitonSlot){i->start+n,PK_INT});piton_seq_put(p,1,i->seq->items[n]);return(long)p;}
typedef struct{long magic,index;PitonSeq*seq;}PitonReversedIterator;
typedef struct{long magic,index;PitonSeq*left,*right;}PitonZipIterator;
static long piton_reversed_new(void*raw){PitonSeq*s=(PitonSeq*)raw;if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: reversed() argument is not iterable\n",48);piton_exit(1);}PitonReversedIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->index=s->length-1;return(long)i;}
static long piton_reversed_next(long raw){PitonReversedIterator*i=(PitonReversedIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index<0){piton_raise_set("StopIteration","");return 0;}return i->seq->items[i->index--].bits;}
static long piton_zip_new(void*a,void*b){PitonSeq*l=(PitonSeq*)a,*r=(PitonSeq*)b;if(!l||!r||(l->kind!=PK_LIST&&l->kind!=PK_TUPLE)||(r->kind!=PK_LIST&&r->kind!=PK_TUPLE)){piton_write(2,"TypeError: zip() arguments are not iterable\n",45);piton_exit(1);}PitonZipIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->left=l;i->right=r;return(long)i;}
static long piton_zip_next(long raw){PitonZipIterator*i=(PitonZipIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->left->length||i->index>=i->right->length){piton_raise_set("StopIteration","");return 0;}long n=i->index++;PitonSeq*p=piton_seq_new(PK_TUPLE,2);piton_seq_put(p,0,i->left->items[n]);piton_seq_put(p,1,i->right->items[n]);return(long)p;}
typedef struct{long magic,index;PitonSeq*seq;long(*callback)(long);}PitonCallbackIterator;
static long piton_callback_invoke(long,long);
static long piton_callback_iterator_new(void*raw,long callback,long filter){PitonSeq*s=(PitonSeq*)raw;if(!s||!callback||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: map/filter requires an iterable and unary callback\n",62);piton_exit(1);}PitonCallbackIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL|(filter?1:0);i->seq=s;i->callback=(long(*)(long))callback;return(long)i;}
static long piton_callback_iterator_next(long raw){PitonCallbackIterator*i=(PitonCallbackIterator*)raw;if(!i||((i->magic&~1L)!=0x5049544E17E2LL)){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}while(i->index<i->seq->length){long value=i->seq->items[i->index++].bits;long mapped=piton_callback_invoke((long)i->callback,value);if((i->magic&1)&&!mapped)continue;return(i->magic&1)?value:mapped;}piton_raise_set("StopIteration","");return 0;}
typedef struct{long magic,callback,sentinel;}PitonCallIter;
static long piton_closure_call_frame(long,long,long*);
static long piton_calliter_new(long callback,long sentinel){PitonCallIter*i=piton_alloc(sizeof(*i));i->magic=0x50495443414C4954LL;i->callback=callback;i->sentinel=sentinel;return(long)i;}
static long piton_calliter_next(long raw){PitonCallIter*i=(PitonCallIter*)raw;if(!i||i->magic!=0x50495443414C4954LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}long v=piton_closure_call_frame(i->callback,0,(long*)0);if(v==i->sentinel){piton_raise_set("StopIteration","");return 0;}return v;}
/* M5: slot 62 = generator return value (int subset); slot 63 reserved for async await marker */
static int piton_exc_flag_get(void);
#define PITON_EXC_FLAG piton_exc_flag_get()
/* SORT_KEY_V1: sorted(iterable, key=fn) — decorar con la clave (llamada por
 * el dispatcher de closures), ordenar por la clave estable, y devolver los
 * elementos originales. La clave se aplica UNA vez por elemento (CPython). */
static int piton_slot_key_less(PitonSlot a,PitonSlot b){return piton_slot_less(a,b);}
static long piton_closure_call_frame(long,long,long*);
static long piton_sorted_key_new(void*raw,long keyfn){
  PitonSeq*s=(PitonSeq*)raw;
  if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: sorted() argument is not iterable\n",46);piton_exit(1);}
  if(!keyfn){piton_raise_set("TypeError","sorted() key must be callable");return 0;}
  long n=s->length;
  /* pares (clave, original) en slots planos: clave en items[i], original en
   * el espejo. Se usa un PitonSeq de 2*n con tag en kind PK_INT para el
   * índice: más simple y sin allocar estructuras nuevas. */
  PitonSeq*pairs=piton_seq_new(PK_LIST,n*2);
  for(long i=0;i<n;++i){
    PitonSlot orig=s->items[i];
    long k;
    {long _ka[1];_ka[0]=orig.bits;k=piton_closure_call_frame(keyfn,1,_ka);}
    if(PITON_EXC_FLAG)return 0;
    pairs->items[i*2]=(PitonSlot){k,PK_INT};
    pairs->items[i*2+1]=orig;
  }
  for(long i=1;i<n;++i){
    PitonSlot kk=pairs->items[i*2],ov=pairs->items[i*2+1];
    long j=i;
    while(j>0){
      int l=piton_slot_less(kk,pairs->items[(j-1)*2]);
      if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}
      if(l){pairs->items[j*2]=pairs->items[(j-1)*2];pairs->items[j*2+1]=pairs->items[(j-1)*2+1];--j;}else break;
    }
    pairs->items[j*2]=kk;pairs->items[j*2+1]=ov;
  }
  PitonSeq*r=piton_seq_new(PK_LIST,n);
  for(long i=0;i<n;++i)r->items[i]=pairs->items[i*2+1];
  return(long)r;
}
/* MINMAX_KEY_V1: min/max con key= — decorar con la clave, tomar el extremo
 * de la clave, devolver el elemento ORIGINAL (no la clave). */
static long piton_extreme_key_new(void*raw,long keyfn,int want_min){
  PitonSeq*s=(PitonSeq*)raw;
  if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: min()/max() argument is not iterable\n",53);piton_exit(1);}
  if(s->length==0){piton_raise_set("ValueError","min()/max() arg is an empty sequence");return 0;}
  if(!keyfn){piton_raise_set("TypeError","min()/max() key must be callable");return 0;}
  long best=s->items[0].bits,bestk=0;
  for(long i=0;i<s->length;++i){
    long k;
    {long _ka[1];_ka[0]=s->items[i].bits;k=piton_closure_call_frame(keyfn,1,_ka);}
    if(PITON_EXC_FLAG)return 0;
    if(i==0){bestk=k;best=s->items[i].bits;continue;}
    int l=piton_slot_less((PitonSlot){k,PK_INT},(PitonSlot){bestk,PK_INT});
    if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}
    /* piton_slot_less devuelve 1 si la nueva clave es MENOR, -1 si los tipos
     * no son comparables. min -> reemplaza cuando es menor; max -> cuando NO
     * es menor (y sí comparable). */
    if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}
    if(want_min?(l==1):(l==0)){bestk=k;best=s->items[i].bits;}
  }
   return best;
}
/* MINMAX_DEFAULT_V1: min/max con `default=` — la coleccion YA viene
   materializada; si esta vacia devuelve el default (CPython). Con clave
   (`key=`) la clave se aplica sobre los elementos, como en
   piton_extreme_key_new. */
static long piton_extreme_default_new(void*raw,long keyfn,int want_min,long dflt){
  PitonSeq*s=(PitonSeq*)raw;
  if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: min()/max() argument is not iterable\n",53);piton_exit(1);}
  if(s->length==0)return dflt;
  if(!keyfn){PitonSlot mm=piton_seq_minmax(s,want_min);if(PITON_EXC_FLAG)return 0;return mm.bits;}
  long best=s->items[0].bits,bestk=0;
  for(long i=0;i<s->length;++i){
    long k;
    {long _ka[1];_ka[0]=s->items[i].bits;k=piton_closure_call_frame(keyfn,1,_ka);}
    if(PITON_EXC_FLAG)return 0;
    if(i==0){bestk=k;best=s->items[i].bits;continue;}
    int l=piton_slot_less((PitonSlot){k,PK_INT},(PitonSlot){bestk,PK_INT});
    if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}
    if(want_min?(l==1):(l==0)){bestk=k;best=s->items[i].bits;}
  }
  return best;
}
static long piton_sorted_new(void*raw){PitonSeq*s=(PitonSeq*)raw;if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: sorted() argument is not iterable\n",46);piton_exit(1);}PitonSeq*r=piton_seq_new(PK_LIST,s->length);for(long i=0;i<s->length;++i)r->items[i]=s->items[i];for(long i=1;i<r->length;++i){PitonSlot v=r->items[i];long j=i;while(j>0){int l=piton_slot_less(v,r->items[j-1]);if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}if(l){r->items[j]=r->items[j-1];--j;}else break;}r->items[j]=v;}return(long)r;}
#define PITON_GEN_MAGIC 0x5049544E47654ELL
#define PITON_GEN_MAX_SLOTS 256
typedef struct{long magic;long state;long finished;long started;long sent_value;long(*func)(void*);long slots[PITON_GEN_MAX_SLOTS];long n_slots;}PitonGenerator;
static long piton_gen_new(long func,long n){PitonGenerator*g=piton_alloc(sizeof(*g));g->magic=PITON_GEN_MAGIC;g->state=0;g->finished=0;g->started=0;g->sent_value=0;g->func=(long(*)(void*))func;g->n_slots=n<0?0:(n>PITON_GEN_MAX_SLOTS?PITON_GEN_MAX_SLOTS:n);return(long)g;}
static long piton_gen_next(long raw){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a generator\n",37);piton_exit(2);}if(g->finished){piton_raise_set("StopIteration","");return 0;}g->sent_value=0;g->started=1;long r=g->func(g);if(g->finished){piton_raise_set("StopIteration","");return 0;}return r;}
static long piton_gen_send(long raw,long value){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a generator\n",37);piton_exit(2);}if(g->finished){piton_raise_set("StopIteration","");return 0;}if(!g->started&&value!=0){piton_raise_set("TypeError","can't send non-None value to a just-started generator");return 0;}g->sent_value=value;g->started=1;long r=g->func(g);if(g->finished){piton_raise_set("StopIteration","");return 0;}return r;}
static long piton_gen_free(long raw){(void)raw;return 0;}
static long piton_gen_throw(long raw,const char*t){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a generator\n",37);piton_exit(2);}g->finished=1;piton_raise_set(t,"");return 0;}
static long piton_gen_close(long raw){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a generator\n",37);piton_exit(2);}g->finished=1;return 0;}
static long piton_coro_run(long raw){PitonGenerator*c=(PitonGenerator*)raw;if((usize)raw<0x100000||!c||c->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a coroutine\n",35);piton_exit(2);}while(!c->finished){c->started=1;long y=c->func(c);if(c->finished)return y;long inner=piton_coro_run(y);c->sent_value=inner;}return 0;}
static long piton_agen_next(long raw){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not an async generator\n",42);piton_exit(2);}while(!g->finished){g->started=1;long y=g->func(g);if(g->finished)return 0;if(g->slots[63]){g->slots[63]=0;g->sent_value=piton_coro_run(y);continue;}return y;}return 0;}
#define PITON_TASK_MAGIC 0x5049544E54414B4BLL
#define PITON_GATHER_MAGIC 0x5049544E47415448LL
#define PITON_SLEEP0_MAGIC 0x5049544E53503030LL
static void piton_report_unhandled(void);
typedef struct PitonWaiter{struct PitonWaiter*next;void*task;}PitonWaiter;
typedef struct PitonGather PitonGather;
typedef struct PitonTask{long magic;long state;long result;long cancel_requested;PitonWaiter*waiters;PitonGather*gather_owner;long gather_slot;PitonGenerator*chain[64];long chain_depth;}PitonTask;
struct PitonGather{long magic;long n;long remaining;long aborted;long*results;PitonTask**tasks;PitonWaiter*waiters;};
static PitonTask*piton_ready[256];static long piton_ready_head=0;static long piton_ready_tail=0;
static void piton_ready_push(PitonTask*t){if(piton_ready_tail>=256){piton_write(2,"RuntimeError: too many queued tasks\n",36);piton_exit(2);}piton_ready[piton_ready_tail++]=t;}
static PitonTask*piton_ready_pop(void){return piton_ready_head<piton_ready_tail?piton_ready[piton_ready_head++]:0;}
static long piton_task_new(long raw){PitonTask*t=(PitonTask*)piton_alloc(sizeof(PitonTask));t->magic=PITON_TASK_MAGIC;t->state=0;t->result=0;t->cancel_requested=0;t->waiters=0;t->gather_owner=0;t->gather_slot=0;t->chain[0]=(PitonGenerator*)raw;t->chain_depth=1;return(long)t;}
static long piton_task_cancel(long raw){PitonTask*t=(PitonTask*)raw;if(!t||raw<0x100000||raw>=0x800000000000||t->magic!=PITON_TASK_MAGIC){piton_write(2,"TypeError: object has no attribute 'cancel'\n",44);piton_exit(2);}t->cancel_requested=1;return 0;}
static long piton_sleep0(long delay){if(delay<0){piton_raise_set("ValueError","sleep length must be non-negative");return 0;}if(delay>0){struct{long sec,sec_r,nsec,nsec_r;}ts={delay,0,0,0};long r;__asm__ volatile("syscall":"=a"(r):"a"(35L),"D"(&ts),"S"(0):"rcx","r11","memory");}return PITON_SLEEP0_MAGIC;}
static long piton_gather_new(long n){if(n<0)n=0;PitonGather*g=(PitonGather*)piton_alloc(sizeof(PitonGather));g->magic=PITON_GATHER_MAGIC;g->n=n;g->remaining=n;g->results=(long*)piton_alloc((usize)n*sizeof(long));g->tasks=(PitonTask**)piton_alloc((usize)n*sizeof(PitonTask*));g->waiters=0;return(long)g;}
static long piton_gather_add(long raw,long index,long task_raw){PitonGather*g=(PitonGather*)raw;PitonTask*t=(PitonTask*)task_raw;if(!g||raw<0x100000||raw>=0x800000000000||g->magic!=PITON_GATHER_MAGIC){piton_write(2,"TypeError: object is not a gather\n",33);piton_exit(2);}if(!t||task_raw<0x100000||task_raw>=0x800000000000||t->magic!=PITON_TASK_MAGIC){piton_write(2,"TypeError: gather requires tasks\n",32);piton_exit(2);}if(index<0||index>=g->n){piton_write(2,"TypeError: gather index out of range\n",37);piton_exit(2);}g->tasks[index]=t;t->gather_owner=g;t->gather_slot=index;if(t->state==4){g->aborted=1;}else if(t->state==3){g->results[index]=t->result;--g->remaining;}return 0;}
static long piton_build_result_list(long*results,long n){PitonSeq*s=piton_seq_new(PK_LIST,n);for(long i=0;i<n;++i)piton_seq_put(s,i,(PitonSlot){results[i],PK_INT});return(long)s;}
static void piton_gather_complete(PitonGather*g){long list=piton_build_result_list(g->results,g->n);PitonWaiter*w=g->waiters;g->waiters=0;while(w){PitonWaiter*nx=w->next;PitonTask*aw=(PitonTask*)w->task;aw->chain[aw->chain_depth-1]->sent_value=list;piton_ready_push(aw);w=nx;}}
static void piton_notify_done(PitonTask*t){if(t->gather_owner){PitonGather*g=t->gather_owner;if(!g->aborted){g->results[t->gather_slot]=t->result;if(--g->remaining==0)piton_gather_complete(g);}}PitonWaiter*w=t->waiters;t->waiters=0;while(w){PitonWaiter*nx=w->next;PitonTask*aw=(PitonTask*)w->task;aw->chain[aw->chain_depth-1]->sent_value=t->result;piton_ready_push(aw);w=nx;}}
static long piton_step_task(PitonTask*task){while(1){if(task->chain_depth==0){task->state=3;task->result=0;return 1;}PitonGenerator*coro=task->chain[task->chain_depth-1];coro->started=1;long y=coro->func(coro);if(coro->finished){long result=y;task->chain_depth--;if(task->chain_depth==0){task->state=3;task->result=result;return 1;}task->chain[task->chain_depth-1]->sent_value=result;continue;}if(y==PITON_SLEEP0_MAGIC){piton_ready_push(task);return 0;}if(y<0x100000||y>=0x800000000000){piton_write(2,"TypeError: object is not awaitable\n",35);piton_exit(2);}if(((long*)y)[0]==PITON_GEN_MAGIC){if(task->chain_depth>=64){piton_write(2,"RuntimeError: await chain too deep\n",34);piton_exit(2);}task->chain[task->chain_depth++]=(PitonGenerator*)y;continue;}if(((long*)y)[0]==PITON_TASK_MAGIC){PitonTask*other=(PitonTask*)y;if(other->cancel_requested||other->state==4){task->cancel_requested=1;piton_ready_push(task);return 0;}PitonWaiter*w=(PitonWaiter*)piton_alloc(sizeof(PitonWaiter));w->task=task;w->next=other->waiters;other->waiters=w;if(other->state==0)piton_ready_push(other);return 0;}if(((long*)y)[0]==PITON_GATHER_MAGIC){PitonGather*g=(PitonGather*)y;if(g->aborted){task->cancel_requested=1;piton_ready_push(task);return 0;}if(g->remaining==0){coro->sent_value=piton_build_result_list(g->results,g->n);continue;}PitonWaiter*w=(PitonWaiter*)piton_alloc(sizeof(PitonWaiter));w->task=task;w->next=g->waiters;g->waiters=w;for(long i=0;i<g->n;++i)if(g->tasks[i]&&g->tasks[i]->state==0)piton_ready_push(g->tasks[i]);return 0;}piton_write(2,"TypeError: object is not awaitable\n",35);piton_exit(2);}}
static long piton_event_run(long raw){PitonGenerator*root=(PitonGenerator*)raw;if(!root||root->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a coroutine\n",35);piton_exit(2);}piton_ready_head=0;piton_ready_tail=0;PitonTask*root_task=(PitonTask*)piton_task_new(raw);piton_ready_push(root_task);while(1){PitonTask*task=piton_ready_pop();if(!task)break;if(task->cancel_requested&&task->state!=3){task->state=4;if(task->gather_owner){PitonGather*g=task->gather_owner;if(!g->aborted){g->aborted=1;PitonWaiter*gw=g->waiters;g->waiters=0;while(gw){PitonWaiter*gnext=gw->next;((PitonTask*)gw->task)->cancel_requested=1;piton_ready_push((PitonTask*)gw->task);gw=gnext;}}}PitonWaiter*w=task->waiters;task->waiters=0;while(w){PitonWaiter*nx=w->next;((PitonTask*)w->task)->cancel_requested=1;piton_ready_push((PitonTask*)w->task);w=nx;}continue;}if(piton_step_task(task))piton_notify_done(task);}if(root_task->cancel_requested||root_task->state==4){piton_raise_set("CancelledError","");piton_report_unhandled();piton_exit(2);}return root_task->result;}
static long piton_gen_collect(long raw){PitonGenerator*g=(PitonGenerator*)raw;if(!g||g->magic!=PITON_GEN_MAGIC){piton_write(2,"TypeError: object is not a generator\n",37);piton_exit(2);}PitonSeq*s=piton_seq_new(PK_LIST,0);while(!g->finished){long v=g->func(g);if(g->finished)break;piton_seq_append(s,(PitonSlot){v,PK_INT});}return(long)s;}
static PitonObject*piton_all_objects=0;
static PitonObject*piton_object_new_finalized(const char*name,const char*parent,long finalizer){PitonObject*o=piton_alloc(sizeof(*o));o->refcount=1;o->kind=PK_OBJECT;o->class_name=name;o->parent_name=parent;o->finalizer=finalizer;o->finalizer_called=0;o->next_all=piton_all_objects;piton_all_objects=o;return o;}
static PitonObject*piton_object_new(const char*name,const char*parent){return piton_object_new_finalized(name,parent,0);}
static void piton_finalize_objects(void){PitonObject*o=piton_all_objects;while(o){if(o->finalizer&&!o->finalizer_called){o->finalizer_called=1;((long(*)(long))o->finalizer)((long)o);}o=o->next_all;}}
static void piton_object_set(PitonObject*o,const char*name,PitonSlot v){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0){o->attrs[i].value=v;return;}if(o->length>=32){piton_write(2,"AttributeError\n",15);piton_exit(1);}o->attrs[o->length].name=name;o->attrs[o->length++].value=v;}
/* DEL_ATTR_DEFAULT_V1: `del obj.attr` sobre objeto nativo sin __delattr__
   definido — CPython elimina de __dict__ (object.__delattr__). Levanta
   AttributeError si el atributo no existe, para que el handler de try/excepto
   lo capture. */
static void piton_object_del(PitonObject*o,const char*name){if(!o){piton_raise_set("AttributeError","null object");return;}if(o->kind!=PK_OBJECT){piton_raise_set("TypeError","can't delete attributes of built-in type");return;}for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0){for(long j=i;j+1<o->length;++j)o->attrs[j]=o->attrs[j+1];--o->length;return;}piton_raise_set("AttributeError",name);}
static PitonSlot piton_object_get(PitonObject*o,const char*name){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value;piton_write(2,"AttributeError\n",15);piton_exit(1);}
static long piton_object_lookup(PitonObject*o,const char*name,long fallback){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value.bits;return ((long(*)(long,long))fallback)((long)o,(long)name);}
static long piton_hasattr(long raw,const char*name){if(!raw||!name)return 0;PitonObject*o=(PitonObject*)raw;if(o->kind==PK_OBJECT){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return 1;}return 0;}
static long piton_getattr(long raw,const char*name,int has_def,long def_val){if(raw&&name){PitonObject*o=(PitonObject*)raw;if(o->kind==PK_OBJECT){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value.bits;}}if(has_def)return def_val;piton_raise_set("AttributeError","object has no attribute");return 0;}
static void piton_setattr(long raw,const char*name,PitonSlot val){if(!raw){piton_raise_set("AttributeError","null object");return;}PitonObject*o=(PitonObject*)raw;if(o->kind!=PK_OBJECT){piton_raise_set("TypeError","can't set attributes of built-in type");return;}piton_object_set(o,name,val);}
static long piton_isinstance_class(long raw,const char*target_name){if(!raw||!target_name)return 0;PitonObject*o=(PitonObject*)raw;if(o->kind!=PK_OBJECT)return 0;if(o->class_name&&piton_strcmp(o->class_name,target_name)==0)return 1;if(o->parent_name&&piton_strcmp(o->parent_name,target_name)==0)return 1;return 0;}
#define PITON_CLOSURE_MAGIC 0x5049544EC10557LL
#define PITON_BOUND_METHOD_MAGIC 0x5049544E424D4554LL
typedef struct{long magic;long addr;long n_args;long n_cells;long*cells;long has_vararg;long has_kwarg;}PitonClosure;
typedef struct{long magic;long addr;long self;long n_args;}PitonBoundMethod;
static long piton_bound_method_new(long addr,long n_args,long self){PitonBoundMethod*m=piton_alloc(sizeof(*m));m->magic=PITON_BOUND_METHOD_MAGIC;m->addr=addr;m->self=self;m->n_args=n_args;return(long)m;}
static long piton_bound_method_self(long raw){PitonBoundMethod*m=(PitonBoundMethod*)raw;if(!m||m->magic!=PITON_BOUND_METHOD_MAGIC){piton_write(2,"AttributeError: bound method has no __self__\n",45);piton_exit(1);}return m->self;}
static long piton_closure_new8(long addr,long n_args,long n_cells,long c0,long c1,long c2,long c3){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;c->has_vararg=0;c->has_kwarg=0;c->cells=piton_alloc((usize)n_cells*sizeof(long));long cs[4]={c0,c1,c2,c3};for(long i=0;i<n_cells&&i<4;++i)c->cells[i]=cs[i];return(long)c;}
/* CALL_NONCALLABLE_V1: a callee is only callable if it is a live object
   inside the arena (then its magic decides) or a real code address in
   this executable's text. `x = 5; x()` used to dereference address 5
   and die with SIGSEGV; an int, a float, None and a string/heap data
   pointer are all outside both ranges, so they are refused with the
   CPython TypeError instead of executing garbage. The ranges are the
   ONLY safe way to decide this: the untagged model gives a function
   value the same static type as an int, so no static check can tell
   `g = f` (callable) from `x = 5`. */
extern char __executable_start[];extern char _etext[];
static int piton_in_arena(long p){return p!=0&&(unsigned char*)p>=piton_arena&&(unsigned char*)p<piton_arena+sizeof(piton_arena);}
static int piton_is_code_addr(long p){return p!=0&&(char*)p>=__executable_start&&(char*)p<_etext;}
static int piton_callable_value(long callee){return piton_in_arena(callee)||piton_is_code_addr(callee);}
static void piton_raise_not_callable(void){piton_write(2,"TypeError: 'X' object is not callable\n",35);piton_exit(1);}
static long piton_closure_call6(long callee,long argc,long a0,long a1,long a2,long a3){if(!callee||((long*)callee)[0]!=PITON_CLOSURE_MAGIC){if(!piton_is_code_addr(callee))piton_raise_not_callable();return((long(*)(long,long,long,long))callee)(a0,a1,a2,a3);}PitonClosure*c=(PitonClosure*)callee;if(argc!=c->n_args){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long total=c->n_cells+argc;if(total>4){piton_write(2,"TypeError: closure cell count plus arguments exceeds four\n",59);piton_exit(2);}long x[4]={a0,a1,a2,a3};for(long i=0;i<c->n_cells&&i<4;++i){for(long j=3;j>i;--j)x[j]=x[j-1];x[i]=c->cells[i];}return((long(*)(long,long,long,long))c->addr)(x[0],x[1],x[2],x[3]);}
static long piton_closure_new_frame(long addr,long n_args,long n_cells,long*cells,long has_vararg,long has_kwarg){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;c->has_vararg=has_vararg;c->has_kwarg=has_kwarg;c->cells=piton_alloc((usize)(n_cells?n_cells:1)*sizeof(long));for(long i=0;i<n_cells;++i)c->cells[i]=cells[i];return(long)c;}
static long piton_closure_call_frame(long callee,long argc,long*args){if(!piton_callable_value(callee)){piton_raise_not_callable();}if(piton_is_code_addr(callee)){/* CODE_ADDR_FIRST_V1: un code address se detecta PRIMERO — leer sus magic numbers (el codigo maquina) como si fueran un objeto puede coincidir por azar y fabricar una llamada rota (SIGSEGV). */if(argc>4){piton_write(2,"TypeError: native call exceeds four direct arguments\n",52);piton_exit(2);}long a[4]={0,0,0,0};for(long i=0;i<argc;++i)a[i]=args[i];return((long(*)(long,long,long,long))callee)(a[0],a[1],a[2],a[3]);}if(callee&&((long*)callee)[0]==PITON_BOUND_METHOD_MAGIC){PitonBoundMethod*m=(PitonBoundMethod*)callee;if(argc!=m->n_args||argc>3){piton_write(2,"TypeError: bound method called with wrong number of arguments\n",61);piton_exit(2);}long a[4]={m->self,0,0,0};for(long i=0;i<argc;++i)a[i+1]=args[i];return((long(*)(long,long,long,long))m->addr)(a[0],a[1],a[2],a[3]);}if(callee&&((long*)callee)[1]==PK_OBJECT){PitonObject*co=(PitonObject*)callee;PitonObject*inst=piton_object_new(co->class_name,co->parent_name);for(long i=0;i<co->length;++i){piton_object_set(inst,co->attrs[i].name,co->attrs[i].value);}return(long)inst;}PitonClosure*c=(PitonClosure*)callee;if(!c||c->magic!=PITON_CLOSURE_MAGIC){if(!piton_is_code_addr(callee)){piton_raise_not_callable();}if(argc>4){piton_write(2,"TypeError: native call exceeds four direct arguments\n",52);piton_exit(2);}long a[4]={0,0,0,0};for(long i=0;i<argc;++i)a[i]=args[i];return((long(*)(long,long,long,long))callee)(a[0],a[1],a[2],a[3]);}if(argc<c->n_args||(!c->has_vararg&&argc!=c->n_args)){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long extra=c->has_vararg&&argc>c->n_args?argc-c->n_args:0;long total=c->n_cells+c->n_args+(c->has_vararg?1:0);long*frame=piton_alloc((usize)total*sizeof(long));for(long i=0;i<c->n_cells;++i)frame[i]=c->cells[i];for(long i=0;i<c->n_args;++i)frame[c->n_cells+i]=args[i];if(c->has_vararg){PitonSeq*t=piton_seq_new(PK_TUPLE,extra);for(long i=0;i<extra;++i)t->items[i]=(PitonSlot){args[c->n_args+i],PK_INT};frame[c->n_cells+c->n_args]=(long)t;}return((long(*)(long*))c->addr)(frame);}
static long piton_callback_invoke(long callback,long value){long args[1]={value};return piton_closure_call_frame(callback,1,args);}
/* DYNAMIC_CALL_V1: llamar a un callable que NO se conoce en build time,
   con los argumentos posicionales y el dict de keywords resueltos en
   RUNTIME (`f(*args, **kw)` / `f(k=v)` con `f` dinamico).

   El frame del callee se arma segun su layout:
     - closure: [celdas..., params fijos..., tupla del *args?, dict de **kw?]
     - metodo ligado: [self, args...]
     - code address: hasta 4 argumentos directos.
   Los defaults NO se pueden llenar (no se conoce la firma): si faltan
   argumentos posicionales, es un TypeError en runtime, como en CPython
   cuando la llamada no encaja. */
static long piton_call_dynamic(long callee,long pos_seq,long star_seq,long kwargs_dict){
  PitonSeq*pos=(PitonSeq*)pos_seq,*star=(PitonSeq*)star_seq;
  long np=pos?pos->length:0,ns=star?star->length:0,total=np+ns;
  if(total>64){piton_write(2,"TypeError: too many arguments in a dynamic call\n",48);piton_exit(2);}
  long args[64];
  for(long i=0;i<np;++i)args[i]=pos->items[i].bits;
  for(long i=0;i<ns;++i)args[np+i]=star->items[i].bits;
  if(!piton_callable_value(callee)){piton_raise_not_callable();return 0;}
  if(callee&&((long*)callee)[0]==PITON_BOUND_METHOD_MAGIC){
    PitonBoundMethod*m=(PitonBoundMethod*)callee;
    if(total+1!=m->n_args||total>3){piton_write(2,"TypeError: bound method called with wrong number of arguments\n",61);piton_exit(2);}
    long a[4]={m->self,0,0,0};for(long i=0;i<total;++i)a[i+1]=args[i];
    return((long(*)(long,long,long,long))m->addr)(a[0],a[1],a[2],a[3]);
  }
  if(callee&&((long*)callee)[0]==PITON_CLOSURE_MAGIC){
    PitonClosure*c=(PitonClosure*)callee;
    long n_pos=c->n_args;
    if(!c->has_vararg&&total<n_pos){piton_write(2,"TypeError: missing positional arguments in a dynamic call\n",52);piton_exit(2);}
    long extra=(c->has_vararg&&total>n_pos)?total-n_pos:0;
    long slots=c->n_cells+n_pos+(c->has_vararg?1:0)+(c->has_kwarg?1:0);
    long*frame=piton_alloc((usize)slots*sizeof(long));
    for(long i=0;i<c->n_cells;++i)frame[i]=c->cells[i];
    long nf=total<n_pos?total:n_pos;
    for(long i=0;i<nf;++i)frame[c->n_cells+i]=args[i];
    if(c->has_vararg){PitonSeq*t=piton_seq_new(PK_TUPLE,extra);for(long i=0;i<extra;++i)t->items[i]=(PitonSlot){args[n_pos+i],PK_INT};frame[c->n_cells+n_pos]=(long)t;}
    if(c->has_kwarg)frame[slots-1]=kwargs_dict;
    return((long(*)(long*))c->addr)(frame);
  }
  if(!piton_is_code_addr(callee)){piton_raise_not_callable();return 0;}
  if(total>4){piton_write(2,"TypeError: native call exceeds four direct arguments\n",52);piton_exit(2);}
  long a[4]={0,0,0,0};for(long i=0;i<total;++i)a[i]=args[i];
  return((long(*)(long,long,long,long))callee)(a[0],a[1],a[2],a[3]);
}
static long piton_frame_call(long addr,long argc,long*args){long*frame=piton_alloc((usize)argc*sizeof(long));for(long i=0;i<argc;++i)frame[i]=args[i];return((long(*)(long*))addr)(frame);}
static void piton_bigint_print_raw(void*a);
static void piton_bytes_print(PitonBytes* b);
static void piton_bytearray_print(PitonByteArray* ba);
static void piton_print_slot(PitonSlot v);
static void piton_print_slot_repr(PitonSlot v);
static void piton_print_complex_raw(long bits);
static long piton_cx_eq(PitonComplexVal x,PitonComplexVal y);
static int piton_cx_truthy_bits(long bits);
static void piton_print_seq(PitonSeq*s){piton_write(piton_print_fd,s->kind==PK_TUPLE?"(":"[",1);for(long i=0;i<s->length;++i){if(i)piton_write(piton_print_fd,", ",2);piton_print_slot_repr(s->items[i]);}if(s->kind==PK_TUPLE&&s->length==1)piton_write(piton_print_fd,",",1);piton_write(piton_print_fd,s->kind==PK_TUPLE?")":"]",1);}
static void piton_print_dict(PitonDict*d){piton_write(piton_print_fd,"{",1);for(long i=0;i<d->length;++i){if(i)piton_write(piton_print_fd,", ",2);piton_print_slot_repr(d->items[i].key);piton_write(piton_print_fd,": ",2);piton_print_slot_repr(d->items[i].value);}piton_write(piton_print_fd,"}",1);}
/* DICT_EQ_V1: igualdad por contenido (mismo largo, cada clave de a existe en
 * b con slot_eq igual). */
static int piton_dict_eq(PitonDict*a,PitonDict*b){
  if(a==b)return 1;
  if(!a||!b)return 0;
  if(a->length!=b->length)return 0;
  /* busqueda propia (piton_dict_get lanza KeyError y el flag de excepcion
   * vive mas abajo en el archivo: no se puede usar aqui) */
  for(long i=0;i<a->length;++i){
    PitonSlot key=a->items[i].key;
    long found=-1;
    for(long j=0;j<b->length;++j){
      if(piton_slot_eq(b->items[j].key,key)){found=j;break;}
    }
    if(found<0)return 0;
    if(!piton_slot_eq(a->items[i].value,b->items[found].value))return 0;
  }
  return 1;
}
static void piton_print_set(PitonSet*s){if(s->kind==PK_SET&&s->length==0){piton_write(piton_print_fd,"set()",5);return;}piton_write(piton_print_fd,"{",1);for(long i=0;i<s->length;++i){if(i)piton_write(piton_print_fd,", ",2);piton_print_slot_repr(s->items[i]);}piton_write(piton_print_fd,"}",1);}
static long piton_object_repr_hook(long obj,const char*cls);
static void piton_print_slot(PitonSlot v){if(v.bits==(long)&piton_none_singleton){piton_write(piton_print_fd,"None",4);return;}switch(v.kind){case PK_NONE:piton_write(piton_print_fd,"None",4);break;case PK_BOOL:piton_write(piton_print_fd,v.bits?"True":"False",v.bits?4:5);break;case PK_INT:piton_write_int(v.bits);break;case PK_FLOAT:{char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)v.bits);piton_write(piton_print_fd,r,(usize)n);break;}case PK_STR:piton_write(piton_print_fd,"'",1);piton_write(piton_print_fd,(const char*)v.bits,piton_strlen((const char*)v.bits));piton_write(piton_print_fd,"'",1);break;case PK_LIST:case PK_TUPLE:piton_print_seq((PitonSeq*)v.bits);break;case PK_DICT:piton_print_dict((PitonDict*)v.bits);break;case PK_SET:piton_print_set((PitonSet*)v.bits);break;case PK_BIGINT:piton_bigint_print_raw((void*)v.bits);break;case PK_RANGE:piton_range_print((PitonRange*)v.bits);break;case PK_BYTES:piton_bytes_print((PitonBytes*)v.bits);break;case PK_BYTEARRAY:piton_bytearray_print((PitonByteArray*)v.bits);break;case PK_COMPLEX:piton_print_complex_raw(v.bits);break;case PK_FROZENSET:if(((PitonSet*)v.bits)->length==0){piton_write(piton_print_fd,"frozenset()",11);break;}piton_write(piton_print_fd,"frozenset(",10);piton_print_set((PitonSet*)v.bits);piton_write(piton_print_fd,")",1);break;case PK_SLICE:piton_print_slice((PitonSlice*)v.bits);break;case PK_MEMORYVIEW:piton_print_memoryview((PitonMemoryView*)v.bits);break;case PK_OBJECT:{long _pr=piton_object_repr_hook(v.bits,((PitonObject*)v.bits)->class_name);if(_pr){piton_print_str_raw((const char*)_pr);break;}piton_write(piton_print_fd,"<object>",8);break;}default:piton_write(piton_print_fd,"<object>",8);}}
/* SEQ_ELEM_REPR_V1: los elementos de una coleccion se imprimen con el
   repr completo de CPython (escapes de backslash/comillas); el camino
   viejo escribia el str raw dentro de [ ] y print(["a\\b"]) divergiaba.
   El print top-level de str queda intacto. */
static void piton_print_slot_repr(PitonSlot v){if(v.bits==(long)&piton_none_singleton){piton_write(piton_print_fd,"None",4);return;}if(v.kind==PK_STR){PitonStrBuf b;sb_init(&b);piton_str_repr_into(&b,(const char*)v.bits);piton_write(piton_print_fd,b.d,(usize)b.len);return;}piton_print_slot(v);}
/* COMP_DICT_ITER_V1: the i-th KEY of a dict / the i-th element of a set,
 * for a comprehension's index loop. Explicit subscripts keep using
 * piton_dict_get, which raises KeyError as CPython does. */
static PitonSlot piton_dict_nth_key(PitonDict*d,long i){if(!d||i<0||i>=d->length)piton_write(2,"IndexError\n",10),piton_exit(1);return d->items[i].key;}
static PitonSlot piton_set_nth(PitonSet*s,long i){if(!s||i<0||i>=s->length)piton_write(2,"IndexError\n",10),piton_exit(1);return s->items[i];}
/* SUM_START_V1: variantes con valor inicial. */
static long piton_sum_seq_start(PitonSeq*s,long start){long r=start;if(s)for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_INT||v.kind==PK_BOOL)r+=v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'list'");return 0;}}return r;}
static long piton_sum_seq_float_start(PitonSeq*s,long start){double r=piton_bits_double(start);if(s)for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_FLOAT)r+=piton_bits_double(v.bits);else if(v.kind==PK_INT||v.kind==PK_BOOL)r+=(double)v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'list'");return 0;}}return piton_double_bits(r);}
static long piton_sum_seq(PitonSeq*s){long r=0;for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_INT||v.kind==PK_BOOL)r+=v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'list'");return 0;}}return r;}
static long piton_sum_dict(PitonDict*d){long r=0;for(long i=0;i<d->length;++i){PitonSlot v=d->items[i].key;if(v.kind==PK_INT||v.kind==PK_BOOL)r+=v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'list'");return 0;}}return r;}
static long piton_sum_set(PitonSet*s){long r=0;for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_INT||v.kind==PK_BOOL)r+=v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'int' and 'list'");return 0;}}return r;}
static const char*piton_type_repr(int kind){switch(kind){case PK_NONE:return"<class 'NoneType'>";case PK_BOOL:return"<class 'bool'>";case PK_INT:return"<class 'int'>";case PK_FLOAT:return"<class 'float'>";case PK_STR:return"<class 'str'>";case PK_LIST:return"<class 'list'>";case PK_TUPLE:return"<class 'tuple'>";case PK_DICT:return"<class 'dict'>";case PK_SET:return"<class 'set'>";case PK_RANGE:return"<class 'range'>";case PK_BYTES:return"<class 'bytes'>";case PK_BYTEARRAY:return"<class 'bytearray'>";case PK_COMPLEX:return"<class 'complex'>";case PK_SLICE:return"<class 'slice'>";case PK_MEMORYVIEW:return"<class 'memoryview'>";case PK_FROZENSET:return"<class 'frozenset'>";default:return"<class 'object'>";}}
/* M14 BUILTINS_CORE_V2 (matriz declarada; ver native_runtime.c) */
static int piton_slot_truthy(PitonSlot v){switch(v.kind){case PK_NONE:return 0;case PK_BOOL:return v.bits?1:0;case PK_INT:return v.bits!=0;case PK_FLOAT:return piton_bits_double(v.bits)!=0.0;case PK_COMPLEX:return piton_cx_truthy_bits(v.bits);case PK_FROZENSET:return ((PitonSet*)v.bits)->length>0;case PK_STR:return v.bits&&((const char*)v.bits)[0]!=0;case PK_LIST:case PK_TUPLE:return ((PitonSeq*)v.bits)->length>0;case PK_RANGE:return piton_range_len((PitonRange*)v.bits)>0;default:return v.bits!=0;}}
static long piton_all_seq(PitonSeq*s){if(!s)return 1;for(long i=0;i<s->length;++i)if(!piton_slot_truthy(s->items[i]))return 0;return 1;}
static long piton_any_seq(PitonSeq*s){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_truthy(s->items[i]))return 1;return 0;}
/* POW_MOD_V1: pow(base, exp, mod) por exponenciación binaria (i64). */
static long piton_pow_mod(long b,long e,long m){
  if(m==0){piton_raise_set("ZeroDivisionError","pow() 3rd argument cannot be 0");return 0;}
  if(m<0)m=-m;
  if(e<0){piton_raise_set("ValueError","pow() 2nd argument cannot be negative when 3rd argument specified");return 0;}
  unsigned long base=(unsigned long)(b%m);
  if(base>0x7FFFFFFFFFFFFFFFUL)base+=(unsigned long)(0-(long)base)+(unsigned long)2*m; /* normalizar signo */
  unsigned long mod=(unsigned long)m;
  unsigned long result=1UL%mod;   /* pow(x, 0, 1) == 0 en CPython */
  while(e>0){
    if(e&1)result=(result*base)%mod;
    e>>=1;
    if(e)base=(base*base)%mod;
  }
  return(long)result;
}
static long piton_pow_int(long b,long e){if(e<0){piton_raise_set("TypeError","pow() negative exponent unsupported (M14 v1)");return 0;}long acc=1;while(e>0){if(e&1)acc*=b;b*=b;e>>=1;}return acc;}
static long piton_pow_float(long bb,long e){double b=piton_bits_double(bb);long neg=e<0;if(neg)e=-e;double acc=1.0;while(e>0){if(e&1)acc*=b;b*=b;e>>=1;}if(neg)acc=1.0/acc;return piton_double_bits(acc);}
static long piton_ord(const char*s){if(!s||!s[0]){piton_raise_set("TypeError","ord() expected a character, but string of length 0 found");return 0;}const unsigned char*u=(const unsigned char*)s;long cp;long n;if(u[0]<0x80){cp=u[0];n=1;}else if((u[0]&0xE0)==0xC0){cp=u[0]&0x1F;n=2;}else if((u[0]&0xF0)==0xE0){cp=u[0]&0x0F;n=3;}else if((u[0]&0xF8)==0xF0){cp=u[0]&0x07;n=4;}else{piton_raise_set("TypeError","ord() received invalid UTF-8");return 0;}for(long i=1;i<n;++i)cp=(cp<<6)|(u[i]&0x3F);if(s[n]){piton_raise_set("TypeError","ord() expected a character, but string of length >1 found");return 0;}return cp;}
static long piton_chr(long cp);
static long piton_percent_chr(long cp){if(cp<0||cp>0x10FFFF){piton_raise_set("OverflowError","%c arg not in range(0x110000)");return 0;}return piton_chr(cp);}
static long piton_chr(long cp){if(cp<0||cp>0x10FFFF){piton_raise_set("ValueError","chr() arg not in range(0x110000)");return 0;}char*p=piton_alloc(5);if(cp<0x80){p[0]=(char)cp;p[1]=0;}else if(cp<0x800){p[0]=(char)(0xC0|(cp>>6));p[1]=(char)(0x80|(cp&0x3F));p[2]=0;}else if(cp<0x10000){p[0]=(char)(0xE0|(cp>>12));p[1]=(char)(0x80|((cp>>6)&0x3F));p[2]=(char)(0x80|(cp&0x3F));p[3]=0;}else{p[0]=(char)(0xF0|(cp>>18));p[1]=(char)(0x80|((cp>>12)&0x3F));p[2]=(char)(0x80|((cp>>6)&0x3F));p[3]=(char)(0x80|(cp&0x3F));p[4]=0;}return(long)p;}
/* STR_MAKETRANS_V1: str.maketrans(dict) -> tabla int(ord) -> str|None, que
 * es exactamente lo que piton_str_translate ya consume (claves enteras y
 * valor NULL = borrar). shlex: wordchars.maketrans(dict.fromkeys(
 * punctuation_chars)) — claves de un byte y valores None.
 * CPython acepta claves str de longitud 1 o int, y valores str/None/int
 * (el int se codifica como UTF-8 con piton_chr). Lo que piton_str_translate
 * no puede honrar — claves no ASCII, porque solo sustituye posiciones ASCII
 * — falla aqui en tiempo de ejecucion: no se diverge en silencio. */
static long piton_str_maketrans(PitonDict*m){
  PitonDict*t=piton_dict_new(0);
  if(!m)return(long)t;
  for(long i=0;i<m->length;++i){
    PitonSlot k=m->items[i].key;
    long ord;
    if(k.kind==PK_STR){
      const char*ks=(const char*)k.bits;
      if(!ks||piton_strlen(ks)!=1){piton_write(2,"ValueError: str.maketrans() keys must be single characters\n",59);piton_exit(1);}
      if((unsigned char)ks[0]>=0x80){piton_write(2,"ValueError: str.maketrans() with non-ASCII keys is not supported in the native subset\n",86);piton_exit(1);}
      ord=(long)(unsigned char)ks[0];
    }else if(k.kind==PK_INT){
      if(k.bits<0||k.bits>=0x80){piton_write(2,"ValueError: str.maketrans() with non-ASCII keys is not supported in the native subset\n",86);piton_exit(1);}
      ord=k.bits;
    }else{
      piton_write(2,"ValueError: str.maketrans() keys must be str or int\n",52);piton_exit(1);
    }
    PitonSlot v=m->items[i].value;
    long vb=0;int vk=PK_NONE;
    if(v.kind==PK_NONE){vb=0;vk=PK_NONE;}
    else if(v.kind==PK_STR){vb=v.bits;vk=PK_STR;}
    else if(v.kind==PK_INT){
      if(v.bits<0||v.bits>0x10FFFF){piton_write(2,"ValueError: str.maketrans() value out of range\n",47);piton_exit(1);}
      vb=piton_chr(v.bits);vk=PK_STR;
    }else{
      piton_write(2,"ValueError: str.maketrans() value must be str, int or None\n",59);piton_exit(1);
    }
    piton_dict_set(t,piton_slot(ord,PK_INT),(PitonSlot){vb,vk});
  }
  return(long)t;
}
/* INT_FROM_BYTES_V1: int.from_bytes(bytes, byteorder='big', signed=False) —
 * classmethod. Convierte bytes/bytearray a int (64-bit signed). byteorder:
 * 'big' | 'little' (default 'big'); signed: bool (default False).
 * Falla cerrado en runtime si: buffer no bytes/bytearray, byteorder invalido,
 * overflow 64-bit (CPython usa arbitrary precision; aqui limitamos a 64-bit
 * igual que el resto del subset nativo). El dispatch pasa PitonSeq*; 
 * bytearray (PK_BYTEARRAY) tiene campo capacity extra -> data en offset
 * distinto; distinguimos por kind. */
static long piton_int_from_bytes(PitonSeq*buf,const char*order,int is_signed){
  if(!buf||(buf->kind!=PK_BYTES&&buf->kind!=PK_BYTEARRAY)){
    piton_write(2,"TypeError: int.from_bytes() argument must support the buffer protocol\n",70);piton_exit(1);}
  if(!order||(piton_strcmp(order,"big")&&piton_strcmp(order,"little"))){
    piton_write(2,"ValueError: byteorder must be either 'big' or 'little'\n",51);piton_exit(1);}
  unsigned char*b; long n;
  if(buf->kind==PK_BYTES){
    PitonBytes*bb=(PitonBytes*)buf; b=bb->data; n=bb->length;
  }else{
    PitonByteArray*ba=(PitonByteArray*)buf; b=ba->data; n=ba->length;
  }
  unsigned long long acc=0;
  if(piton_strcmp(order,"big")==0){
    for(long i=0;i<n;++i){acc=(acc<<8)|b[i];}}
  else{
    for(long i=n-1;i>=0;--i){acc=(acc<<8)|b[i];}}
  if(is_signed){
    if(n>0&&(b[0]&0x80)){
      if(piton_strcmp(order,"big")==0){
        for(long i=0;i<n;++i)acc|=~0ULL<<(8*(i+1));
      }else{
        for(long i=n-1;i>=0;--i)acc|=~0ULL<<(8*(n-i));
      }
    }
    long long sval = (long long)acc;
    if(sval < -0x8000000000000000LL || sval > 0x7FFFFFFFFFFFFFFFLL){
      piton_write(2,"OverflowError: int.from_bytes() overflow\n",41);piton_exit(1);}
    return sval;
  }else{
    if(acc>0xFFFFFFFFFFFFFFFFULL){piton_write(2,"OverflowError: int.from_bytes() overflow\n",41);piton_exit(1);}
    return (long)acc;
  }
}
/* BYTES_MAKETRANS_V1: bytes.maketrans(frm, to) -> 256-byte translation table.
 * frm y to son bytes/bytearray de igual longitud. Retorna bytes de 256 bytes
 * donde table[frm[i]] = to[i]. Falla cerrado si longitudes difieren o no son
 * bytes/bytearray. El dispatch pasa PitonSeq* (el sistema de tipos trata
 * bytes como secuencia); hacemos cast interno a PitonBytes*. */
static long piton_bytes_maketrans(PitonSeq*frm,PitonSeq*to){
  if(!frm||!to||frm->length!=to->length){
    piton_write(2,"ValueError: bytes.maketrans() arguments must be of equal length\n",58);piton_exit(1);}
  if(frm->kind!=PK_BYTES||to->kind!=PK_BYTES){
    piton_write(2,"TypeError: bytes.maketrans() requires bytes arguments\n",51);piton_exit(1);}
  PitonBytes*bf=(PitonBytes*)frm; PitonBytes*bt=(PitonBytes*)to;
  unsigned char*t=(unsigned char*)piton_alloc(256);
  for(int i=0;i<256;++i)t[i]=i;
  unsigned char*f=bf->data; unsigned char*g=bt->data;
  for(long i=0;i<frm->length;++i)t[f[i]]=g[i];
  PitonBytes*r=(PitonBytes*)piton_alloc(sizeof(PitonBytes));
  r->refcount=1; r->kind=PK_BYTES; r->length=256; r->data=t;
  return(long)r;
}
/* FLOAT_FROMHEX_V1: float.fromhex(str) — classmethod. Hex float parsing
 * (0x1.23p+4) delega a piton_strtod_local (ya implementado). Falla cerrado
 * si str no es str/bytes o parsing no avanza (formato invalido). */
static long piton_float_fromhex(const char*s){
  if(!s){piton_write(2,"TypeError: float.fromhex() argument must be a string\n",49);piton_exit(1);}
  char*end=0; double d=piton_strtod_local(s,&end);
  if(end==s){piton_write(2,"ValueError: float.fromhex() invalid hex format\n",45);piton_exit(1);}
  return piton_double_bits(d);
}
static long piton_bin(long v){char*p=piton_alloc(70);usize o=0;unsigned long m;if(v<0){p[o++]='-';m=(unsigned long)(-(v+1))+1;}else m=(unsigned long)v;p[o++]='0';p[o++]='b';char tmp[64];long n=0;do{tmp[n++]=(char)('0'+(m&1));m>>=1;}while(m);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static long piton_round_float(long bits){double x=piton_bits_double(bits);double ax=x<0?-x:x;if(ax>=9.0e18){piton_raise_set("OverflowError","round() float too large to convert to int");return 0;}long t=(long)ax;double frac=ax-(double)t;long r;if(frac>0.5)r=t+1;else if(frac<0.5)r=t;else r=(t&1)?t+1:t;return x<0?-r:r;}
/* ROUND_NDIGITS_V1: round(x, n) devuelve FLOAT en CPython y redondea
 * half-even sobre la REPRESENTACION DECIMAL exacta del double (round(2.675, 2)
 * es 2.67, no 2.68). La ruta usa piton_float_fmt_fixed (aritmetica decimal
 * exacta, ver float_repr.h) y reinterpreta el texto, no x*10^n/10^n. */

/* M14 TYPE_CONVERSION_V1 + MATH_TIER1_V1 (matriz: ver native_runtime.c) */
static long piton_int_from_str(const char*s){if(!s){piton_raise_set("TypeError","int() argument must be a string");return 0;}while(*s==' '||*s=='\t'||*s=='\n')++s;int neg=0;if(*s=='-'||*s=='+'){neg=*s=='-';++s;}if(!*s||*s<'0'||*s>'9'){piton_raise_set("ValueError","invalid literal for int() with base 10");return 0;}long v=0;while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');++s;}while(*s==' '||*s=='\t'||*s=='\n')++s;if(*s){piton_raise_set("ValueError","invalid literal for int() with base 10");return 0;}return neg?-v:v;}
static long piton_float_from_str(const char*s){if(!s){piton_raise_set("TypeError","float() argument must be a string");return 0;}while(*s==' '||*s=='\t'||*s=='\n')++s;int neg=0;if(*s=='-'||*s=='+'){neg=*s=='-';++s;}const char*q=s;long ip=0;int has=0;while(*q>='0'&&*q<='9'){ip=ip*10+(*q-'0');++q;has=1;}double frac=0.0;double div=1.0;if(*q=='.'){++q;while(*q>='0'&&*q<='9'){frac=frac*10+(*q-'0');div*=10;++q;has=1;}}if(!has){piton_raise_set("ValueError","could not convert string to float");return 0;}while(*q==' '||*q=='\t'||*q=='\n')++q;if(*q){piton_raise_set("ValueError","could not convert string to float");return 0;}double v=(double)ip+frac/div;if(neg)v=-v;return piton_double_bits(v);}
/* OS_FSPATH_V1: os.fspath de un str es el mismo str (el backend no tiene
   objetos PathLike: esos llegan como error de tipo en runtime). */
static long piton_fspath_str(const char*s){return(long)s;}
static long piton_str_from_int(long v){char*p=piton_alloc(24);usize o=0;unsigned long u;if(v<0){p[o++]='-';u=(unsigned long)(-(v+1))+1;}else u=(unsigned long)v;char tmp[24];long n=0;do{tmp[n++]=(char)('0'+u%10);u/=10;}while(u);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static long piton_str_from_float(long bits){char*p=piton_alloc(PITON_REPR_MAX+1);int n=piton_repr_double(p,(unsigned long long)bits);p[n]=0;return(long)p;}
static long piton_str_truthy(const char*s){return(s&&s[0])?1:0;}
static long piton_math_floor_bits(long bits){double d=piton_bits_double(bits);long t=(long)d;if((double)t>d)--t;return t;}
static long piton_math_ceil_bits(long bits){double d=piton_bits_double(bits);long t=(long)d;if((double)t<d)++t;return t;}
static long piton_math_trunc_bits(long bits){double d=piton_bits_double(bits);return (long)d;}
static long piton_math_fabs_bits(long bits){return bits&0x7FFFFFFFFFFFFFFFL;}
static long piton_math_gcd(long a,long b){if(a<0)a=-a;if(b<0)b=-b;while(b){long t=a%b;a=b;b=t;}return a;}
static int piton_is_printable_str(const char*s){if(!s)return 0;int has=0;for(int i=0;i<256;++i){char c=s[i];if(c==0)return has;if((unsigned char)c<9||((unsigned char)c>13&&(unsigned char)c<32))return 0;has=1;}return 0;}
static int piton_is_double_bits(long bits){unsigned long u=(unsigned long)bits;unsigned long exp=(u>>52)&0x7FF;return(exp>=0x3CC&&exp<=0x434&&(u&0xFFFFFFFFFFFFFUL)!=0);}
/* INT_OF_VALUE_V1: int(x) con tipo estatico desconocido (_aix_support.py:
   int(build_date) donde build_date sale de sysconfig.get_config_var).
   Despacha por kind en runtime: double -> truncado, str -> parse, int ->
   identidad; cualquier otra cosa -> TypeError como CPython. */
static long piton_int_of_value(long bits){
  if(piton_is_double_bits(bits))return (long)piton_bits_double(piton_bits_double(bits));
  if(piton_maybe_ptr(bits)&&piton_is_printable_str((const char*)bits))return piton_int_from_str((const char*)bits);
  if(!piton_maybe_ptr(bits))return bits;
  piton_raise_set("TypeError","int() argument must be a string, a bytes-like object or a number");
  return 0;
}
static long piton_isinstance_dynamic(long bits,int kind){if(!bits)return(kind==PK_NONE);if(piton_is_double_bits(bits))return(kind==PK_FLOAT);if(bits>0x400000L&&bits<0x7FFFFFFFFFFFL){long*ptr=(long*)bits;long k=ptr[1];if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_OBJECT){return(k==kind);}if(piton_is_printable_str((const char*)bits)){return(kind==PK_STR);}}return(kind==PK_INT);}
/* SEQ_CMP_V1: comparación lexicográfica elemento a elemento (CPython). */
static long piton_seq_cmp(PitonSeq*a,PitonSeq*b){
  long n=a->length<b->length?a->length:b->length;
  for(long i=0;i<n;++i){
    PitonSlot x=a->items[i],y=b->items[i];
    if(piton_slot_eq(x,y))continue;
    /* escalares: comparar por valor; mixtos: fallar como CPython */
    if((x.kind==PK_INT||x.kind==PK_BOOL||x.kind==PK_FLOAT)&&
       (y.kind==PK_INT||y.kind==PK_BOOL||y.kind==PK_FLOAT)){
      double dx=(x.kind==PK_FLOAT)?piton_bits_double(x.bits):(double)x.bits;
      double dy=(y.kind==PK_FLOAT)?piton_bits_double(y.bits):(double)y.bits;
      return dx<dy?-1:(dx>dy?1:0);
    }
    if(x.kind==PK_STR&&y.kind==PK_STR){long c=piton_strcmp((const char*)x.bits,(const char*)y.bits);return c<0?-1:(c>0?1:0);}
    if((x.kind==PK_LIST||x.kind==PK_TUPLE)&&(y.kind==PK_LIST||y.kind==PK_TUPLE)){
      long c=piton_seq_cmp((PitonSeq*)x.bits,(PitonSeq*)y.bits);
      if(c)return c;
      continue;
    }
    piton_raise_set("TypeError","'<' not supported between instances of incompatible types");
    return 0;
  }
  if(a->length==b->length)return 0;
  return a->length<b->length?-1:1;
}
/* PRINT_SAFE_PTR_V1: ¿se puede desreferenciar `bits`? El binario es estático
 * no-PIE: la imagen (con .rodata) arranca en 0x400000 y el arena de PITÓN vive
 * justo después en .bss. Todo lo demás (enteros grandes como 2**30 o un hash)
 * NO es un puntero: desreferenciarlo era el SIGSEGV de print_auto. */
static int piton_maybe_ptr(long bits){
  /* PRINT_SAFE_PTR_V1: unión EXACTA de los buffers estáticos que producen
   * punteros (arena, heap de listas, buffer de bigint, buffer de concat) más
   * la imagen del binario (código + .rodata, por debajo de 1 MB). Cualquier
   * otra cosa —incluido un entero grande como 2**30 o 20000000— NO es un
   * puntero: desreferenciarlo era el SIGSEGV de print_auto. */
  if(bits<0x400000L)return 0;
  if(bits<0x100000L)return 1;                       /* imagen (rodata) */
  #define PITON_IN_BUF(a) (bits>=(long)(a)&&bits<(long)(a)+(long)sizeof(a))
  if(PITON_IN_BUF(piton_arena))return 1;
  if(PITON_IN_BUF(piton_heap))return 1;

  if(PITON_IN_BUF(piton_concat_buf))return 1;
  /* globals emitidos (static long ...) y tablas pequeñas: .bss bajo de 1 MB
   * inmediatamente después de la imagen ya es seguro: está mapeado. */
  if(bits>=0x400000L&&bits<0x480000L)return 1;
  #undef PITON_IN_BUF
  return 0;
}
/* OS_FSPATH_V1: nombre del tipo dinamico de un valor `bits` (misma politica
   que piton_isinstance_dynamic: puntero con cabecera -> secuencia/dict/
   set/objeto; puntero imprimible -> str; doble -> float; resto -> int). */
static const char* piton_dynamic_type_name(long bits){if(bits==(long)&piton_none_singleton)return "NoneType";if(bits==(long)&piton_ellipsis_singleton)return "ellipsis";
  if(!bits)return "NoneType";
  if(piton_is_double_bits(bits))return "float";
  if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_OBJECT){
      if(k==PK_LIST)return "list";if(k==PK_TUPLE)return "tuple";
      if(k==PK_DICT)return "dict";if(k==PK_SET)return "set";
      if(k==PK_OBJECT)return((PitonObject*)bits)->class_name;
      return "object";}
    if(piton_is_printable_str((const char*)bits))return "str";}
  return "int";
}
/* PCT_UNKNOWN_ARG_V1: `'%s' % x` con x de tipo DESCONOCIDO (un param sin
   inferencia) — CPython aplica str(x). La conversion es dinamica: se mira
   el valor en runtime (misma politica que piton_print_auto: puntero
   imprimible -> str, cabecera de coleccion -> su repr, doble -> float,
   enteros crudos -> int). Un valor que no cae en ninguno de esos casos se
   reporta con raise_set, no se imprime como puntero basura. */
static const char* piton_str_of_value(long bits){if(bits==(long)&piton_none_singleton)return "None";if(bits==(long)&piton_ellipsis_singleton)return "Ellipsis";
  if(!bits)return "None";
  if(piton_is_double_bits(bits))return (const char*)piton_str_from_float(bits);
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;
    if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<=PK_OBJECT){
      PitonSeq*s=(PitonSeq*)bits;
      if(s->kind==PK_LIST||s->kind==PK_TUPLE)return (const char*)piton_str_from_seq((PitonSeq*)bits);
      piton_raise_set("TypeError","%s format: unsupported collection value");
      return 0;
    }
    if(piton_is_printable_str((const char*)bits))return (const char*)bits;
  }
  return (const char*)piton_str_from_int(bits);
}
/* PCT_REPR_UNKNOWN_V1: `%r` de un valor sin tipo estatico (warnings.py:
   `"invalid action: %r" % (action,)` con action de parametro). Es el repr de
   runtime: un str va entre comillas (a diferencia de `%s`), las secuencias
   usan su repr y los escalares su forma decimal. Un bool que cruza el ABI
   como long es indistinguible de 0/1 y sale "1"/"0" — la misma
   representacion de bools que usa el resto del backend. dict/set/bytes
   dinamicos fallan cerrado (exit 2) en vez de divergir. */
static const char* piton_repr_of_value(long bits){if(bits==(long)&piton_none_singleton)return "None";if(bits==(long)&piton_ellipsis_singleton)return "Ellipsis";
  if(!bits)return "None";
  if(piton_is_double_bits(bits))return (const char*)piton_str_from_float(bits);
  /* el str va ANTES de la cabecera: un literal de rodata no siempre pasa
     piton_maybe_ptr, y su repr es el quoted. */
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;
    if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<PK_BIGINT){
      if(ptr[1]==PK_LIST||ptr[1]==PK_TUPLE)return (const char*)piton_str_from_seq((PitonSeq*)bits);
      piton_write(2,"TypeError: %r of a dynamically typed collection is not supported in the native subset\n",94);
      piton_exit(2);
    }
  }
  {long b2=bits;if(!b2)return "None";
   if(piton_maybe_ptr(b2)&&piton_is_printable_str((const char*)b2))return (const char*)piton_str_quote((const char*)b2);
   if(b2>=0x400000L&&b2<0x480000L)return "None";}
  return (const char*)piton_str_from_int(bits);
}
/* LEN_OF_VALUE_V1: len() de un valor en runtime (para map(len, xs)).
   bits es el campo bits de un PitonSlot. Retorna la longitud como long
   (bits de un int PK_INT). */
static long piton_len_of_value(long bits){
  if(bits==(long)&piton_none_singleton)return 0;
  if(bits==(long)&piton_ellipsis_singleton)return 0;
  if(piton_is_double_bits(bits))return 0;
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;
    if(ptr[0]>0&&ptr[0]<1000000L){
      long k=ptr[1];
      if((k>=PK_LIST&&k<=PK_SET)||k==PK_BYTES||k==PK_BYTEARRAY)return ptr[2];
      /* asume string: puntero imprimible */
      if(piton_is_printable_str((const char*)bits))return piton_strlen((const char*)bits);
    }
  }
  return -1;
}
static void piton_print_int_safe(long bits){if(bits==(long)&piton_none_singleton){piton_write(piton_print_fd,"None",4);return;}if(bits==(long)&piton_ellipsis_singleton){piton_write(piton_print_fd,"Ellipsis",8);return;}
  /* PRINT_INT_V1: un entero cuyo tipo estático es int pero que en runtime es
   * un puntero (huecos de inferencia) se imprime como string; si no, como int.
   * PRINT_DYNAMIC_KIND_V1: si el puntero lleva cabecera de coleccion (el valor
   * viene de un slice/llamada de tipo dinamico) se imprime su repr con el
   * mismo criterio que piton_print_auto, no como numero. */
  if(piton_maybe_ptr(bits)){
    if(piton_is_printable_str((const char*)bits)){piton_print_str_raw((const char*)bits);return;}
    {long*ptr=(long*)bits;long k=ptr[1];
     if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW){PitonSlot sl={bits,(int)k};piton_print_slot(sl);return;}}
  }
  piton_write_int(bits);
}
static void piton_print_auto(long bits){if(bits==(long)&piton_none_singleton){piton_write(piton_print_fd,"None",4);return;}if(bits==(long)&piton_ellipsis_singleton){piton_write(piton_print_fd,"Ellipsis",8);return;}if(bits==0){piton_write(piton_print_fd,"0",1);return;}if(piton_is_double_bits(bits)){piton_print_float_bits_raw(bits);return;}if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_OBJECT){PitonSlot sl={bits,(int)k};piton_print_slot(sl);return;}if(piton_is_printable_str((const char*)bits)){piton_print_str_raw((const char*)bits);return;}}piton_print_int_raw(bits);}
/* SLOT_GUESS_V1: la kind de un valor que cruza un ABI sin slot (un metodo
   ligado builtin recibe `long`). Se deriva de la cabecera con la misma
   politica que piton_dynamic_type_name; el resto es int. NO inventa un tipo:
   un valor que no es puntero con cabecera ni str imprimible es un entero. */
static PitonSlot piton_slot_guess(long bits){
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW)return (PitonSlot){bits,(int)k};
    if(piton_is_printable_str((const char*)bits))return (PitonSlot){bits,PK_STR};
  }
  return (PitonSlot){bits,PK_INT};
}
/* SEQ_SORT_KEY_V1: lista.sort(key=f) — decora con la clave (UNA invocación
   por elemento, como CPython: piton_sorted_key_new), ordena estable por la
   clave comparando piton_slot_less sobre la kind derivada (str primero, para
   que las claves string comparen lexicográficamente) y escribe los originales
   de vuelta en la lista (sort es in-place). */
static PitonSlot piton_sort_key_slot(long bits){
  /* Derivacion de kind para una CLAVE de sort: STR primero (un literal de
     rodata como 'b' tiene bytes basura en ptr[1] que piton_slot_guess leia
     como kind de coleccion). Luego cabecera de coleccion, luego int. */
  if(piton_is_double_bits(bits))return (PitonSlot){bits,PK_FLOAT};
  if(piton_maybe_ptr(bits)){
    if(piton_is_printable_str((const char*)bits))return (PitonSlot){bits,PK_STR};
    long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW)return (PitonSlot){bits,(int)k};
  }
  return (PitonSlot){bits,PK_INT};
}
static void piton_seq_sort_key(PitonSeq*s,long keyfn){
  if(!s||s->length<2)return;
  if(!keyfn){piton_raise_set("TypeError","sort() key must be callable");return;}
  long n=s->length;
  PitonSeq*pairs=piton_seq_new(PK_LIST,n*2);
  for(long i=0;i<n;++i){
    PitonSlot orig=s->items[i];
    long k;
    {long _ka[1];_ka[0]=orig.bits;k=piton_closure_call_frame(keyfn,1,_ka);}
    if(PITON_EXC_FLAG)return;
    pairs->items[i*2]=piton_sort_key_slot(k);
    pairs->items[i*2+1]=orig;
  }
  for(long i=1;i<n;++i){
    PitonSlot kk=pairs->items[i*2],ov=pairs->items[i*2+1];
    long j=i;
    while(j>0){
      int l=piton_slot_less(kk,pairs->items[(j-1)*2]);
      if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}
      if(l){pairs->items[j*2]=pairs->items[(j-1)*2];pairs->items[j*2+1]=pairs->items[(j-1)*2+1];--j;}else break;
    }
    pairs->items[j*2]=kk;pairs->items[j*2+1]=ov;
  }
  for(long i=0;i<n;++i)s->items[i]=pairs->items[i*2+1];
}
static void piton_seq_sort_key_rev(PitonSeq*s,long keyfn){
  piton_seq_sort_key(s,keyfn);
  if(!s)return;
  for(long i=0,j=s->length-1;i<j;++i,--j){PitonSlot t=s->items[i];s->items[i]=s->items[j];s->items[j]=t;}
}
/* BUILTIN_BOUND_METHOD_V1: `m = xs.append; m(v)` — el metodo ligado de un
   builtin no existia (AttributeError en runtime). El ABI de bound method pasa
   longs, asi que la kind se deriva en runtime. */
static long piton_list_append_bound(long self,long v){piton_seq_append((PitonSeq*)self,piton_slot_guess(v));return 0;}
/* ITER_DYNAMIC_V1: `iter(x)` con x sin tipo estatico (heapq:
   `map(iter, iterables)` con iterables de parametro). Las secuencias
   list/tuple se iteran; str/bytes/dict/set/range, que CPython tambien itera
   pero con otra forma de elemento, fallan cerrado con exit 2 en vez de
   divergir; un no-iterable levanta el TypeError de CPython (exit 1). */
static long piton_iter_dynamic(long bits){
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L){
      if(k==PK_LIST||k==PK_TUPLE)return piton_iterator_new_any((void*)bits,(int)k);
      if(k==PK_DICT||k==PK_SET||k==PK_FROZENSET||k==PK_RANGE||k==PK_BYTES||k==PK_BYTEARRAY){
        piton_write(2,"TypeError: iter() of a dynamically typed collection is not supported in the native subset\n",97);
        piton_exit(2);
      }
    }
    if(piton_is_printable_str((const char*)bits)){
      piton_write(2,"TypeError: iter() of a dynamically typed str is not supported in the native subset\n",91);
      piton_exit(2);
    }
  }
  piton_write(2,"TypeError: object is not iterable\n",33);
  piton_exit(1);
  return 0;
}
/* UNPACK_DYNAMIC_V1: el valor a desestructurar no tiene tipo estatico
   (sqlite3/dump.py: `for a, b, c in cursor.fetchall()`). La aridad la decide
   la cabecera; un str/dict/set dinamico (que CPython tambien desestructura)
   falla cerrado con exit 2 en vez de leer memoria como si fuera una tupla, y
   un no-iterable levanta el TypeError de CPython. */
static long piton_unpack_len_dynamic(long bits){
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L){
      if(k==PK_LIST||k==PK_TUPLE)return ((PitonSeq*)bits)->length;
      if(k==PK_DICT||k==PK_SET||k==PK_FROZENSET){
        piton_write(2,"TypeError: unpack of a dynamically typed set/dict is not supported in the native subset\n",100);
        piton_exit(2);
      }
      if(k>=PK_BIGINT&&k<=PK_MEMORYVIEW){
        piton_write(2,"TypeError: unpack of a dynamically typed scalar is not supported in the native subset\n",94);
        piton_exit(2);
      }
    }
    if(piton_is_printable_str((const char*)bits)){
      piton_write(2,"TypeError: unpack of a dynamically typed str is not supported in the native subset\n",92);
      piton_exit(2);
    }
  }
  piton_raise_set("TypeError","cannot unpack non-iterable object");
  return -1;
}
/* STR_PREFIX_DYNAMIC_V1: `s.startswith(p)` / `s.endswith(p)` con p sin tipo
   estatico (warnings.py: el prefijo viene de un parametro). CPython exige un
   str: un valor que no lo es levanta el MISMO TypeError, no divergencia. El
   orden importa: un puntero menor que la imagen (0x400000) es un entero, y
   solo se desreferencia una direccion que esta dentro de la imagen o de los
   buffers de PITON. */
static long piton_str_prefix_dyn(long self,long v,int end){
  const char*msg=end?"endswith":"startswith";
  /* Convencion del modulo (ver el docstring de _emit_str_method): un metodo
     de str reporta y sale, no levanta bandera capturable. */
  /* Resuelve self: puede ser str (const char*) o bytes (PitonBytes* con .data) */
  const char*self_data;
  if(piton_maybe_ptr(self)){
    long*s=(long*)self;
    if(s[0]>0&&s[0]<1000000L&&s[1]==PK_BYTES){
      self_data=(const char*)((PitonBytes*)self)->data;
    }else{
      self_data=(const char*)self;
    }
  }else{
    self_data=(const char*)self;
  }
  /* Resuelve v: str, bytes (PitonBytes*), o escalar */
  const char*v_data;
  if(!v){piton_raise_set("TypeError","startswith first arg must be str or a tuple of str, not None");piton_report_unhandled();piton_exit(1);}
  if(!piton_maybe_ptr(v)){
    if(v<0x400000L){piton_raise_set("TypeError","startswith first arg must be str or a tuple of str, not int");piton_report_unhandled();piton_exit(1);}
    if(!piton_is_printable_str((const char*)v)){piton_raise_set("TypeError","startswith first arg must be str or a tuple of str, not int");piton_report_unhandled();piton_exit(1);}
    v_data=(const char*)v;
  }else{
    long*ptr=(long*)v;
    if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<=PK_OBJECT){
      piton_raise_set("TypeError","startswith first arg must be str or a tuple of str, not a collection");
      piton_report_unhandled();piton_exit(1);
    }
    if(ptr[1]==PK_BYTES){
      v_data=(const char*)((PitonBytes*)v)->data;
    }else if(piton_is_printable_str((const char*)v)){
      v_data=(const char*)v;
    }else{
      piton_raise_set("TypeError","startswith first arg must be str or a tuple of str, not bytes");piton_report_unhandled();piton_exit(1);}
  }
  return end?piton_str_endswith(self_data,v_data)
             :piton_str_startswith(self_data,v_data);
}
/* LIST_OF_DYNAMIC_V1: `lista(x)` con x sin tipo estatico (warnings.py:
   `lista(traceback.extract())` con el retorno desconocido). Se decide por la
   cabecera con la misma politica que los del resto de dinamicos: list/tuple/str
   se copian conservando la kind de cada elemento; un dict/set/range, que
   CPython tambien convierte, falla cerrado con exit 2; un no-iterable levanta
   el TypeError de CPython. */
static long piton_list_of_dynamic(long bits){
  if(!bits){piton_raise_set("TypeError","'NoneType' object is not iterable");return 0;}
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW){
      if(k==PK_LIST||k==PK_TUPLE)return piton_conv_seq(PK_LIST,(PitonSlot){bits,(int)k});
      if(k==PK_SET){
        /* LIST_OF_SET_V1: lista(set) y sorted(set) convierten el set a lista. */
        PitonSet*st=(PitonSet*)bits;
        PitonSeq*lst=piton_seq_new(PK_LIST,0);
        for(long i=0;i<st->length;++i)piton_seq_append(lst,st->items[i]);
        return (long)lst;
      }
      piton_write(2,"TypeError: list() of a dynamically typed collection is not supported in the native subset\n",99);
      piton_exit(2);
    }
    /* un str imprimible (heap o literal de rodata, que maybe_ptr acepta):
       list('abc') -> ['a','b','c'] conservando la kind. */
    if(piton_is_printable_str((const char*)bits))return piton_conv_seq(PK_LIST,(PitonSlot){bits,PK_STR});
  }
  if(piton_is_double_bits(bits)){piton_raise_set("TypeError","'float' object is not iterable");return 0;}
  piton_raise_set("TypeError","'int' object is not iterable");
  return 0;
}
typedef struct{long magic;const char*path;const char*mode;long fd;PitonBytes*data;long pos;long closed;}PitonFile;
#define PITON_FILE_MAGIC 0x50495446566CL
/* syscalls: open(2) = 2, read = 0, write = 1, close = 3 (x86-64 freestanding). */
static long piton_sys_open(const char*path,long flags,long mode){long r;__asm__ volatile("syscall":"=a"(r):"a"(2L),"D"(path),"S"(flags),"d"(mode):"rcx","r11","memory");return r;}
static long piton_sys_close(long fd){long r;__asm__ volatile("syscall":"=a"(r):"a"(3L),"D"(fd):"rcx","r11","memory");return r;}
static long piton_sys_read(long fd,void*buf,long n){long r;__asm__ volatile("syscall":"=a"(r):"a"(0L),"D"(fd),"S"(buf),"d"(n):"rcx","r11","memory");return r;}
static long piton_sys_lseek(long fd,long off,long whence){long r;__asm__ volatile("syscall":"=a"(r):"a"(8L),"D"(fd),"S"(off),"d"(whence):"rcx","r11","memory");return r;}
static long piton_unlink(const char*path){return 0;}
static PitonFile* piton_file_new(const char*path,const char*mode,long fd,PitonBytes*data){
  PitonFile*f=(PitonFile*)piton_alloc(sizeof(PitonFile));
  f->magic=PITON_FILE_MAGIC;f->path=path;f->mode=mode;f->fd=fd;f->data=data;f->pos=0;f->closed=0;return f;}
/* FILE_OPEN_V1: abre de verdad. Devuelve el handle o levanta el OSError de
   CPython (IsADirectoryError / PermissionError / FileNotFoundError) con el
   errno en el mensaje: el runtime no tiene strerror, pero el numero de errno
   si viaja. */
static PitonFile* piton_file_open(const char*path,const char*mode){
  int write=0,create=0,truncate=0,append=0,plus=0;
  for(const char*p=mode;*p;++p){if(*p=='w'){write=1;create=1;truncate=1;}else if(*p=='a'){write=1;create=1;append=1;}
    else if(*p=='+'){plus=1;}
    else if(*p=='x'){create=1;}
    else if(*p=='r'){write=0;}else if(*p=='b'||*p=='t'){}else{piton_write(2,"ValueError: invalid mode\n",26);piton_exit(1);}}
  /* FILE_PLUS_V1: '+' abre lectura+escritura (O_RDWR=2), no solo escritura
     (_aix_support.py: abrir(path,'w+b')). */
  long flags=(plus?2L:(write?1L:0L))|(create?0100L:0L)|(truncate?01000L:0L)|(append?02000L:0L);
  long fd=piton_sys_open(path,flags,0644);
  if(fd<0){
    if(fd==-2){piton_raise_set("FileNotFoundError","[Errno 2] No such file or directory");}
    else if(fd==-13){piton_raise_set("PermissionError","[Errno 13] Permission denied");}
    else if(fd==-21){piton_raise_set("IsADirectoryError","[Errno 21] Is a directory");}
    else{piton_raise_set("OSError","[Errno] open failed");}
    piton_report_unhandled();piton_exit(1);return 0;}
  PitonBytes*data=0;
  if(!write){
    long end=piton_sys_lseek(fd,0,2);if(end<0)end=0;
    piton_sys_lseek(fd,0,0);
    usize cap=(usize)(end>0?end:16);
    char*buf=(char*)piton_alloc(cap+1);long total=0;
    for(;;){long n=piton_sys_read(fd,buf+total,(long)(cap-(usize)total));if(n<=0)break;total+=n;}
    buf[total>0?total:0]=0;
    data=(PitonBytes*)piton_bytes_new((usize)(total>0?total:0),buf);
  }
  return piton_file_new(path,mode,fd,data);
}
/* FILE_OPEN_DYN_V1: `abrir(x)` con x sin tipo estatico (netrc: la ruta que
   devuelve os.path.expanduser). Si x no es un str, CPython levanta TypeError:
   aqui se hace lo mismo, no se desreferencia un entero. */
static PitonFile* piton_file_open_dyn(long bits,const char*mode){
  if(!bits||bits==(long)&piton_none_singleton){piton_raise_set("TypeError","expected str, not NoneType");piton_report_unhandled();piton_exit(1);return 0;}
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;
    if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<=PK_MEMORYVIEW){
      piton_raise_set("TypeError","expected str, not a collection");piton_report_unhandled();piton_exit(1);return 0;}
    if(piton_is_printable_str((const char*)bits))return piton_file_open((const char*)bits,mode);
  }
  if(piton_is_double_bits(bits)){piton_raise_set("TypeError","expected str, not float");piton_report_unhandled();piton_exit(1);}
  piton_raise_set("TypeError","expected str, not int");piton_report_unhandled();piton_exit(1);
  return 0;
}
static usize piton_file_size(PitonFile*f){return f->data?(usize)f->data->length:0;}
/* FILE_READ_V1: read() sin argumento lee todo lo que queda; read(n) n BYTES.
   El modo por defecto es texto (como CPython), asi que devuelve str: el mismo
   criterio NUL-terminado que usa el resto del subconjunto (un NUL embebido
   truncaria, limitacion documentada, no divergencia silenciosa). */
static const char* piton_file_read(PitonFile*f,long n){
  if(!f||f->closed||!f->data){piton_raise_set("ValueError","I/O operation on closed file");piton_report_unhandled();piton_exit(1);}
  usize avail=piton_file_size(f)-(usize)f->pos;
  usize want=n<0?avail:((usize)n<avail?(usize)n:avail);
  char*out=(char*)piton_alloc(want+1);
  for(usize i=0;i<want;++i)out[i]=((char*)(long)f->data->data)[f->pos++];
  out[want]=0;
  return out;
}
/* FILE_READLINE_V1: hasta '\n' inclusivo (o EOF). */
static const char* piton_file_readline(PitonFile*f){
  if(!f||f->closed||!f->data){piton_raise_set("ValueError","I/O operation on closed file");piton_report_unhandled();piton_exit(1);}
  usize total=piton_file_size(f);
  const char*base=(const char*)(long)f->data->data;
  usize start=(usize)f->pos,i=start;
  while(i<total&&base[i]!='\n')++i;
  if(i<total)++i;
  usize want=i-(usize)f->pos;
  char*out=(char*)piton_alloc(want+1);
  for(usize k=0;k<want;++k)out[k]=base[(usize)f->pos+k];
  out[want]=0;f->pos=(long)i;
  return out;
}
/* FILE_CLOSE_V1 / FILE_WRITE_V1 / FILE_ITER_*: cierre, escritura por descriptor
   e iteracion linea a linea como CPython. */
static void piton_file_close(PitonFile*f){if(!f||f->closed)return;if(f->fd>=0&&f->mode&&(f->mode[0]=='w'||f->mode[0]=='a'))piton_sys_close(f->fd);f->closed=1;}
static long piton_file_write(PitonFile*f,const char*s){
  if(!f||f->closed){piton_raise_set("ValueError","I/O operation on closed file");piton_report_unhandled();piton_exit(1);}
  if(!f->mode||(f->mode[0]!='w'&&f->mode[0]!='a')){piton_raise_set("io.UnsupportedOperation","not writable");piton_report_unhandled();piton_exit(1);}
  usize n=piton_strlen(s);long w=piton_write(f->fd,s,n);return(long)w;}
static long piton_file_has_more(PitonFile*f){return f&&f->data&&!f->closed&&(usize)f->pos<piton_file_size(f);}
static const char* piton_file_next_line(PitonFile*f){return piton_file_readline(f);}
static const char* piton_file_to_str(PitonFile*f){return f->path;}
/* JOIN_OF_DYNAMIC_V1: `sep.join(x)` con x sin tipo estatico. CPython acepta
   cualquier iterable: una lista o tupla se une directo; un str se parte en
   caracteres y se une (",".join("ab") == "a,b"); cualquier otra cosa levanta el
   TypeError de CPython. dict/set/range (que CPython tambien recorre) fallan
   cerrados con exit 2: su orden de iteracion no es el de una secuencia. */
static long piton_str_join_dyn(const char*sep,long bits){
  if(!bits){piton_raise_set("TypeError","can only join an iterable");piton_report_unhandled();piton_exit(1);return 0;}
  if(piton_maybe_ptr(bits)){
    long*ptr=(long*)bits;
    if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<=PK_MEMORYVIEW){
      if(ptr[1]==PK_LIST||ptr[1]==PK_TUPLE)return piton_str_join(sep,(PitonSeq*)bits);
      piton_write(2,"TypeError: join of a dynamically typed collection is not supported in the native subset\n",99);
      piton_exit(2);return 0;
    }
    if(piton_is_printable_str((const char*)bits)){
      const char*src=(const char*)bits;usize n=piton_strlen(src);
      PitonSeq*parts=piton_seq_new(PK_LIST,n);
      for(usize i=0;i<n;++i){char*c=(char*)piton_alloc(2);c[0]=src[i];c[1]=0;piton_seq_put(parts,i,(PitonSlot){(long)c,PK_STR});}
      return piton_str_join(sep,parts);
    }
  }
  piton_raise_set("TypeError","can only join an iterable");piton_report_unhandled();piton_exit(1);
  return 0;
}
/* INPUT_V1: input([prompt]) nativo. El prompt se escribe a stdout (sin
   salto de linea, como CPython) y se lee UNA linea del fd 0. EOF sin datos
   levanta EOFError (exit 1); la linea devuelta no lleva el '\\n' final. */
static const char* piton_input_read(const char*prompt){
  if(prompt&&*prompt)piton_write(piton_print_fd,prompt,piton_strlen(prompt));
  static char inbuf[16384];
  long total=0;long got=0;
  for(;;){
    char c;long n=piton_sys_read(0,&c,1);
    if(n<=0)break;
    got=1;
    if(c=='\n')break;
    if(total<(long)sizeof(inbuf)-1)inbuf[total++]=c;
  }
  inbuf[total]=0;
  if(!got){piton_raise_set("EOFError","EOF when reading a line");piton_report_unhandled();piton_exit(1);}
  return inbuf;
}
static int piton_exc_flag=0;static const char*piton_exc_type=0;static const char*piton_exc_message=0;
/* RAISE_VALUE_V1: `raise <instancia>` — el valor Levantado viaja al runtime
   (el backend solo llevaba el NOMBRE del tipo y el mensaje). */
static long piton_exc_value=0;
static const char*piton_exc_cause_type=0;static const char*piton_exc_cause_msg=0;
/* EXC_ARGS_MULTI_V1: tupla .args de la excepcion activa (0 = sin multi-arg,
 * .args se materializa como (mensaje,) como antes). */
static long piton_exc_args=0;
static void piton_raise_set(const char*type,const char*message){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=message;piton_exc_args=0;}
/* EXC_ARGS_MULTI_V1: raise con 2+ argumentos -> la tupla .args completa. */
static void piton_raise_set_args(const char*type,const char*message,long args_tuple){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=message;piton_exc_args=args_tuple;}
/* RAISE_VALUE_V1: levantar una INSTANCIA (no una clase): el tipo sale del
   class_name del objeto y el valor queda disponible para el handler. */
static void piton_raise_set_value(long value){piton_exc_flag=1;piton_exc_value=value;piton_exc_type="Exception";piton_exc_message=0;piton_exc_args=0;if(value&&piton_maybe_ptr(value)){long*ptr=(long*)value;if(ptr[0]>0&&ptr[0]<1000000L&&ptr[1]>=PK_LIST&&ptr[1]<=PK_OBJECT){piton_exc_type=((PitonObject*)value)->class_name;}}}
/* OS_FSPATH_V1: TypeError de os.fspath con el texto EXACTO de CPython:
   "expected str, bytes or os.PathLike object, not <tipo>". */
static void piton_raise_fspath_type(long bits){
  piton_raise_set("TypeError","expected str, bytes or os.PathLike object, not ");
  piton_exc_message=(const char*)piton_str_concat(piton_exc_message,piton_dynamic_type_name(bits));
}
static long piton_int_add(long a,long b){i64 r;if(__builtin_add_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_sub(long a,long b){i64 r;if(__builtin_sub_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_mul(long a,long b){i64 r;if(__builtin_mul_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_truediv(long a,long b){if(b==0){piton_raise_set("ZeroDivisionError","division by zero");return 0;}return piton_double_bits((double)a/(double)b);}
static long piton_float_div(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float division by zero");return 0;}return piton_double_bits(x/y);}
static long piton_float_floor_div(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float floor division by zero");return 0;}return piton_double_bits(__builtin_floor(x/y));}
static void piton_raise_chain_set(const char*type,const char*message,const char*cause_type,const char*cause_msg){piton_exc_cause_type=cause_type;piton_exc_cause_msg=cause_msg;piton_raise_set(type,message);}
static const char*piton_reraise_type=0;static const char*piton_reraise_message=0;
/* EXC_ARGS_MULTI_V1: tupla de 1 elemento (mensaje) para .args por defecto. */
static long piton_tuple1(const char*msg){PitonSeq*p=piton_seq_new(PK_TUPLE,1);p->items[0]=(PitonSlot){(long)msg,PK_STR};return(long)p;}
static void piton_reraise_save(void){piton_reraise_type=piton_exc_type;piton_reraise_message=piton_exc_message;}
static void piton_reraise_set(const char*type){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=piton_reraise_message;piton_exc_args=0;}
static int piton_exc_flag_get(void){return piton_exc_flag;}
static void piton_catch_clear(void){piton_exc_flag=0;piton_exc_type=0;piton_exc_message=0;piton_exc_cause_type=0;piton_exc_cause_msg=0;
  /* EXC_ARGS_MULTI_V1: piton_exc_args NO se limpia aqui — el cuerpo del
   * handler lee e.args despues del clear. Cada raise lo re-setea. */
}
/* SEQ_EXTEND_V1: extiende dst con todos los elementos de src (cualquier iterable).
   Usado para `[*a, b]` donde a es un iterable (starred en lista/tupla/conjunto). */
static void piton_seq_extend_any(PitonSeq*dst,long src_bits){
  if(!piton_maybe_ptr(src_bits))return;
  long*ptr=(long*)src_bits;long k=ptr[1];
  if(k==PK_LIST||k==PK_TUPLE){
    PitonSeq*s=(PitonSeq*)src_bits;
    for(long i=0;i<s->length;++i)piton_seq_append(dst,s->items[i]);
  }else if(k==PK_SET){
    PitonSet*st=(PitonSet*)src_bits;
    for(long i=0;i<st->length;++i)piton_seq_append(dst,st->items[i]);
  }else{
    /* Iterador generico para otros tipos (range, objetos con __iter__, etc.) */
    long it=piton_iterator_new_any((void*)src_bits,k);
    for(;;){
      long v=piton_iterator_next_any(it);
      if(piton_exc_flag){
        if(piton_exc_type&&piton_strcmp(piton_exc_type,"StopIteration")==0){
          piton_catch_clear();
          break;
        }
        return;
      }
      piton_seq_append(dst,(PitonSlot){v,PK_INT});
    }
  }
}
static void piton_seq_set(PitonSeq*s,long i,PitonSlot v){if(!s)return;if(i<0)i+=s->length;if(i<0||i>=s->length){piton_raise_set("IndexError","seq index out of range");return;}s->items[i]=v;}
static void piton_dict_set(PitonDict*d,PitonSlot k,PitonSlot v){if(!d)return;for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k)){d->items[i].value=v;return;}piton_dict_append(d,k,v);}
static void piton_dict_update(PitonDict*d,PitonDict*src){if(!d||!src)return;for(long i=0;i<src->length;++i)piton_dict_set(d,src->items[i].key,src->items[i].value);}
static void piton_dict_del(PitonDict*d,PitonSlot k){for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k)){for(long j=i;j+1<d->length;++j)d->items[j]=d->items[j+1];--d->length;return;}piton_raise_set("KeyError","");}
static void piton_seq_remove(PitonSeq*s,PitonSlot v){if(!s)return;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v)){for(long j=i;j<s->length-1;++j)s->items[j]=s->items[j+1];--s->length;return;}piton_raise_set("ValueError","list.remove(x): x not in list");}
static void piton_seq_extend(PitonSeq*s,PitonSeq*o){if(!s||!o)return;for(long i=0;i<o->length;++i)piton_seq_append(s,o->items[i]);}
static void piton_seq_clear(PitonSeq*s){if(s)s->length=0;}
static long piton_seq_copy(PitonSeq*s){if(!s)return 0;PitonSeq*r=piton_seq_new((int)s->kind,s->length);for(long i=0;i<s->length;++i)r->items[i]=s->items[i];return(long)r;}
static int piton_set_index_of(PitonSet*s,PitonSlot v){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return (int)i;return -1;}
static void piton_set_discard(PitonSet*s,PitonSlot v){int i=s?piton_set_index_of(s,v):-1;if(i>=0){for(long j=i;j<s->length-1;++j)s->items[j]=s->items[j+1];--s->length;}}
static void piton_set_remove(PitonSet*s,PitonSlot v){int i=s?piton_set_index_of(s,v):-1;if(i<0){piton_raise_set("KeyError","");return;}for(long j=i;j<s->length-1;++j)s->items[j]=s->items[j+1];--s->length;}
static long piton_seq_index(PitonSeq*s,PitonSlot v){if(s){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return i;}piton_raise_set("ValueError","sequence.index(x): x not in sequence");return 0;}
/* SEQ_INDEX_RANGE_V1: list.index(x[, start[, stop]]) — busqueda en el
   rango [start, stop) como CPython (datastructures: index(x, 4)). */
static long piton_seq_index_range(PitonSeq*s,PitonSlot v,long start,long stop){
  if(!s){piton_raise_set("ValueError","sequence.index(x): x not in sequence");return 0;}
  long n=s->length;
  if(start<0)start+=n;if(stop<0)stop+=n;
  if(start<0)start=0;if(stop>n)stop=n;
  for(long i=start;i<stop;++i)if(piton_slot_eq(s->items[i],v))return i;
  piton_raise_set("ValueError","sequence.index(x): x not in sequence");
  return 0;
}
static void piton_dict_clear(PitonDict*d){if(d)d->length=0;}
static long piton_dict_copy(PitonDict*d){if(!d)return 0;PitonDict*r=piton_dict_new(d->length);r->length=d->length;for(long i=0;i<d->length;++i)r->items[i]=d->items[i];return(long)r;}
static PitonSlot piton_dict_pop(PitonDict*d,PitonSlot k,int has_def,PitonSlot def_val){if(d){for(long i=0;i<d->length;++i){if(piton_slot_eq(d->items[i].key,k)){PitonSlot val=d->items[i].value;for(long j=i;j+1<d->length;++j)d->items[j]=d->items[j+1];--d->length;return val;}}}if(has_def)return def_val;piton_raise_set("KeyError","");return(PitonSlot){0,PK_NONE};}
static PitonSlot piton_dict_setdefault(PitonDict*d,PitonSlot k,PitonSlot def_val){if(d){for(long i=0;i<d->length;++i){if(piton_slot_eq(d->items[i].key,k))return d->items[i].value;}piton_dict_append(d,k,def_val);}return def_val;}
static long piton_dict_popitem(PitonDict*d){if(!d||d->length<=0){piton_raise_set("KeyError","popitem(): dictionary is empty");return 0;}PitonDictEntry entry=d->items[--d->length];PitonSeq*p=piton_seq_new(PK_TUPLE,2);p->items[0]=entry.key;p->items[1]=entry.value;return(long)p;}
static void piton_set_clear(PitonSet*s){if(s)s->length=0;}
static long piton_set_copy(PitonSet*s){if(!s)return 0;PitonSet*r=piton_set_new(s->length);r->length=s->length;for(long i=0;i<s->length;++i)r->items[i]=s->items[i];return(long)r;}
static PitonSlot piton_set_pop(PitonSet*s){if(!s||s->length<=0){piton_raise_set("KeyError","pop from an empty set");return(PitonSlot){0,PK_NONE};}return s->items[--s->length];}
static long piton_set_union(PitonSet*a,PitonSet*b){PitonSet*r=piton_set_new((a?a->length:0)+(b?b->length:0));if(a)for(long i=0;i<a->length;++i)piton_set_add(r,a->items[i]);if(b)for(long i=0;i<b->length;++i)piton_set_add(r,b->items[i]);return(long)r;}
static long piton_set_intersection(PitonSet*a,PitonSet*b){PitonSet*r=piton_set_new(a?a->length:0);if(a&&b){for(long i=0;i<a->length;++i)if(piton_set_contains(b,a->items[i]))piton_set_add(r,a->items[i]);}return(long)r;}
static long piton_set_difference(PitonSet*a,PitonSet*b){PitonSet*r=piton_set_new(a?a->length:0);if(a){for(long i=0;i<a->length;++i)if(!b||!piton_set_contains(b,a->items[i]))piton_set_add(r,a->items[i]);}return(long)r;}
static long piton_set_sym_diff(PitonSet*a,PitonSet*b){PitonSet*r=piton_set_new((a?a->length:0)+(b?b->length:0));if(a){for(long i=0;i<a->length;++i)if(!b||!piton_set_contains(b,a->items[i]))piton_set_add(r,a->items[i]);}if(b){for(long i=0;i<b->length;++i)if(!a||!piton_set_contains(a,b->items[i]))piton_set_add(r,b->items[i]);}return(long)r;}
static long piton_set_issubset(PitonSet*a,PitonSet*b){if(!a)return 1;if(!b)return a->length==0;for(long i=0;i<a->length;++i)if(!piton_set_contains(b,a->items[i]))return 0;return 1;}
static long piton_set_issuperset(PitonSet*a,PitonSet*b){return piton_set_issubset(b,a);}
static long piton_set_isdisjoint(PitonSet*a,PitonSet*b){if(!a||!b)return 1;for(long i=0;i<a->length;++i)if(piton_set_contains(b,a->items[i]))return 0;return 1;}
static void piton_set_update(PitonSet*a,PitonSet*b){if(a&&b)for(long i=0;i<b->length;++i)piton_set_add(a,b->items[i]);}
/* SET_INPLACE_V1: variantes in-place de las operaciones de conjunto. */
static void piton_set_intersection_update(PitonSet*a,PitonSet*b){
  if(!a)return;
  if(!b){a->length=0;return;}
  long w=0;
  for(long i=0;i<a->length;++i)if(piton_set_contains(b,a->items[i]))a->items[w++]=a->items[i];
  a->length=w;
}
static void piton_set_difference_update(PitonSet*a,PitonSet*b){
  if(!a)return;
  if(!b)return;
  long w=0;
  for(long i=0;i<a->length;++i)if(!piton_set_contains(b,a->items[i]))a->items[w++]=a->items[i];
  a->length=w;
}
static void piton_set_symmetric_difference_update(PitonSet*a,PitonSet*b){
  if(!a||!b)return;
  /* diferencia simétrica = (a - b) U (b - a). Se construye el resultado en un
   * conjunto nuevo y se copia de vuelta a `a` (que conserva su identidad). */
  PitonSet*r=piton_set_new(a->length+b->length);
  for(long i=0;i<a->length;++i)if(!piton_set_contains(b,a->items[i]))piton_set_add(r,a->items[i]);
  for(long i=0;i<b->length;++i)if(!piton_set_contains(a,b->items[i]))piton_set_add(r,b->items[i]);
  a->length=0;
  for(long i=0;i<r->length;++i)piton_set_add(a,r->items[i]);
}
static long piton_str_removeprefix(const char*s,const char*prefix){if(!s||!prefix)return(long)s;usize pl=piton_strlen(prefix),sl=piton_strlen(s);if(sl>=pl&&piton_memcmp(s,prefix,pl)==0){usize rem=sl-pl;char*r=piton_alloc(rem+1);piton_memcpy(r,s+pl,rem);r[rem]=0;return(long)r;}char*r=piton_alloc(sl+1);piton_memcpy(r,s,sl);r[sl]=0;return(long)r;}
static long piton_str_removesuffix(const char*s,const char*suffix){if(!s||!suffix)return(long)s;usize sufl=piton_strlen(suffix),sl=piton_strlen(s);if(sl>=sufl&&piton_memcmp(s+(sl-sufl),suffix,sufl)==0){usize rem=sl-sufl;char*r=piton_alloc(rem+1);piton_memcpy(r,s,rem);r[rem]=0;return(long)r;}char*r=piton_alloc(sl+1);piton_memcpy(r,s,sl);r[sl]=0;return(long)r;}
static long piton_dict_keys(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i)s->items[i]=d->items[i].key;return(long)s;}
static long piton_dict_values(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i)s->items[i]=d->items[i].value;return(long)s;}
static long piton_dict_items(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i){PitonSeq*p=piton_seq_new(PK_TUPLE,2);p->items[0]=d->items[i].key;p->items[1]=d->items[i].value;s->items[i]=(PitonSlot){(long)p,PK_TUPLE};}return(long)s;}
static long piton_range_slice(PitonRange*r,long lo,long hi){if(!r)return 0;long n=piton_range_len(r);if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;return(long)piton_range_new(r->start+lo*r->step,r->start+hi*r->step,r->step);}
static long piton_range_slice_step(PitonRange*r,long lo,long hi,long st){if(!r)return 0;if(st==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}long n=piton_range_len(r);long a=0,b=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;a=lo;b=hi;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;a=lo;b=hi;}return(long)piton_range_new(r->start+a*r->step,r->start+b*r->step,r->step*st);}
/* DIVMOD_FLOAT_V1: divmod(a, b) con floats -> tupla (q, r). */
static long piton_divmod_float(double a,double b){
  if(b==0.0){piton_raise_set("ZeroDivisionError","float divmod()");return 0;}
  double q=__builtin_floor(a/b);
  double r=a-q*b;
  if(r!=0.0&&((r<0.0)!=(b<0.0))){q-=1.0;r+=b;}
  PitonSeq*p=piton_seq_new(PK_TUPLE,2);
  p->items[0]=(PitonSlot){piton_double_bits(q),PK_FLOAT};
  p->items[1]=(PitonSlot){piton_double_bits(r),PK_FLOAT};
  return(long)p;
}
/* DIVMOD_BIGINT_V1: divmod con al menos un bigint -> tupla (q, r). */
static void*piton_bigint_from_i64(long);
static int piton_bigint_is_zero(void*);
static void*piton_bigint_floor_div(void*,void*);
static void*piton_bigint_mod(void*,void*);
static long piton_divmod_bigint(long a,long b,int a_big,int b_big){
  void*ba=a_big?(void*)a:piton_bigint_from_i64(a);
  void*bb=b_big?(void*)b:piton_bigint_from_i64(b);
  if(piton_bigint_is_zero(bb)){piton_raise_set("ZeroDivisionError","integer division or modulo by zero");return 0;}
  PitonSeq*p=piton_seq_new(PK_TUPLE,2);
  p->items[0]=(PitonSlot){(long)piton_bigint_floor_div(ba,bb),PK_BIGINT};
  p->items[1]=(PitonSlot){(long)piton_bigint_mod(ba,bb),PK_BIGINT};
  return(long)p;
}
static long piton_divmod_xy(long a,long b){if(!b){piton_raise_set("ZeroDivisionError","integer division or modulo by zero");return 0;}long q=a/b,r=a%b;if(r&&((r<0)!=(b<0))){q-=1;r+=b;}PitonSeq*p=piton_seq_new(PK_TUPLE,2);p->items[0]=(PitonSlot){q,PK_INT};p->items[1]=(PitonSlot){r,PK_INT};return(long)p;}
static PitonSlot piton_str_minmax(const char*s,int want_min){if(!s||!*s){piton_raise_set("ValueError","min() arg is an empty sequence");return (PitonSlot){0,PK_STR};}unsigned char best=(unsigned char)s[0];for(long i=1;s[i];++i){unsigned char c=(unsigned char)s[i];if(want_min?c<best:c>best)best=c;}char*r=piton_alloc(2);r[0]=(char)best;r[1]=0;return (PitonSlot){(long)r,PK_STR};}
static long piton_collect(int which,long raw,int elemkind){PitonSeq*s=piton_seq_new(PK_LIST,0);if(!s)return 0;for(;;){long v;if(which==0)v=piton_enumerate_next(raw);else if(which==1)v=piton_reversed_next(raw);else if(which==2)v=piton_callback_iterator_next(raw);else if(which==3)v=piton_zip_next(raw);else if(which==4)v=piton_gen_next(raw);else{piton_raise_set("TypeError","collect");return 0;}if(piton_exc_flag){if(piton_exc_type&&piton_strcmp(piton_exc_type,"StopIteration")==0){piton_catch_clear();break;}return 0;}piton_seq_append(s,(PitonSlot){v,elemkind});}return(long)s;}
static PitonSeq* piton_collect_gen(long raw){return (PitonSeq*)piton_collect(4,raw,PK_INT);}
static long piton_collect_any_iter(long raw){PitonSeq*s=piton_seq_new(PK_LIST,0);for(;;){long v=piton_iterator_next_any(raw);if(piton_exc_flag){if(piton_exc_type&&piton_strcmp(piton_exc_type,"StopIteration")==0){piton_catch_clear();break;}return 0;}PitonSlot it={v,PK_INT};piton_seq_append(s,it);}return(long)s;}
static long piton_collect_via_next_fn(long raw,long(*fn)(long)){PitonSeq*s=piton_seq_new(PK_LIST,0);for(;;){long v=fn(raw);if(piton_exc_flag){if(piton_exc_type&&piton_strcmp(piton_exc_type,"StopIteration")==0){piton_catch_clear();break;}return 0;}PitonSlot it={v,PK_INT};piton_seq_append(s,it);}return(long)s;}
static PitonSeq* piton_str_explode(const char*s){usize n=piton_strlen(s);PitonSeq*r=piton_seq_new(PK_LIST,n);for(usize i=0;i<n;++i){char*q=piton_alloc(2);q[0]=s[i];q[1]=0;piton_seq_put(r,(long)i,(PitonSlot){(long)q,PK_STR});}return r;}
static PitonBytes* piton_bytes_new(long len, const unsigned char* data){
    if(len<0){piton_raise_set("ValueError","negative count");return 0;}
    PitonBytes* b=(PitonBytes*)piton_alloc(sizeof(PitonBytes));
    b->refcount=1;
    b->kind=PK_BYTES;
    b->length=len;
    b->data=len>0?(unsigned char*)piton_alloc((usize)len+1):0;
    if(len>0&&data)piton_memcpy(b->data,data,(usize)len);
    if(b->data)b->data[len]=0;
    return b;
}
static long piton_str_encode(const char*s,const char*enc){
  if(!s)s="";
  int is_latin=(enc&&*enc&&(piton_strcmp(enc,"latin-1")==0||piton_strcmp(enc,"latin1")==0||piton_strcmp(enc,"iso-8859-1")==0));
  int is_ascii=(enc&&*enc&&piton_strcmp(enc,"ascii")==0);
  if(enc&&*enc&&!is_latin&&!is_ascii&&piton_strcmp(enc,"utf-8")!=0&&piton_strcmp(enc,"utf8")!=0){
    piton_raise_set("LookupError","unknown encoding");return 0;}
  if(is_latin){
    /* decodificar UTF-8 -> code points -> 1 byte por punto (U+0000..U+00FF) */
    usize n=piton_strlen(s);
    unsigned char*out=(unsigned char*)piton_alloc(n+1);
    usize o=0;
    for(usize i=0;i<n;){
      unsigned char c=(unsigned char)s[i];long cp;int k;
      if(c<0x80){cp=c;k=1;}
      else if((c&0xE0)==0xC0){cp=c&0x1F;k=2;}
      else if((c&0xF0)==0xE0){cp=c&0x0F;k=3;}
      else if((c&0xF8)==0xF0){cp=c&0x07;k=4;}
      else{piton_raise_set("UnicodeEncodeError","'latin-1' codec can't encode character");return 0;}
      if(i+(usize)k>n){piton_raise_set("UnicodeEncodeError","'latin-1' codec can't encode character");return 0;}
      for(int j=1;j<k;++j){unsigned char cc=(unsigned char)s[i+(usize)j];if((cc&0xC0)!=0x80){piton_raise_set("UnicodeEncodeError","'latin-1' codec can't encode character");return 0;}cp=(cp<<6)|(cc&0x3F);}
      if(cp>0xFF){piton_raise_set("UnicodeEncodeError","'latin-1' codec can't encode character");return 0;}
      out[o++]=(unsigned char)cp;
      i+=(usize)k;
    }
    return(long)piton_bytes_new((long)o,out);
  }
  usize n=piton_strlen(s);
  PitonBytes*b=piton_bytes_new((long)n,(const unsigned char*)s);
  if(!b)return 0;
  if(is_ascii){
    for(usize i=0;i<n;++i)if((unsigned char)s[i]>=0x80){piton_raise_set("UnicodeEncodeError","'ascii' codec can't encode character");return 0;}
  }
  return(long)b;
}

static PitonByteArray* piton_bytearray_new(long len, const unsigned char* data){
    if(len<0){piton_raise_set("ValueError","negative count");return 0;}
    PitonByteArray* ba=(PitonByteArray*)piton_alloc(sizeof(PitonByteArray));
    ba->refcount=1;
    ba->kind=PK_BYTEARRAY;
    ba->length=len;
    long cap=len>0?len:4;
    ba->capacity=cap;
    ba->data=(unsigned char*)piton_alloc((usize)cap+1);
    if(len>0&&data)piton_memcpy(ba->data,data,(usize)len);
    ba->data[len]=0;
    return ba;
}

static const unsigned char* piton_bytes_get_data(void* ptr, long* out_len){
    if(!ptr){if(out_len)*out_len=0;return 0;}
    long kind=((long*)ptr)[1];
    if(kind==PK_BYTES){PitonBytes*b=(PitonBytes*)ptr;if(out_len)*out_len=b->length;return b->data;}
    if(kind==PK_BYTEARRAY){PitonByteArray*ba=(PitonByteArray*)ptr;if(out_len)*out_len=ba->length;return ba->data;}
    if(out_len)*out_len=0;return 0;
}

static void piton_bytes_print_body(void* raw, int is_bytearray){
    long n=0;
    const unsigned char* d=piton_bytes_get_data(raw,&n);
    int has_sq=0,has_dq=0;
    for(long i=0;i<n;++i){if(d[i]=='\'')has_sq=1;else if(d[i]=='"')has_dq=1;}
    char q=(has_sq&&!has_dq)?'"':'\'';
    if(is_bytearray)piton_write(piton_print_fd,"bytearray(",10);
    piton_write(piton_print_fd,"b",1);
    piton_write(piton_print_fd,&q,1);
    for(long i=0;i<n;++i){
        unsigned char c=d[i];
        if(c=='\\')piton_write(piton_print_fd,"\\\\",2);
        else if(c==(unsigned char)q){char e[2];e[0]='\\';e[1]=q;piton_write(piton_print_fd,e,2);}
        else if(is_bytearray&&q=='"'&&c=='\''){piton_write(piton_print_fd,"\\'",2);}
        else if(c=='\t')piton_write(piton_print_fd,"\\t",2);
        else if(c=='\n')piton_write(piton_print_fd,"\\n",2);
        else if(c=='\r')piton_write(piton_print_fd,"\\r",2);
        else if(c>=0x20&&c<=0x7E)piton_write(piton_print_fd,(const char*)&c,1);
        else{
            char h[4];
            const char* hex_digits="0123456789abcdef";
            h[0]='\\';h[1]='x';
            h[2]=hex_digits[(c>>4)&0xF];
            h[3]=hex_digits[c&0xF];
            piton_write(piton_print_fd,h,4);
        }
    }
    piton_write(piton_print_fd,&q,1);
    if(is_bytearray)piton_write(piton_print_fd,")",1);
}

static void piton_bytes_print(PitonBytes* b){
    if(!b){piton_write(piton_print_fd,"b''",3);return;}
    piton_bytes_print_body((void*)b, 0);
}

static void piton_bytearray_print(PitonByteArray* ba){
    if(!ba){piton_write(piton_print_fd,"bytearray(b'')",14);return;}
    piton_bytes_print_body((void*)ba, 1);
}

static long piton_bytes_get(PitonBytes* b, long idx){
    if(!b){piton_raise_set("IndexError","index out of range");piton_report_unhandled();piton_exit(1);return 0;}
    long len=b->length;
    if(idx<0)idx+=len;
    if(idx<0||idx>=len){piton_raise_set("IndexError","index out of range");piton_report_unhandled();piton_exit(1);return 0;}
    const unsigned char* d=piton_bytes_get_data(b,0);
    return (long)d[idx];
}

static void piton_bytearray_set(PitonByteArray* ba, long idx, long val){
    if(!ba){piton_raise_set("IndexError","bytearray index out of range");piton_report_unhandled();piton_exit(1);return;}
    if(idx<0)idx+=ba->length;
    if(idx<0||idx>=ba->length){piton_raise_set("IndexError","bytearray index out of range");piton_report_unhandled();piton_exit(1);return;}
    if(val<0||val>255){piton_raise_set("ValueError","byte must be in range(0, 256)");piton_report_unhandled();piton_exit(1);return;}
    ba->data[idx]=(unsigned char)val;
}

static PitonBytes* piton_bytes_slice(PitonBytes* b, long lo, long hi){
    if(!b)return piton_bytes_new(0,0);
    long n=b->length;
    if(lo<0)lo+=n;if(hi<0)hi+=n;
    if(lo<0)lo=0;if(hi>n)hi=n;
    if(hi<lo)hi=lo;
    const unsigned char* d=piton_bytes_get_data(b,0);
    return piton_bytes_new(hi-lo, d ? d+lo : 0);
}

static PitonByteArray* piton_bytearray_slice(PitonByteArray* ba, long lo, long hi){
    if(!ba)return piton_bytearray_new(0,0);
    long n=ba->length;
    if(lo<0)lo+=n;if(hi<0)hi+=n;
    if(lo<0)lo=0;if(hi>n)hi=n;
    if(hi<lo)hi=lo;
    const unsigned char* d=piton_bytes_get_data(ba,0);
    return piton_bytearray_new(hi-lo, d ? d+lo : 0);
}

static PitonBytes* piton_bytes_slice_step(PitonBytes* b, long lo, long hi, long st){
    if(!b)return piton_bytes_new(0,0);
    if(st==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}
    long n=b->length;
    long len=0, a=0;
    if(st>0){
        if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;
        if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;
        if(lo<0)lo+=n;if(hi<0)hi+=n;
        if(lo<0)lo=0;if(hi>n)hi=n;
        if(hi<lo)hi=lo;
        len=(hi-lo+st-1)/st;
        a=lo;
    }else{
        if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;
        if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;
        if(lo<0)lo+=n;if(hi<0)hi+=n;
        if(lo>=n)lo=n-1;if(hi<-1)hi=-1;
        if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);
        a=lo;
    }
    const unsigned char* d=piton_bytes_get_data(b,0);
    PitonBytes* res=piton_bytes_new(len,0);
    for(long i=0;i<len;++i)res->data[i]=d[a+i*st];
    return res;
}

static PitonByteArray* piton_bytearray_slice_step(PitonByteArray* ba, long lo, long hi, long st){
    if(!ba)return piton_bytearray_new(0,0);
    if(st==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}
    long n=ba->length;
    long len=0, a=0;
    if(st>0){
        if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;
        if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;
        if(lo<0)lo+=n;if(hi<0)hi+=n;
        if(lo<0)lo=0;if(hi>n)hi=n;
        if(hi<lo)hi=lo;
        len=(hi-lo+st-1)/st;
        a=lo;
    }else{
        if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;
        if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;
        if(lo<0)lo+=n;if(hi<0)hi+=n;
        if(lo>=n)lo=n-1;if(hi<-1)hi=-1;
        if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);
        a=lo;
    }
    const unsigned char* d=piton_bytes_get_data(ba,0);
    PitonByteArray* res=piton_bytearray_new(len,0);
    for(long i=0;i<len;++i)res->data[i]=d[a+i*st];
    return res;
}

static PitonBytes* piton_bytes_concat(PitonBytes* a, PitonBytes* b){
    long la=0, lb=0;
    const unsigned char* da=piton_bytes_get_data(a,&la);
    const unsigned char* db=piton_bytes_get_data(b,&lb);
    PitonBytes* res=piton_bytes_new(la+lb,0);
    if(la&&da)piton_memcpy(res->data,da,(usize)la);
    if(lb&&db)piton_memcpy(res->data+la,db,(usize)lb);
    return res;
}

static PitonByteArray* piton_bytearray_concat(PitonByteArray* a, PitonByteArray* b){
    long la=0, lb=0;
    const unsigned char* da=piton_bytes_get_data(a,&la);
    const unsigned char* db=piton_bytes_get_data(b,&lb);
    PitonByteArray* res=piton_bytearray_new(la+lb,0);
    if(la&&da)piton_memcpy(res->data,da,(usize)la);
    if(lb&&db)piton_memcpy(res->data+la,db,(usize)lb);
    return res;
}

static PitonBytes* piton_bytes_repeat(PitonBytes* b, long n){
    if(n<=0)return piton_bytes_new(0,0);
    long len=0;
    const unsigned char* d=piton_bytes_get_data(b,&len);
    PitonBytes* res=piton_bytes_new(len*n,0);
    for(long i=0;i<n;++i){
        if(len&&d)piton_memcpy(res->data+i*len,d,(usize)len);
    }
    return res;
}

static PitonByteArray* piton_bytearray_repeat(PitonByteArray* ba, long n){
    if(n<=0)return piton_bytearray_new(0,0);
    long len=0;
    const unsigned char* d=piton_bytes_get_data(ba,&len);
    PitonByteArray* res=piton_bytearray_new(len*n,0);
    for(long i=0;i<n;++i){
        if(len&&d)piton_memcpy(res->data+i*len,d,(usize)len);
    }
    return res;
}

static int piton_bytes_cmp(PitonBytes* a, PitonBytes* b){
    long la=0, lb=0;
    const unsigned char* da=piton_bytes_get_data(a,&la);
    const unsigned char* db=piton_bytes_get_data(b,&lb);
    long min_len=la<lb?la:lb;
    if(min_len>0&&da&&db){
        int c=piton_memcmp(da,db,(usize)min_len);
        if(c!=0)return c;
    }
    return la<lb?-1:(la>lb?1:0);
}

static long piton_bytes_contains(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(v.kind==PK_INT||v.kind==PK_BOOL){
        if(v.bits<0||v.bits>255){piton_raise_set("ValueError","byte must be in range(0, 256)");return 0;}
        unsigned char target=(unsigned char)v.bits;
        for(long i=0;i<len;++i){if(d[i]==target)return 1;}
        return 0;
    }else if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen==0)return 1;
        if(nlen>len)return 0;
        for(long i=0;i+nlen<=len;++i){
            if(piton_memcmp(d+i,nd,(usize)nlen)==0)return 1;
        }
        return 0;
    }
    piton_raise_set("TypeError","a bytes-like object is required, not 'str'");
    return 0;
}

static long piton_bytes_decode(void* raw, const char* enc){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(!enc||!*enc||piton_strcmp(enc,"utf-8")==0||piton_strcmp(enc,"utf8")==0){
        char* out=piton_alloc((usize)len+1);
        if(len&&d)piton_memcpy(out,d,(usize)len);
        out[len]=0;
        return (long)out;
    }
    if(piton_strcmp(enc,"ascii")==0){
        for(long i=0;i<len;++i){
            if(d[i]>=0x80){piton_raise_set("UnicodeDecodeError","'ascii' codec can't decode byte");return 0;}
        }
        char* out=piton_alloc((usize)len+1);
        if(len&&d)piton_memcpy(out,d,(usize)len);
        out[len]=0;
        return (long)out;
    }
    if(piton_strcmp(enc,"latin-1")==0||piton_strcmp(enc,"latin1")==0||piton_strcmp(enc,"iso-8859-1")==0){
        char* out=piton_alloc((usize)len*2+1);
        usize o=0;
        for(long i=0;i<len;++i){
            unsigned char c=d[i];
            if(c<0x80)out[o++]=c;
            else{out[o++]=(char)(0xC0|(c>>6));out[o++]=(char)(0x80|(c&0x3F));}
        }
        out[o]=0;
        return (long)out;
    }
    piton_raise_set("LookupError","unknown encoding");
    return 0;
}

static long piton_bytes_hex(void* raw){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    char* out=piton_alloc((usize)len*2+1);
    const char* digits="0123456789abcdef";
    for(long i=0;i<len;++i){
        out[i*2]=digits[(d[i]>>4)&0xF];
        out[i*2+1]=digits[d[i]&0xF];
    }
    out[len*2]=0;
    return (long)out;
}

static void* piton_bytes_fromhex(const char* s, int is_ba){
    if(!s)s="";
    usize sl=piton_strlen(s);
    unsigned char* buf=piton_alloc(sl+1);
    long bl=0;
    int hi=-1;
    for(usize i=0;i<sl;++i){
        char c=s[i];
        if(c==' '||c=='\t'||c=='\n'||c=='\r')continue;
        int v=-1;
        if(c>='0'&&c<='9')v=c-'0';
        else if(c>='a'&&c<='f')v=c-'a'+10;
        else if(c>='A'&&c<='F')v=c-'A'+10;
        else{piton_raise_set("ValueError","non-hexadecimal number found in fromhex() arg");return 0;}
        if(hi<0)hi=v;
        else{buf[bl++]=(unsigned char)((hi<<4)|v);hi=-1;}
    }
    if(hi>=0){piton_raise_set("ValueError","fromhex() arg must contain an even number of hexadecimal digits");return 0;}
    if(is_ba)return piton_bytearray_new(bl,buf);
    return piton_bytes_new(bl,buf);
}

static long piton_bytes_count(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    long cnt=0;
    if(v.kind==PK_INT||v.kind==PK_BOOL){
        if(v.bits<0||v.bits>255)return 0;
        unsigned char target=(unsigned char)v.bits;
        for(long i=0;i<len;++i)if(d[i]==target)cnt++;
        return cnt;
    }else if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen==0)return len+1;
        if(nlen>len)return 0;
        for(long i=0;i+nlen<=len;){
            if(piton_memcmp(d+i,nd,(usize)nlen)==0){cnt++;i+=nlen;}
            else i++;
        }
        return cnt;
    }
    piton_raise_set("TypeError","a bytes-like object is required");
    return 0;
}

static long piton_bytes_find(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(v.kind==PK_INT||v.kind==PK_BOOL){
        if(v.bits<0||v.bits>255)return -1;
        unsigned char target=(unsigned char)v.bits;
        for(long i=0;i<len;++i)if(d[i]==target)return i;
        return -1;
    }else if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen==0)return 0;
        if(nlen>len)return -1;
        for(long i=0;i+nlen<=len;++i){
            if(piton_memcmp(d+i,nd,(usize)nlen)==0)return i;
        }
        return -1;
    }
    piton_raise_set("TypeError","a bytes-like object is required");
    return -1;
}

static long piton_bytes_rfind(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(v.kind==PK_INT||v.kind==PK_BOOL){
        if(v.bits<0||v.bits>255)return -1;
        unsigned char target=(unsigned char)v.bits;
        for(long i=len-1;i>=0;--i)if(d[i]==target)return i;
        return -1;
    }else if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen==0)return len;
        if(nlen>len)return -1;
        for(long i=len-nlen;i>=0;--i){
            if(piton_memcmp(d+i,nd,(usize)nlen)==0)return i;
        }
        return -1;
    }
    piton_raise_set("TypeError","a bytes-like object is required");
    return -1;
}

static long piton_bytes_index(void* raw, PitonSlot v){
    long pos=piton_bytes_find(raw,v);
    if(pos<0){piton_raise_set("ValueError","subsection not found");piton_report_unhandled();piton_exit(1);return 0;}
    return pos;
}

static long piton_bytes_rindex(void* raw, PitonSlot v){
    long pos=piton_bytes_rfind(raw,v);
    if(pos<0){piton_raise_set("ValueError","subsection not found");piton_report_unhandled();piton_exit(1);return 0;}
    return pos;
}

static long piton_bytes_startswith(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen>len)return 0;
        if(nlen==0)return 1;
        return piton_memcmp(d,nd,(usize)nlen)==0?1:0;
    }
    piton_raise_set("TypeError","startswith first arg must be bytes or a tuple of bytes");
    piton_report_unhandled();piton_exit(1);
    return 0;
}

static long piton_bytes_endswith(void* raw, PitonSlot v){
    long len=0;
    const unsigned char* d=piton_bytes_get_data(raw,&len);
    if(v.kind==PK_BYTES||v.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)v.bits,&nlen);
        if(nlen>len)return 0;
        if(nlen==0)return 1;
        return piton_memcmp(d+len-nlen,nd,(usize)nlen)==0?1:0;
    }
    piton_raise_set("TypeError","endswith first arg must be bytes or a tuple of bytes");
    piton_report_unhandled();piton_exit(1);
    return 0;
}

/* BYTEARRAY_SLICE_ASSIGN_V1: ba[lo:hi:st] = src.
 * CPython: con step 1 sustituye el rango por cualquier longitud (crece o
 * encoge el buffer); con step != 1 exige longitud EXACTA. El rastreo de
 * capacidad espeja piton_bytearray_append. */
static long piton_bytes_len_of(long raw){long n=0;(void)piton_bytes_get_data((void*)raw,&n);return n;}
/* IN_STR_DYNAMIC_V1: el needle de `x in <str>` sin tipo estatico fiable se
   decide en runtime: un str imprimible es busqueda de subcadena, cualquier
   otra cosa es el TypeError de CPython (netrc.py: `ch in self.whitespace`
   con `ch` sin tipo; el tipo "int" por defecto era un hueco de inferencia). */
static long piton_str_contains_dynamic(const char*h,long needle){
  if(needle&&piton_maybe_ptr(needle)&&piton_is_printable_str((const char*)needle))
    return piton_str_contains(h,(const char*)needle);
  piton_raise_set("TypeError","'in <string>' requires string as left operand");
  return 0;
}
/* ORD_DYNAMIC_V1: ord() de un valor sin tipo estatico (curses/ascii.py:
   `ord(c)` con c de origen desconocido). La longitud y el tipo se deciden en
   runtime; fuera del caso valido se levanta el TypeError de CPython. */
static long piton_ord_dynamic(long v){
  if(v&&piton_maybe_ptr(v)&&piton_is_printable_str((const char*)v)){
    const char*s=(const char*)v;
    if(piton_strlen(s)==1)return (long)(unsigned char)s[0];
    piton_raise_set("TypeError","ord() expected a character, but string of length != 1 found");
    return 0;
  }
  piton_raise_set("TypeError","ord() expected string of length 1");
  return 0;
}
/* LEN_DYNAMIC_V1: len() de un valor cuyo tipo estatico NO se conoce. La
   verdad la decide la cabecera en runtime; si no es una coleccion se levanta
   el TypeError de CPython en vez de inventar un numero. protocols.py:
   `len(self._drain_waiter)` (atributo sin tipo estatico). */
static long piton_len_dynamic(long bits){
  if(!bits){piton_raise_set("TypeError","object of type 'NoneType' has no len()");return 0;}
  if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];
    /* LEN_DYNAMIC_V1: el rango debe cubrir TODAS las cabeceras (el enum pone
       bigint/range/bytes/bytearray/complex/frozenset/slice/memoryview por
       encima de PK_OBJECT), o las ramas de bytes quedan muertas. */
    if(ptr[0]>0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW){
      if(k==PK_LIST||k==PK_TUPLE)return ((PitonSeq*)bits)->length;
      if(k==PK_DICT)return ((PitonDict*)bits)->length;
      if(k==PK_SET||k==PK_FROZENSET)return ((PitonSet*)bits)->length;
      if(k==PK_RANGE)return piton_range_len((PitonRange*)bits);
      if(k==PK_BYTES||k==PK_BYTEARRAY)return piton_bytes_len_of(bits);
      piton_raise_set("TypeError","object has no len()");return 0;
    }
    if(piton_is_printable_str((const char*)bits))return (long)piton_strlen((const char*)bits);
  }
  piton_raise_set("TypeError","object of type 'int' has no len()");return 0;
}
static const unsigned char* piton_bytes_data_ptr_of(long raw){return piton_bytes_get_data((void*)raw,0);}
static void piton_bytearray_slice_assign(PitonByteArray* ba, long lo, long hi, long st, const unsigned char* src, long slen){
    if(!ba)return;
    if(st==0){piton_raise_set("ValueError","slice step cannot be zero");piton_report_unhandled();piton_exit(1);return;}
    long n=ba->length;
    if(st>0){
        if(lo<0)lo+=n;if(lo<0)lo=0;if(lo>n)lo=n;
        if(hi<0)hi+=n;if(hi<0)hi=0;if(hi>n)hi=n;
        if(hi<lo)hi=lo;
    }else{
        if(lo<0)lo+=n;if(lo<-1)lo=-1;if(lo>=n)lo=n-1;
        if(hi<0)hi+=n;if(hi<-1)hi=-1;
    }
    if(st==1){
        long old_len=hi-lo;
        long delta=slen-old_len;
        long new_len=n+delta;
        if(delta>0&&new_len>ba->capacity){
            long nc=ba->capacity?ba->capacity*2:8;
            while(nc<new_len)nc*=2;
            unsigned char* nd=(unsigned char*)piton_alloc((usize)nc+1);
            if(ba->data)piton_memcpy(nd,ba->data,(usize)ba->length);
            ba->data=nd;ba->capacity=nc;
        }
        if(delta!=0&&hi<n)piton_memmove(ba->data+hi+delta,ba->data+hi,(usize)(n-hi));
        if(slen>0&&src)piton_memcpy(ba->data+lo,src,(usize)slen);
        ba->length=new_len;
        if(ba->data)ba->data[ba->length]=0;
        return;
    }
    /* step != 1: longitud exacta */
    long slots;
    if(st>0)slots=(hi-lo+st-1)/st;
    else slots=(lo-hi-st-1)/(-st);
    if(slots<0)slots=0;
    if(slen!=slots){piton_raise_set("ValueError","attempt to assign a sequence to an extended slice of different size");piton_report_unhandled();piton_exit(1);return;}
    for(long i=0;i<slots;++i){
        long pos=lo+(st>0?i*st:-(i*(-st)));
        ba->data[pos]=src[i];
    }
}
/* SLICE_DYNAMIC_V1: el tipo estatico del valor NO es una secuencia conocida
   (warnings.py: `self._filters[:]` sobre un atributo de modulo sin tipo). La
   cabecera decide en runtime. Una secuencia se corta como CPython (el kind del
   header manda al imprimir); un str dinamico falla cerrado con exit 2 (su
   resultado tendria tipo estatico desconocido); el resto levanta el TypeError
   de CPython. */
static long piton_slice_dynamic(long bits,long lo,long hi,long has_step,long step){
  if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW){
      if(k==PK_LIST||k==PK_TUPLE)
        return has_step?piton_seq_slice_step((PitonSeq*)bits,lo,hi,step)
                       :piton_seq_slice((PitonSeq*)bits,lo,hi);
      if(k==PK_BYTES)
        return has_step?piton_bytes_slice_step((PitonBytes*)bits,lo,hi,step)
                       :(long)piton_bytes_slice((PitonBytes*)bits,lo,hi);
      if(k==PK_BYTEARRAY)
        return has_step?piton_bytearray_slice_step((PitonByteArray*)bits,lo,hi,step)
                       :(long)piton_bytearray_slice((PitonByteArray*)bits,lo,hi);
      if(k==PK_RANGE)
        return has_step?piton_range_slice_step((PitonRange*)bits,lo,hi,step)
                       :piton_range_slice((PitonRange*)bits,lo,hi);
    }
    if(piton_is_printable_str((const char*)bits)){
      piton_write(2,"TypeError: slice of a dynamically typed str is not supported in the native subset\n",88);
      piton_exit(2);
      return 0;
    }
  }
  piton_raise_set("TypeError","object is not subscriptable");
  return 0;
}
/* SLICE_ASSIGN_DYNAMIC_V1: destino de slice-store sin tipo anotado
   (protocols.py: `buf[:data_len] = data` con buf bytearray de parametro). Solo
   bytearray <- bytes/bytearray se resuelve; list/str (que CPython SI acepta)
   fallan cerrado con exit 2 en vez de divergir en silencio, y bytes/tuple
   levantan el TypeError de CPython. */
static void piton_slice_assign_dynamic(long bits,long lo,long hi,long step,long src){
  if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L&&k>=PK_LIST&&k<=PK_MEMORYVIEW){
      if(k==PK_BYTEARRAY){
        long kk=0;
        if(piton_maybe_ptr(src)){kk=((long*)src)[1];}
        if(kk==PK_BYTES||kk==PK_BYTEARRAY){
          piton_bytearray_slice_assign((PitonByteArray*)bits,lo,hi,step,
            (const unsigned char*)piton_bytes_data_ptr_of(src),piton_bytes_len_of(src));
          return;
        }
        piton_write(2,"TypeError: bytearray slice assignment requires bytes/bytearray value\n",72);
        piton_exit(1);
        return;
      }
      if(k==PK_BYTES||k==PK_TUPLE||k==PK_STR){
        piton_raise_set("TypeError","object does not support item assignment");
        piton_report_unhandled();
        piton_exit(1);
        return;
      }
      piton_write(2,"TypeError: slice assignment of a dynamically typed sequence is not supported in the native subset\n",101);
      piton_exit(2);
      return;
    }
  }
  piton_raise_set("TypeError","object does not support item assignment");
  piton_report_unhandled();
  piton_exit(1);
}
/* CALL_STAR_DYNAMIC_V1: `f(*x)` con x sin tipo estatico (warnings.py:
   `simplefilter(*self._filters)`). El kind lo dice la cabecera; si no es una
   lista/tupla se levanta el TypeError de CPython. Los iterables que CPython
   acepta y el subset no (set, genexpr) siguen siendo un hueco documentado:
   el build NO se relaja para tipos conocidos, solo para los desconocidos. */
static long piton_unpack_seq4_dynamic(long bits,long capacity,long*out4){
  if(piton_maybe_ptr(bits)){long*ptr=(long*)bits;long k=ptr[1];
    if(ptr[0]>=0&&ptr[0]<1000000L&&(k==PK_LIST||k==PK_TUPLE)){
      PitonSeq*s=(PitonSeq*)bits;
      if(s->length>capacity){piton_raise_set("TypeError","too many positional arguments for call");return -1;}
      for(long i=0;i<s->length;++i)out4[i]=s->items[i].bits;
      return s->length;
    }}
  piton_raise_set("TypeError","argument after * must be a list or tuple");
  return -1;
}
static void piton_bytearray_append(PitonByteArray* ba, long val){
    if(!ba)return;
    if(val<0||val>255){piton_raise_set("ValueError","byte must be in range(0, 256)");piton_report_unhandled();piton_exit(1);return;}
    if(ba->length>=ba->capacity){
        long nc=ba->capacity?ba->capacity*2:8;
        unsigned char* nd=(unsigned char*)piton_alloc((usize)nc+1);
        if(ba->data)piton_memcpy(nd,ba->data,(usize)ba->length);
        ba->data=nd;
        ba->capacity=nc;
    }
    ba->data[ba->length++]=(unsigned char)val;
    ba->data[ba->length]=0;
}

static void piton_bytearray_extend(PitonByteArray* ba, PitonSlot src){
    if(!ba)return;
    if(src.kind==PK_BYTES||src.kind==PK_BYTEARRAY){
        long nlen=0;
        const unsigned char* nd=piton_bytes_get_data((void*)src.bits,&nlen);
        for(long i=0;i<nlen;++i)piton_bytearray_append(ba,(long)nd[i]);
    }else if(src.kind==PK_LIST||src.kind==PK_TUPLE){
        PitonSeq* s=(PitonSeq*)src.bits;
        for(long i=0;i<s->length;++i){
            PitonSlot it=s->items[i];
            if(it.kind!=PK_INT&&it.kind!=PK_BOOL){piton_raise_set("TypeError","an integer is required");return;}
            piton_bytearray_append(ba,it.bits);
            if(piton_exc_flag)return;
        }
    }else if(src.kind==PK_RANGE){
        PitonRange* r=(PitonRange*)src.bits;
        long n=piton_range_len(r);
        for(long i=0;i<n;++i){
            long v=r->start+i*r->step;
            piton_bytearray_append(ba,v);
            if(piton_exc_flag)return;
        }
    }else{
        piton_raise_set("TypeError","can't extend bytearray with non-iterable");
    }
}

static PitonBytes* piton_bytes_from_int(long n){
    if(n<0){piton_raise_set("ValueError","negative count");return 0;}
    return piton_bytes_new(n,0);
}

static PitonByteArray* piton_bytearray_from_int(long n){
    if(n<0){piton_raise_set("ValueError","negative count");return 0;}
    return piton_bytearray_new(n,0);
}

static PitonBytes* piton_bytes_from_seq(PitonSeq* s){
    if(!s)return piton_bytes_new(0,0);
    PitonBytes* res=piton_bytes_new(s->length,0);
    for(long i=0;i<s->length;++i){
        PitonSlot v=s->items[i];
        if(v.kind!=PK_INT&&v.kind!=PK_BOOL){piton_raise_set("TypeError","'str' object cannot be interpreted as an integer");return 0;}
        if(v.bits<0||v.bits>255){piton_raise_set("ValueError","bytes must be in range(0, 256)");return 0;}
        res->data[i]=(unsigned char)v.bits;
    }
    return res;
}

static PitonByteArray* piton_bytearray_from_seq(PitonSeq* s){
    if(!s)return piton_bytearray_new(0,0);
    PitonByteArray* res=piton_bytearray_new(s->length,0);
    for(long i=0;i<s->length;++i){
        PitonSlot v=s->items[i];
        if(v.kind!=PK_INT&&v.kind!=PK_BOOL){piton_raise_set("TypeError","'str' object cannot be interpreted as an integer");return 0;}
        if(v.bits<0||v.bits>255){piton_raise_set("ValueError","byte must be in range(0, 256)");return 0;}
        res->data[i]=(unsigned char)v.bits;
    }
    return res;
}

static PitonBytes* piton_bytes_from_range(PitonRange* r){
    if(!r)return piton_bytes_new(0,0);
    long n=piton_range_len(r);
    PitonBytes* res=piton_bytes_new(n,0);
    for(long i=0;i<n;++i){
        long v=r->start+i*r->step;
        if(v<0||v>255){piton_raise_set("ValueError","bytes must be in range(0, 256)");return 0;}
        res->data[i]=(unsigned char)v;
    }
    return res;
}

static PitonByteArray* piton_bytearray_from_range(PitonRange* r){
    if(!r)return piton_bytearray_new(0,0);
    long n=piton_range_len(r);
    PitonByteArray* res=piton_bytearray_new(n,0);
    for(long i=0;i<n;++i){
        long v=r->start+i*r->step;
        if(v<0||v>255){piton_raise_set("ValueError","byte must be in range(0, 256)");return 0;}
        res->data[i]=(unsigned char)v;
    }
    return res;
}

static PitonBytes* piton_bytes_from_str(const char* s, const char* enc){
    if(!s)s="";
    usize sl=piton_strlen(s);
    if(!enc||!*enc||piton_strcmp(enc,"utf-8")==0||piton_strcmp(enc,"utf8")==0){
        return piton_bytes_new((long)sl,(const unsigned char*)s);
    }
    if(piton_strcmp(enc,"ascii")==0){
        for(usize i=0;i<sl;++i){
            if((unsigned char)s[i]>=0x80){piton_raise_set("UnicodeEncodeError","'ascii' codec can't encode character");return 0;}
        }
        return piton_bytes_new((long)sl,(const unsigned char*)s);
    }
    if(piton_strcmp(enc,"latin-1")==0||piton_strcmp(enc,"latin1")==0||piton_strcmp(enc,"iso-8859-1")==0){
        return piton_bytes_new((long)sl,(const unsigned char*)s);
    }
    piton_raise_set("LookupError","unknown encoding");
    return 0;
}

static PitonByteArray* piton_bytearray_from_str(const char* s, const char* enc){
    PitonBytes* b=piton_bytes_from_str(s,enc);
    if(!b)return 0;
    return piton_bytearray_new(b->length,b->data);
}

static PitonBytes* piton_bytes_from_any(PitonSlot src){
    if(src.kind==PK_INT||src.kind==PK_BOOL)return piton_bytes_from_int(src.bits);
    if(src.kind==PK_STR){piton_raise_set("TypeError","string argument without an encoding");return 0;}
    if(src.kind==PK_LIST||src.kind==PK_TUPLE)return piton_bytes_from_seq((PitonSeq*)src.bits);
    if(src.kind==PK_RANGE)return piton_bytes_from_range((PitonRange*)src.bits);
    if(src.kind==PK_BYTES||src.kind==PK_BYTEARRAY){
        long len=0;
        const unsigned char* d=piton_bytes_get_data((void*)src.bits,&len);
        return piton_bytes_new(len,d);
    }
    piton_raise_set("TypeError","cannot convert to bytes");
    return 0;
}

static PitonByteArray* piton_bytearray_from_any(PitonSlot src){
    if(src.kind==PK_INT||src.kind==PK_BOOL)return piton_bytearray_from_int(src.bits);
    if(src.kind==PK_STR){piton_raise_set("TypeError","string argument without an encoding");return 0;}
    if(src.kind==PK_LIST||src.kind==PK_TUPLE)return piton_bytearray_from_seq((PitonSeq*)src.bits);
    if(src.kind==PK_RANGE)return piton_bytearray_from_range((PitonRange*)src.bits);
    if(src.kind==PK_BYTES||src.kind==PK_BYTEARRAY){
        long len=0;
        const unsigned char* d=piton_bytes_get_data((void*)src.bits,&len);
        return piton_bytearray_new(len,d);
    }
    piton_raise_set("TypeError","cannot convert to bytearray");
    return 0;
}

static long piton_bytes_iterator_new(void* b){
    if(!b){piton_raise_set("TypeError","'NoneType' object is not iterable");return 0;}
    PitonBytesIterator* i=piton_alloc(sizeof(*i));
    i->magic=0x5049544E42595445LL;
    i->bytes_obj=b;
    i->index=0;
    return (long)i;
}

static long piton_bytes_iterator_next(long raw){
    PitonBytesIterator* i=(PitonBytesIterator*)raw;
    if(!i||i->magic!=0x5049544E42595445LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}
    long len=0;
    const unsigned char* d=piton_bytes_get_data(i->bytes_obj,&len);
    if(i->index>=len){piton_raise_set("StopIteration","");return 0;}
    return (long)d[i->index++];
}

static void piton_report_unhandled(void){if(piton_exc_cause_type){piton_write(2,piton_exc_cause_type,piton_strlen(piton_exc_cause_type));if(piton_exc_cause_msg&&piton_exc_cause_msg[0]){piton_write(2,": ",2);piton_write(2,piton_exc_cause_msg,piton_strlen(piton_exc_cause_msg));}piton_write(2," -> causada por\n",16);}piton_write(2,piton_exc_type,piton_strlen(piton_exc_type));piton_write(2,": ",2);if(piton_exc_message)piton_write(2,piton_exc_message,piton_strlen(piton_exc_message));else if(piton_exc_value)piton_write(2,"<instancia>",10);piton_write(2,"\n",1);}
"""

_BIGINT_FREESTANDING_C = r"""
static unsigned long piton_bump_buf[1024*1024];
static unsigned long*piton_bump_ptr=piton_bump_buf;
static void*piton_bump_alloc(unsigned long n){void*r=(void*)piton_bump_ptr;piton_bump_ptr+=n;return r;}
typedef struct{int sign;long count;unsigned long capacity;unsigned long*limbs;}PitonBigInt;
static long bi_bit_width(unsigned long v){long w=0;while(v){v>>=1;++w;}return w;}
static void*bi_from_u64(unsigned long v);
/* BI_CMP_MAG_FIX_V1: the previous version computed a bit width by calling
 * bi_bit_width with a LIMB, but that function takes a PitonBigInt*, so it
 * reinterpreted the limb value as a pointer and produced a garbage width.
 * Comparing magnitudes by trimmed limb count and then limb by limb is exact
 * and needs no pointer arithmetic on limb values. */
static int bi_cmp_mag(PitonBigInt*a,PitonBigInt*b){long ac=a->count,bc=b->count;while(ac>0&&a->limbs[ac-1]==0)ac--;while(bc>0&&b->limbs[bc-1]==0)bc--;if(ac!=bc)return ac>bc?1:-1;for(long i=ac-1;i>=0;--i){if(a->limbs[i]!=b->limbs[i])return a->limbs[i]>b->limbs[i]?1:-1;}return 0;}
static void bi_ensure(PitonBigInt*r,long n){if(n<=0)n=1;if((unsigned long)n<=r->capacity)return;unsigned long c=r->capacity?r->capacity*2:2;while(c<(unsigned long)n)c*=2;unsigned long*p=(unsigned long*)piton_bump_alloc(c*sizeof(unsigned long));for(unsigned long i=0;i<r->count;++i)p[i]=r->limbs[i];for(unsigned long i=r->count;i<c;++i)p[i]=0;r->limbs=p;r->capacity=c;}
static void bi_trim(PitonBigInt*r){while(r->count>0&&r->limbs[r->count-1]==0)--r->count;if(r->count==0){r->count=1;r->sign=0;}}
static void bi_add_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){long max_c=a->count>b->count?a->count:b->count;bi_ensure(r,max_c+1);u128 carry=0;for(long i=0;i<=max_c;++i){if(i<a->count)carry+=a->limbs[i];if(i<b->count)carry+=b->limbs[i];r->limbs[i]=(unsigned long)carry;carry>>=64;r->count=i+1;}bi_trim(r);}
static void bi_sub_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){long max_c=a->count>b->count?a->count:b->count;bi_ensure(r,max_c);u128 borrow=0;for(long i=0;i<max_c;++i){u128 av=i<a->count?a->limbs[i]:0;u128 bv=i<b->count?b->limbs[i]:0;u128 diff=av-bv-borrow;r->limbs[i]=(unsigned long)diff;borrow=(diff>>127)&1;r->count=i+1;}bi_trim(r);}
static void*bi_from_u64(unsigned long v){PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;if(v){bi_ensure(r,1);r->limbs[0]=v;r->count=1;}return r;}
static void*piton_bigint_from_i64(long v){if(v==0)return bi_from_u64(0);int neg=v<0?-1:1;unsigned long av=(unsigned long)(v<0?-v:v);void*r=bi_from_u64(av);((PitonBigInt*)r)->sign=neg;return r;}
static void*piton_bigint_from_str(const char*s){PitonBigInt*a=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));a->sign=1;a->count=0;a->capacity=0;a->limbs=0;while(*s==' '||*s=='\t')++s;if(*s=='-'){a->sign=-1;++s;}else if(*s=='+'){++s;}while(*s>='0'&&*s<='9'){int digit=*s++-'0';u128 carry=0;for(long i=0;i<a->count;++i){carry+=(u128)a->limbs[i]*10;a->limbs[i]=(unsigned long)carry;carry>>=64;}if(carry){bi_ensure(a,a->count+1);a->limbs[a->count++]=(unsigned long)carry;}carry=digit;for(long i=0;i<a->count&&carry;++i){carry+=a->limbs[i];a->limbs[i]=(unsigned long)carry;carry>>=64;}if(carry){bi_ensure(a,a->count+1);a->limbs[a->count++]=(unsigned long)carry;}}bi_trim(a);return a;}
static int piton_bigint_is_zero(void*a){PitonBigInt*x=(PitonBigInt*)a;if(!x)return 1;for(long i=0;i<x->count;++i)if(x->limbs[i])return 0;return 1;}
static void piton_bigint_print_raw(void*a){if(piton_bigint_is_zero(a)){piton_write(piton_print_fd,"0",1);return;}PitonBigInt*x=(PitonBigInt*)a;char buf[64];int pos=sizeof(buf);buf[--pos]=0;unsigned long tmp[64];int tc=0;for(long i=0;i<x->count;++i)tmp[i]=x->limbs[i];tc=(int)x->count;while(tc>0){unsigned long carry=0;for(int i=tc-1;i>=0;--i){unsigned long cur=(carry<<32)|(tmp[i]>>32);unsigned long q1=cur/10;unsigned long r1=cur-q1*10;unsigned long mid=(r1<<32)|(tmp[i]&0xFFFFFFFF);unsigned long q2=mid/10;unsigned long r2=mid-q2*10;tmp[i]=(q1<<32)|q2;carry=r2;}buf[--pos]=(char)('0'+carry);while(tc>0&&tmp[tc-1]==0)--tc;}if(x->sign<0)buf[--pos]='-';piton_write(piton_print_fd,buf+pos,piton_strlen(buf+pos));}
static void piton_bigint_print(void*a){piton_bigint_print_raw(a);piton_write(piton_print_fd,"\n",1);}
static void*piton_bigint_add(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;if(x->sign==y->sign){r->sign=x->sign;bi_add_mag(r,x,y);}else{int c=bi_cmp_mag(x,y);if(c==0)return r;if(c>0){r->sign=x->sign;bi_sub_mag(r,x,y);}else{r->sign=y->sign;bi_sub_mag(r,y,x);}}bi_trim(r);return r;}
static void*piton_bigint_negate(void*a){PitonBigInt*x=(PitonBigInt*)a;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=-x->sign;r->count=x->count;r->capacity=x->capacity;r->limbs=x->limbs;return r;}
static void*piton_bigint_sub(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*negy=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));negy->sign=-y->sign;negy->count=y->count;negy->capacity=y->capacity;negy->limbs=y->limbs;return piton_bigint_add(x,negy);}
static void bi_mul_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){if(a->count==0||b->count==0){r->count=0;return;}long max_c=a->count+b->count;bi_ensure(r,max_c);for(unsigned long i=0;i<r->capacity;++i)r->limbs[i]=0;for(long i=0;i<a->count;++i){u128 carry=0;for(long j=0;j<b->count||carry;++j){u128 cur=r->limbs[i+j]+(u128)a->limbs[i]*(j<b->count?b->limbs[j]:0)+carry;r->limbs[i+j]=(unsigned long)cur;carry=cur>>64;}r->count=i+b->count+1;}bi_trim(r);}
static void*piton_bigint_mul(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=x->sign^y->sign;r->count=0;r->capacity=0;r->limbs=0;bi_mul_mag(r,x,y);return r;}
static void*piton_bigint_pow_small(long base,long exp){void*acc=piton_bigint_from_i64(1);void*b=piton_bigint_from_i64(base);while(exp>0){if(exp&1)acc=piton_bigint_mul(acc,b);exp>>=1;if(exp)b=piton_bigint_mul(b,b);}return acc;}
static long bi_cmp_magnitude(PitonBigInt*a,PitonBigInt*b){long ac=a->count,bc=b->count;while(ac>0&&a->limbs[ac-1]==0)ac--;while(bc>0&&b->limbs[bc-1]==0)bc--;if(ac!=bc)return ac>bc?1:-1;for(long i=ac-1;i>=0;--i){if(a->limbs[i]!=b->limbs[i])return a->limbs[i]>b->limbs[i]?1:-1;}return 0;}
static void bi_div_mod_internal(PitonBigInt*quot,PitonBigInt*rem,PitonBigInt*dividend,PitonBigInt*divisor){
  /* BI_DIV_REWRITE_V1: division binaria larga estandar. La version anterior
   * dejaba crecer rem->count hasta i+1 (la posicion del bit del dividendo):
   * con un dividendo de mas limbs que el divisor escribia fuera del arreglo.
   * Ahora rem solo crece cuando hay acarreo real, y queda acotado por
   * divisor*2 (dvc+1 limbs). */
  long dc=dividend->count;long dvc=divisor->count;
  bi_ensure(quot,dc);for(long i=0;i<dc;++i)quot->limbs[i]=0;quot->count=dc;
  bi_ensure(rem,dvc+2);rem->limbs[0]=0;rem->count=0;
  for(long i=dc-1;i>=0;--i){
    for(int b=63;b>=0;--b){
      unsigned long bit=(unsigned long)((dividend->limbs[i]>>b)&1UL);
      unsigned long carry=bit;
      for(long j=0;j<rem->count;++j){
        unsigned long nx=(rem->limbs[j]<<1)|carry;
        carry=rem->limbs[j]>>63;
        rem->limbs[j]=nx;
      }
      if(carry){rem->limbs[rem->count]=1;++rem->count;}
      if(bi_cmp_magnitude(rem,divisor)>=0){bi_sub_mag(rem,rem,divisor);quot->limbs[i]|=(1UL<<b);}
    }
  }
  bi_trim(quot);bi_trim(rem);
}
static void*piton_bigint_floor_div(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);q->sign=x->sign^y->sign;bi_trim(q);return q;}
static long piton_sum_seq_bigint(PitonSeq*s){void*acc=piton_bigint_from_i64(0);if(!s)return (long)acc;for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_BIGINT)acc=piton_bigint_add(acc,(void*)v.bits);else if(v.kind==PK_INT||v.kind==PK_BOOL)acc=piton_bigint_add(acc,piton_bigint_from_i64(v.bits));else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'str' and 'int'");return 0;}}return (long)acc;}
static long piton_sum_seq_float(PitonSeq*s){double r=0.0;if(!s)return piton_double_bits(r);for(long i=0;i<s->length;++i){PitonSlot v=s->items[i];if(v.kind==PK_FLOAT)r+=piton_bits_double(v.bits);else if(v.kind==PK_INT||v.kind==PK_BOOL)r+=(double)v.bits;else{piton_raise_set("TypeError","unsupported operand type(s) for +: 'str' and 'int'");return 0;}}return piton_double_bits(r);}
static void*piton_bigint_mod(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);if(r->count!=0&&r->sign!=0){PitonBigInt*ys=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));ys->sign=y->sign^1;ys->count=y->count;ys->capacity=y->capacity;ys->limbs=y->limbs;r= piton_bigint_add(r,ys);}bi_trim(r);return r;}
static void*bi_shl_u64(unsigned long v,long k){PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=1;r->count=0;r->capacity=0;r->limbs=0;if(!v||k<0)return r;long word=k/64;int bits=(int)(k%64);if(bits==0){bi_ensure(r,word+1);r->limbs[word]=v;r->count=word+1;}else{bi_ensure(r,word+2);r->limbs[word]=(v<<bits);r->limbs[word+1]=(v>>(64-bits));r->count=word+2;}bi_trim(r);return r;}
static int piton_bi_bit(void*a,long p){PitonBigInt*x=(PitonBigInt*)a;if(p<0)return 0;long i=p/64;int b=(int)(p%64);if(i>=x->count)return 0;return(int)((x->limbs[i]>>b)&1UL);}
static double piton_bigint_scaled_to_double(void*a,long E,int xsign){PitonBigInt*x=(PitonBigInt*)a;long p=-1;for(long i=x->count-1;i>=0;--i){unsigned long w=x->limbs[i];if(w){p=i*64+63-__builtin_clzll(w);break;}}unsigned long long signbit=((unsigned long long)(xsign?1:0))<<63;if(p<0){double d;__builtin_memcpy(&d,&signbit,8);return d;}unsigned long long t=0;int sticky=0;for(long i=0;i<55;++i){long pos=p-i;t=(t<<1)|(unsigned long long)(pos>=0?piton_bi_bit(a,pos):0);}long lo=p-54;if(lo>0){for(long i=0;i<lo;++i)if(piton_bi_bit(a,i)){sticky=1;break;}}int e2=(int)(p+E);int guard=(int)((t>>1)&1ULL),rnd=(int)(t&1ULL);t>>=2;if(guard&&(rnd||sticky||(t&1ULL))){t+=1;if(t>=(1ULL<<53)){t>>=1;e2+=1;}}if(e2>=-1022){if(e2>1023){unsigned long long b=signbit|0x7FF0000000000000ULL;double d;__builtin_memcpy(&d,&b,8);return d;}unsigned long long b=signbit|((unsigned long long)(e2+1023)<<52)|(t&0xFFFFFFFFFFFFFULL);double d;__builtin_memcpy(&d,&b,8);return d;}int s=-1022-e2;if(s>=64){double d;__builtin_memcpy(&d,&signbit,8);return d;}unsigned long long dropped=t&((s==64)?~0ULL:((1ULL<<s)-1));int g2=(int)((t>>(s-1))&1ULL);int r2=(s>1)&&((dropped&(((1ULL<<(s-1))-1)))!=0);t>>=s;if(g2&&(r2||sticky||(t&1ULL)))t+=1;if(t>=(1ULL<<52)){unsigned long long b=signbit|(1ULL<<52);double d;__builtin_memcpy(&d,&b,8);return d;}unsigned long long b=signbit|t;double d;__builtin_memcpy(&d,&b,8);return d;}
static double piton_fmod_core(double x,double y){unsigned long long ux,uy;__builtin_memcpy(&ux,&x,8);__builtin_memcpy(&uy,&y,8);int ex=(int)((ux>>52)&0x7FFULL),ey=(int)((uy>>52)&0x7FFULL);unsigned long long mx=ux&0xFFFFFFFFFFFFFULL,my=uy&0xFFFFFFFFFFFFFULL;int sx=(int)(ux>>63);if(ex==0x7FF||(ey==0x7FF&&my!=0)||(uy&0x7FFFFFFFFFFFFFFFULL)==0)return(x*y)/(x*y);if((ux&0x7FFFFFFFFFFFFFFFULL)==0)return x;int uex,uey;if(ex==0){int s=__builtin_clzll(mx)-11;mx<<=(unsigned)s;uex=-1074-s;}else{mx|=0x10000000000000ULL;uex=ex-1075;}if(ey==0){int s=__builtin_clzll(my)-11;my<<=(unsigned)s;uey=-1074-s;}else{my|=0x10000000000000ULL;uey=ey-1075;}if(uex<uey||(uex==uey&&mx<my))return x;long E=uex<uey?uex:uey;void*Mx=bi_shl_u64(mx,(long)(uex-E));void*My=bi_shl_u64(my,(long)(uey-E));void*R=piton_bigint_mod(Mx,My);return piton_bigint_scaled_to_double(R,E,sx);}
static int piton_double_isinf(long bits){unsigned long long u=(unsigned long long)bits;return ((u&0x7FF0000000000000ULL)==0x7FF0000000000000ULL)&&((u&0xFFFFFFFFFFFFFULL)==0);}
static int piton_double_isfinite(long bits){unsigned long long u=(unsigned long long)bits;return ((u&0x7FF0000000000000ULL)!=0x7FF0000000000000ULL);}

static void piton_slot_repr_into(PitonStrBuf*b,PitonSlot v){
  switch(v.kind){
    case PK_STR:piton_str_repr_into(b,(const char*)v.bits);break;
    case PK_BOOL:sb_puts(b,v.bits?"True":"False");break;
    case PK_NONE:sb_puts(b,"None");break;
    case PK_INT:sb_put_int(b,v.bits);break;
    case PK_FLOAT:{char t[PITON_REPR_MAX];int n=piton_repr_double(t,(unsigned long long)v.bits);
      for(int i=0;i<n;++i)sb_putc(b,t[i]);break;}
    case PK_LIST:case PK_TUPLE:piton_seq_repr_into(b,(PitonSeq*)v.bits);break;
    case PK_DICT:piton_dict_repr_into(b,(PitonDict*)v.bits);break;
    case PK_SET:piton_set_repr_into(b,(PitonSet*)v.bits);break;
    case PK_FROZENSET:sb_puts(b,"frozenset(");piton_set_repr_into(b,(PitonSet*)v.bits);sb_putc(b,')');break;
    case PK_BIGINT:{/* reimprimir por digitos */char buf[512];long pos=sizeof(buf);buf[--pos]=0;
      PitonBigInt*x=(PitonBigInt*)v.bits;unsigned long tmp[64];int tc=0;
      if(piton_bigint_is_zero(x)){sb_putc(b,'0');break;}
      for(long i=0;i<x->count;++i)tmp[i]=x->limbs[i];tc=(int)x->count;
      while(tc>0){unsigned long carry=0;for(int i=tc-1;i>=0;--i){unsigned long cur=(carry<<32)|(tmp[i]>>32);unsigned long q1=cur/10;unsigned long r1=cur-q1*10;unsigned long mid=(r1<<32)|(tmp[i]&0xFFFFFFFF);unsigned long q2=mid/10;unsigned long r2=mid-q2*10;tmp[i]=(q1<<32)|q2;carry=r2;}buf[--pos]=(char)('0'+carry);while(tc>0&&tmp[tc-1]==0)--tc;}
      if(x->sign<0)buf[--pos]='-';
      sb_puts(b,buf+pos);break;}
    case PK_RANGE:{PitonRange*r=(PitonRange*)v.bits;sb_puts(b,"range(");sb_put_int(b,r->start);sb_puts(b,", ");sb_put_int(b,r->stop);if(r->step!=1){sb_puts(b,", ");sb_put_int(b,r->step);}sb_putc(b,')');break;}
    case PK_BYTES:case PK_BYTEARRAY:{
      long n=0;const unsigned char*d=piton_bytes_get_data((void*)v.bits,&n);
      if(v.kind==PK_BYTEARRAY)sb_puts(b,"bytearray(");
      int is_ba=(v.kind==PK_BYTEARRAY);
      int has_sq=0,has_dq=0;
      for(long i=0;i<n;++i){if(d[i]=='\'')has_sq=1;else if(d[i]=='"')has_dq=1;}
      char qc=(has_sq&&!has_dq)?'"':'\'';
      sb_putc(b,'b');sb_putc(b,qc);
      for(long i=0;i<n;++i){unsigned char c=d[i];
        if(c=='\\')sb_puts(b,"\\\\");
        else if((char)c==qc){char e[2];e[0]='\\';e[1]=(char)c;sb_putc(b,e[0]);sb_putc(b,e[1]);}
        else if(c=='\n')sb_puts(b,"\\n");
        else if(c=='\t')sb_puts(b,"\\t");
        else if(c=='\r')sb_puts(b,"\\r");
        else if(c>=0x20&&c<=0x7E)sb_putc(b,(char)c);
        else{char h[5];h[0]='\\';h[1]='x';h[2]=(char)("0123456789abcdef"[(c>>4)&0xF]);h[3]=(char)("0123456789abcdef"[c&0xF]);h[4]=0;sb_puts(b,h);}
        (void)is_ba;}
      sb_putc(b,qc);
      if(v.kind==PK_BYTEARRAY)sb_putc(b,')');
      break;}
    case PK_OBJECT:{
      /* OBJECT_REPR_HOOK_V1: las colecciones se imprimen desde C puro, así que
       * no pueden llamar a un metodo Python. El emitter registra un hook
       * (clase -> direccion de __repr__) que este camino consulta. */
      extern long piton_object_repr_hook(long obj,const char*cls);
      long pr=piton_object_repr_hook(v.bits,((PitonObject*)v.bits)->class_name);
      if(pr)sb_puts(b,(const char*)pr);
      else sb_puts(b,"<object>");
      break;}
    default:sb_puts(b,"<object>");break;
  }
}
static long piton_slot_repr(long bits,int kind){PitonStrBuf b;sb_init(&b);piton_slot_repr_into(&b,(PitonSlot){bits,kind});return(long)b.d;}
static long piton_slot_repr_ascii(long bits,int kind){PitonStrBuf b;g_piton_ascii_mode=1;sb_init(&b);piton_slot_repr_into(&b,(PitonSlot){bits,kind});g_piton_ascii_mode=0;return(long)b.d;}

/* ASCII_V1: ascii() = repr() escapando no-ASCII. */
static void piton_str_repr_ascii_into(PitonStrBuf*b,const char*s){
  int has_sq=0,has_dq=0;
  for(usize i=0;s&&s[i];++i){if(s[i]=='\'')has_sq=1;else if(s[i]=='"')has_dq=1;}
  char qc=(has_sq&&!has_dq)?'"':'\'';
  sb_putc(b,qc);
  for(usize i=0;s&&s[i];){
    unsigned char c=(unsigned char)s[i];
    if(c<0x80){
      if(c=='\\')sb_puts(b,"\\\\");
      else if(c=='\n')sb_puts(b,"\\n");
      else if(c=='\t')sb_puts(b,"\\t");
      else if(c=='\r')sb_puts(b,"\\r");
      else if((char)c==qc){char e[2];e[0]='\\';e[1]=(char)c;sb_putc(b,e[0]);sb_putc(b,e[1]);}
      else sb_putc(b,(char)c);
      ++i;continue;
    }
    /* decodificar UTF-8 -> code point */
    long cp;int n;
    if((c&0xE0)==0xC0){cp=c&0x1F;n=2;}
    else if((c&0xF0)==0xE0){cp=c&0x0F;n=3;}
    else if((c&0xF8)==0xF0){cp=c&0x07;n=4;}
    else {cp=c;n=1;}
    for(int k=1;k<n;++k){unsigned char cc=(unsigned char)s[i+k];if((cc&0xC0)!=0x80){cp=c;n=1;break;}cp=(cp<<6)|(cc&0x3F);}
    i+=(usize)n;
    if(cp<0x100){char h[5];h[0]='\\';h[1]='x';h[2]=(char)("0123456789abcdef"[(cp>>4)&0xF]);h[3]=(char)("0123456789abcdef"[cp&0xF]);h[4]=0;sb_puts(b,h);}
    else if(cp<0x10000){char h[7];h[0]='\\';h[1]='u';
      h[2]=(char)("0123456789abcdef"[(cp>>12)&0xF]);h[3]=(char)("0123456789abcdef"[(cp>>8)&0xF]);
      h[4]=(char)("0123456789abcdef"[(cp>>4)&0xF]);h[5]=(char)("0123456789abcdef"[cp&0xF]);h[6]=0;sb_puts(b,h);}
    else{char h[11];h[0]='\\';h[1]='U';
      for(int k=0;k<8;++k)h[2+k]=(char)("0123456789abcdef"[(cp>>(4*(7-k)))&0xF]);
      h[10]=0;sb_puts(b,h);}
  }
  sb_putc(b,qc);
}
static long piton_hash_of_float(long bits){
  double x=piton_bits_double(bits);
  if(x==(double)(long)x&&x>-9.2e18&&x<9.2e18)return piton_hash_long((long)x);
  unsigned long u;__builtin_memcpy(&u,&x,8);
  return piton_hash_long((long)u);
}
static long piton_hash_str(const char*s);
static long piton_hash_long(long v);
static long piton_hash_of_float(long bits);
/* HASH_FROZENSET_V1: hash del conjunto = combinación de los hashes de sus
 * elementos (orden independiente: conjuntos iguales dan el mismo hash). */
static long piton_hash_set(PitonSet*s){
  /* combinación CONMUTATIVA (XOR de hash*primo): el orden de recorrido del
   * conjunto (tabla hash) no debe cambiar el resultado. */
  unsigned long h=0;
  if(!s||s->length==0)return 133146708735736L; /* hash(frozenset()) estable */
  for(long i=0;i<s->length;++i){
    unsigned long e;
    if(s->items[i].kind==PK_STR){e=(unsigned long)piton_hash_str((const char*)s->items[i].bits);}
    else if(s->items[i].kind==PK_FLOAT){e=(unsigned long)piton_hash_of_float((long)s->items[i].bits);}
    else{e=(unsigned long)piton_hash_long((long)s->items[i].bits);}
    h^=e*0x9E3779B97F4A7C15UL;
  }
  return(long)h;
}
static long piton_hash_str(const char*s){
  if(!s)return 0;
  unsigned long h=1469598103934665603UL;
  for(usize i=0;s[i];++i){h^=(unsigned char)s[i];h*=1099511628211UL;}
  return(long)h;
}

/* ROUND_NDIGITS_V1: round(x, n) byte-identico a CPython. CPython redondea
 * half-even sobre la REPRESENTACION DECIMAL EXACTA del double y devuelve el
 * double mas cercano a ese decimal (round(2.675, 2) es 2.67). No existe ruta
 * binaria que lo logre: x*10^n/10^n falla en el ultimo bit. La implementacion
 * usa la aritmetica bigint ya presente (exacta por construccion):
 *   1. x = m * 2^e exacto (del patron de bits).
 *   2. para n>=0: A = m*5^n exacto; R = round_half_even(A / 2^max(0,-e-n)).
 *   3. v = R / 10^n como double correctamente redondeado: se calcula
 *      Q = round_half_even((R<<64) / 10^n) y se convierte con
 *      piton_bigint_scaled_to_double(Q, -64) (redondeo half-even con sticky).
 *      Un double nunca cae exactamente en el punto medio entre dos doubles
 *      para n<=52, asi que el error de 2^-64 no puede cambiar el veredicto.
 *   4. para n<0: R = round_half_even(x / 10^-n) y el resultado es R * 10^-n
 *      convertido exactamente (multiplicar por 10^k es exacto en bigint).
 */
static void* bi_round_div(void*a,void*d){
  PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));
  PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));
  q->sign=1;q->count=0;q->capacity=0;q->limbs=0;
  r->sign=1;r->count=0;r->capacity=0;r->limbs=0;
  bi_div_mod_internal(q,r,(PitonBigInt*)a,(PitonBigInt*)d);
  void*r2=piton_bigint_add(r,r);
  int cmp=bi_cmp_magnitude((PitonBigInt*)r2,(PitonBigInt*)d);
  if(cmp>0||(cmp==0&&(q->limbs[0]&1)))q=piton_bigint_add(q,piton_bigint_from_i64(1));
  return q;
}
static void* bi_pow_small(long base,long exp){
  void*acc=piton_bigint_from_i64(1);void*b=piton_bigint_from_i64(base);
  for(long i=0;i<exp;++i)acc=piton_bigint_mul(acc,b);
  return acc;
}
static long piton_round_float_nd(long bits,long nd){
  double x=piton_bits_double(bits);
  if(x!=x)return bits;
  if(piton_double_isinf(bits))return bits;
  if(x==0.0)return bits;
  if(nd>=18)return bits;
  int neg=(x<0.0)||(x==0.0&&__builtin_signbit(x));
  if(neg)x=-x;
  unsigned long long u;__builtin_memcpy(&u,&x,8);
  int be=(int)((u>>52)&0x7FFULL);
  unsigned long long m=u&0xFFFFFFFFFFFFFULL;
  int e=(be==0)?-1074:(be-1075);
  if(be!=0)m|=0x10000000000000ULL;
  void*R;
  if(nd>=0){
    void*A=piton_bigint_mul(piton_bigint_from_i64((long)m),bi_pow_small(5,nd));
    long s=(long)e+nd;
    if(s>=0)R=piton_bigint_mul(A,bi_pow_small(2,s));
    else R=bi_round_div(A,bi_pow_small(2,-s));
    if(nd==0)return piton_double_bits(piton_bigint_scaled_to_double(R,0,neg));
    void*dividend=piton_bigint_mul(R,bi_pow_small(2,64));
    void*Q=bi_round_div(dividend,bi_pow_small(10,nd));
    return piton_double_bits(piton_bigint_scaled_to_double(Q,-64,neg));
  }
  long k=-nd;
  long sh=(long)e-k;
  void*A;
  if(sh>=0)A=piton_bigint_mul(piton_bigint_from_i64((long)m),bi_pow_small(2,sh));
  else A=piton_bigint_from_i64((long)m);
  void*D=bi_pow_small(5,k);
  if(sh<0)D=piton_bigint_mul(D,bi_pow_small(2,-sh));
  R=bi_round_div(A,D);
  return piton_double_bits(piton_bigint_scaled_to_double(piton_bigint_mul(R,bi_pow_small(10,k)),0,neg));
}
/* ROUND_DYN_V1: round(x[, n]) con tipo estatico desconocido (corpus
   tutorial/introduction.py: round(_, 2) con _ de una expresion previa).
   Despacha en runtime: double -> redondeo (entero sin ndigits, float con
   el); int/bits no-puntero -> identidad (CPython: round(int) es int).
   Cualquier otra cosa -> TypeError con el texto de CPython. */
static long piton_round_dyn(long bits,long has_nd,long nd){
  if(piton_is_double_bits(bits))return has_nd?piton_round_float_nd(bits,nd):piton_round_float(bits);
  if(!piton_maybe_ptr(bits))return bits;
  piton_raise_set("TypeError","type str doesn't define __round__ method");
  return 0;
}
static long piton_float_pow_v1(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0)return piton_double_bits(1.0);int neg=(x<0.0)&&(y==-1.0||y==1.0);double ax=neg?-x:x;if(y==1.0)return a;if(y==2.0||y==-2.0){double sq=ax*ax;long sb=piton_double_bits(sq);if(piton_double_isinf(sb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}if(y==2.0)return sb;if(x==0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}double r=1.0/sq;long rb=piton_double_bits(r);if(piton_double_isinf(rb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}return rb;}if(y==-1.0){if(ax==0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}double r=1.0/ax;if(neg)r=-r;long rb=piton_double_bits(r);if(piton_double_isinf(rb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}return rb;}if(y==0.5||y==-0.5){if(x<0.0){piton_raise_set("ValueError","negative number cannot be raised to a fractional power");return 0;}if(x==0.0){if(y<0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}return piton_double_bits(0.0);}long sb=piton_float_sqrt(a);if(y==0.5)return sb;double r=1.0/piton_bits_double(sb);return piton_double_bits(r);}piton_raise_set("ValueError","float ** with non-trivial exponent is not supported in the native subset");return 0;}
static long piton_float_mod(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float modulo");return 0;}double r=piton_fmod_core(x,y);if(r==0.0)r=(y<0.0?-0.0:0.0);else if((y<0.0)!=(r<0.0))r+=y;return piton_double_bits(r);}
/* ── COMPLEX_V1: números complejos ────────────────────────────────────
 * Representación: arena de 24 bytes {magic, re, im}. Escalares inmutables:
 * sin refcount (PK_COMPLEX queda fuera del rango refcounted). El repr sigue las
 * reglas observadas de CPython 3.12: parte real +0.0 sin signo -> forma imaginaria
 * pura ("1j", "-0j", "-1j"); en cualquier otro caso "(re±im j)" con el signo
 * tomado del BIT DE SIGNO de im (complex(2,-0.0) imprime "(2-0j)") y la magnitud
 * de fabs(im). La parte real/imaginaria enteras pierden el ".0" ("1", no "1.0").
 */
static PitonComplex*piton_complex_new(double re,double im){PitonComplex*c=(PitonComplex*)piton_alloc(sizeof(PitonComplex));c->magic=0x5049544E1C39L;c->re=re;c->im=im;return c;}
static double piton_cx_re(long b){return((PitonComplex*)b)->re;}
static double piton_cx_im(long b){return((PitonComplex*)b)->im;}
static PitonComplexVal piton_cx_of(long b){PitonComplex*c=(PitonComplex*)b;PitonComplexVal v;v.re=c->re;v.im=c->im;return v;}
static PitonComplexVal piton_cx_from_int(long b){PitonComplexVal v;v.re=(double)b;v.im=0.0;return v;}
static PitonComplexVal piton_cx_from_float(long b){PitonComplexVal v;v.re=piton_bits_double(b);v.im=0.0;return v;}
static long piton_cx_wrap(PitonComplexVal v){return(long)piton_complex_new(v.re,v.im);}
static long piton_cx_add(PitonComplexVal x,PitonComplexVal y){return(long)piton_complex_new(x.re+y.re,x.im+y.im);}
static long piton_cx_sub(PitonComplexVal x,PitonComplexVal y){return(long)piton_complex_new(x.re-y.re,x.im-y.im);}
static long piton_cx_mul(PitonComplexVal x,PitonComplexVal y){return(long)piton_complex_new(x.re*y.re-x.im*y.im,x.re*y.im+x.im*y.re);}
/* COMPLEX_DIV_V1: algoritmo de Smith de CPython (_Py_c_quot), no la fórmula
 * naive (ac+bd)/(c²+d²): los dos difieren en el último bit y el repr
 * diferencial exige byte-identical. */
static long piton_cx_div(PitonComplexVal a,PitonComplexVal b){
  double abs_breal=__builtin_fabs(b.re),abs_bimag=__builtin_fabs(b.im);
  double rr,ri;
  if(abs_breal>=abs_bimag){
    if(b.re==0.0){piton_raise_set("ZeroDivisionError","complex division by zero");return 0;}
    double ratio=b.im/b.re;
    double denom=b.re+b.im*ratio;
    rr=(a.re+a.im*ratio)/denom;
    ri=(a.im-a.re*ratio)/denom;
  }else{
    if(b.im==0.0){piton_raise_set("ZeroDivisionError","complex division by zero");return 0;}
    double ratio=b.re/b.im;
    double denom=b.re*ratio+b.im;
    rr=(a.re*ratio+a.im)/denom;
    ri=(a.im*ratio-a.re)/denom;
  }
  return(long)piton_complex_new(rr,ri);
}
static long piton_cx_neg(long b){PitonComplex*c=(PitonComplex*)b;return(long)piton_complex_new(-c->re,-c->im);}
static long piton_cx_eq(PitonComplexVal x,PitonComplexVal y){return(x.re==y.re&&x.im==y.im)?1:0;}
static int piton_cx_truthy_bits(long bits){PitonComplex*c=(PitonComplex*)bits;return(c->re!=0.0||c->im!=0.0)?1:0;}
/* HEX_FLOAT_V1: parsing de hex floats (0x1.23p+4, -0x1p-2, 0x1.921fb54442d18p+1).
 * Soporta: prefijo 0x/0X opcional, digitos hex [0-9a-fA-F], punto decimal opcional,
 * EXPONENTE OBLIGATORIO p/P con signo opcional y digitos decimales (base 2). */
static double piton_strtod_local(const char*s,char**end){
  const char*p=s;int neg=0;
  if(*p=='+'||*p=='-'){neg=(*p=='-');++p;}
  /* HEX FLOAT: detecta 0x/0X O p/P exponent (CPython acepta 1.5p+2 sin 0x) */
  int is_hex = 0;
  const char* p_save = p;
  if(*p=='0' && (p[1]=='x' || p[1]=='X')){
    is_hex = 1; p += 2;
  } else {
    /* peek ahead for p/P to decide hex mode */
    const char* q = p;
    while((*q>='0'&&*q<='9')||*q=='.')++q;
    if(*q=='p' || *q=='P') is_hex = 1;
  }
  if(is_hex){
    double mantissa = 0.0; int has_digits = 0;
    double div = 1.0; /* 16^(-num_frac_digits) */
    /* parte entera hex */
    while((*p>='0'&&*p<='9')||(*p>='a'&&*p<='f')||(*p>='A'&&*p<='F')){
      int d = (*p>='0'&&*p<='9')? *p-'0' : (*p>='a'&&*p<='f')? *p-'a'+10 : *p-'A'+10;
      mantissa = mantissa * 16.0 + d;
      has_digits = 1; ++p;
    }
    /* parte fraccionaria hex */
    if(*p == '.'){
      ++p;
      while((*p>='0'&&*p<='9')||(*p>='a'&&*p<='f')||(*p>='A'&&*p<='F')){
        int d = (*p>='0'&&*p<='9')? *p-'0' : (*p>='a'&&*p<='f')? *p-'a'+10 : *p-'A'+10;
        mantissa = mantissa * 16.0 + d;
        div *= 16.0; ++p; has_digits = 1;
      }
    }
    if(!has_digits){*end=(char*)s; return 0.0;}
    mantissa /= div;
    /* EXPONENTE p/P OBLIGATORIO en hex float */
    if(*p!='p' && *p!='P'){*end=(char*)s; return 0.0;}
    ++p;
    int exp_neg = 0;
    if(*p=='+' || *p=='-'){exp_neg=(*p=='-'); ++p;}
    int exp_val = 0; int has_exp = 0;
    while(*p>='0' && *p<='9'){exp_val = exp_val*10 + (*p-'0'); ++p; has_exp=1;}
    if(!has_exp){*end=(char*)s; return 0.0;}
    double result = mantissa;
    /* aplica exponente 2^exp SIN ldexp (no esta en -nostdlib) */
    if(exp_neg){
      while(exp_val--) result *= 0.5;
    }else{
      while(exp_val--) result *= 2.0;
    }
    *end = (char*)p;
    return neg ? -result : result;
  }
  /* DECIMAL (original) */
  double ip=0.0;int has=0;
  while(*p>='0'&&*p<='9'){ip=ip*10.0+(*p-'0');++p;has=1;}
  if(*p=='.'){++p;double div=1.0;while(*p>='0'&&*p<='9'){ip=ip*10.0+(*p-'0');div*=10.0;++p;has=1;}ip/=div;}
  if(!has){*end=(char*)s;return 0.0;}
  if(*p=='e'||*p=='E'){
    const char*q=p+1;int eneg=0;
    if(*q=='+'||*q=='-'){eneg=(*q=='-');++q;}
    if(*q>='0'&&*q<='9'){
      int ev=0;while(*q>='0'&&*q<='9'){ev=ev*10+(*q-'0');++q;}
      double f=1.0;for(int i=0;i<ev;++i)f*=10.0;
      if(eneg)ip/=f;else ip*=f;
      p=q;
    }
  }
  *end=(char*)p;
  return neg?-ip:ip;
}
static long piton_complex_from_str(const char*s){
  /* COMPLEX_STR_CTOR_V1: replica el parser acotado de CPython:
   *   [ws] [signo] numero [signo numero ['j']] [ws]     -> real + imag
   *   [ws] [signo] numero 'j' [ws]                     -> solo imaginaria
   *   [ws] 'j' | [ws] [signo] 'j'                      -> 1j / -1j
   * Sin espacios INTERIORES (complex('1 + 2j') es ValueError en CPython). */
  if(!s){piton_raise_set("ValueError","complex() arg is an empty string");return 0;}
  const char*p=s;
  while(*p==' '||*p=='\t'||*p=='\n'||*p=='\r')++p;
  double re=0.0,im=0.0;int has_im=0;
  /* imaginaria sola: 'j' o '-j' */
  if(*p=='j'||*p=='J'){
    const char*q=p+1;
    while(*q==' '||*q=='\t'||*q=='\n'||*q=='\r')++q;
    if(*q){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
    return(long)piton_complex_new(0.0,1.0);
  }
  if((*p=='+'||*p=='-')&&(p[1]=='j'||p[1]=='J')){
    double sv=(*p=='-')?-1.0:1.0;
    const char*q=p+2;
    while(*q==' '||*q=='\t'||*q=='\n'||*q=='\r')++q;
    if(*q){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
    return(long)piton_complex_new(0.0,sv);
  }
  /* parte real (con signo opcional) */
  char*end=0;
  double v=piton_strtod_local(p,&end);
  if(end==p){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
  p=end;
  if(*p=='j'||*p=='J'){
    /* el numero leido era la imaginaria */
    const char*q=p+1;
    while(*q==' '||*q=='\t'||*q=='\n'||*q=='\r')++q;
    if(*q){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
    return(long)piton_complex_new(0.0,v);
  }
  re=v;
  if(*p=='+'||*p=='-'){
    int neg=(*p=='-');++p;
    char*end2=0;
    double w=piton_strtod_local(p,&end2);
    if(end2==p){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
    p=end2;im=neg?-w:w;has_im=1;
    if(*p=='j'||*p=='J')++p;
    else{piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
  }
  while(*p==' '||*p=='\t'||*p=='\n'||*p=='\r')++p;
  if(*p){piton_raise_set("ValueError","complex() arg is a malformed string");return 0;}
  (void)has_im;
  return(long)piton_complex_new(re,im);
}
/* COMPLEX_POW_INT_V1: z ** n para n entero (square-and-multiply); n negativo
 * usa el inverso. Exponentes no enteros quedan fuera del subset (CPython usa
 * exp/log en plano complejo). */
static PitonComplexVal piton_cx_wrap_val(PitonComplexVal v){PitonComplex*c=(PitonComplex*)piton_alloc(sizeof(PitonComplex));c->magic=0x5049544E1C39L;c->re=v.re;c->im=v.im;return v;}
static long piton_cx_pow_int(long b,long n){
  PitonComplexVal base=piton_cx_of(b);
  if(n==0)return(long)piton_complex_new(1.0,0.0);
  int neg=n<0;
  unsigned long e=neg?(unsigned long)(-n):(unsigned long)n;
  if(neg){
    double d=base.re*base.re+base.im*base.im;
    if(d==0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}
    base.re=base.re/d;base.im=-base.im/d;
  }
  PitonComplexVal acc;acc.re=1.0;acc.im=0.0;
  while(e){
    if(e&1){double ar=acc.re*base.re-acc.im*base.im;double ai=acc.re*base.im+acc.im*base.re;acc.re=ar;acc.im=ai;}
    e>>=1;
    if(e){double br=base.re*base.re-base.im*base.im;double bi=base.re*base.im+base.im*base.re;base.re=br;base.im=bi;}
  }
  return(long)piton_complex_new(acc.re,acc.im);
}
static long piton_cx_abs(long b){PitonComplex*c=(PitonComplex*)b;return piton_double_bits(__builtin_sqrt(c->re*c->re+c->im*c->im));}
/* número formateado al estilo CPython complex_repr: sin ".0" final, conservando
 * el signo de -0.0 ("-0") */
static int piton_cx_fmt(char*out,double x){
  int n=0;
  if(__builtin_signbit(x)){out[0]='-';++out;n=1;x=-x;}
  unsigned long long u;__builtin_memcpy(&u,&x,8);
  int m=piton_repr_double(out,u);
  if(m>2&&out[m-2]=='.'&&out[m-1]=='0')m-=2;
  return n+m;
}
static int piton_cx_fmt_abs(char*out,double x){x=__builtin_fabs(x);unsigned long long u;__builtin_memcpy(&u,&x,8);int m=piton_repr_double(out,u);if(m>2&&out[m-2]=='.'&&out[m-1]=='0')m-=2;return m;}
static long piton_complex_repr_len(PitonComplex*c){
  char rb[PITON_REPR_MAX],ib[PITON_REPR_MAX];
  int rn=piton_cx_fmt(rb,c->re);
  if(c->re==0.0&&!__builtin_signbit(c->re)){
    char sb[PITON_REPR_MAX];int sn=piton_cx_fmt(sb,c->im);
    return sn+1;                                            /* "1j", "-0j", "-1j" */
  }
  int in_=piton_cx_fmt_abs(ib,c->im);
  return rn+in_+4;                                          /* "(re±im j)" */
}
static char*piton_complex_repr_write(char*p,PitonComplex*c){
  char rb[PITON_REPR_MAX],ib[PITON_REPR_MAX];
  int rn=piton_cx_fmt(rb,c->re),in_=piton_cx_fmt_abs(ib,c->im);
  if(c->re==0.0&&!__builtin_signbit(c->re)){
    char sb[PITON_REPR_MAX];int sn=piton_cx_fmt(sb,c->im);
    piton_memcpy(p,sb,(usize)sn);p[sn]='j';return p+sn+1;
  }
  p[0]='(';piton_memcpy(p+1,rb,(usize)rn);p[1+rn]=__builtin_signbit(c->im)?'-':'+';
  piton_memcpy(p+2+rn,ib,(usize)in_);p[2+rn+in_]='j';p[3+rn+in_]=')';return p+4+rn+in_;
}
static const char* piton_complex_str(long bits){PitonComplex*c=(PitonComplex*)bits;long n=piton_complex_repr_len(c);char*p=(char*)piton_alloc(n+1);piton_complex_repr_write(p,c);p[n]=0;return p;}
static void piton_print_complex_raw(long bits){PitonComplex*c=(PitonComplex*)bits;long n=piton_complex_repr_len(c);char*p=(char*)piton_alloc(n+1);piton_complex_repr_write(p,c);p[n]=0;piton_write(piton_print_fd,p,(usize)n);}
/* BIGINT_CMP_MIXED_V1: compare a bigint against a plain int. piton_bigint_cmp casts BOTH operands to PitonBigInt*, so `10 ** 20 > 5` dereferenced the integer 5 as a struct pointer and died with SIGSEGV. Returns the sign of (bigint - int) exactly like piton_bigint_cmp. */
/* Correct bit width of a bigint from its limb array.
 * bi_cmp_mag (and the mixed comparison) call bi_bit_width with a LIMB, but that
 * function takes a PitonBigInt*, so it reinterprets the limb value as a pointer.
 * It happens to read mapped heap (the limbs live there) and the limb-by-limb
 * loop afterwards usually rescues the answer, but it faults when the limb value
 * is not a readable address. This computes the width directly. */
static long bi_width_from_limbs(const unsigned long *limbs, long count) {
    for (long i = count - 1; i >= 0; --i) {
        if (limbs[i]) {
            long w = i * 64;
            unsigned long v = limbs[i];
            while (v) { ++w; v >>= 1; }
            return w;
        }
    }
    return 0;
}

static long piton_bigint_cmp_int(void*raw,long v){PitonBigInt*x=(PitonBigInt*)raw;if(!x)return v>0?-1:(v<0?1:0);int vs=v<0?-1:(v>0?1:0);if(x->sign!=vs)return x->sign?-1:1;unsigned long mag=v<0?(unsigned long)(-(v+1))+1UL:(unsigned long)v;long width=bi_width_from_limbs(x->limbs,x->count);
int mw=0;for(unsigned long t=mag;t;t>>=1)++mw;if(width!=mw)return width>mw?1:-1;for(long i=x->count-1;i>=0;--i){unsigned long lo=(i==0)?mag:0UL;if(x->limbs[i]!=lo)return x->limbs[i]>lo?1:-1;}
return 0;}
/* BI_CMP_SIGN_FIX_V1: `x->sign?-c:c` negated the result for POSITIVE
 * bigints, because a positive bigint carries sign == 1. Every ordering
 * comparison of two positive bigints was therefore inverted
 * (`10 ** 20 > 10 ** 30` answered True). The magnitude sign must only be
 * flipped for NEGATIVE values, as the Windows backend already did. */
static long piton_bigint_cmp(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;if(x->sign!=y->sign)return x->sign<y->sign?-1:1;int c=bi_cmp_mag(x,y);return x->sign<0?-c:c;}
static void piton_bigint_free(void*a){(void)a;}
"""


_C_RESERVED_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while",
}


def _name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if cleaned in _C_RESERVED_KEYWORDS:
        return f"p_{cleaned}"
    return f"piton_{cleaned}" if cleaned and cleaned[0].isdigit() else cleaned


def _float_c_literal(value: float) -> str:
    """C expression for a float constant.

    ``float.hex()`` is valid C for every finite double, but returns the bare
    strings ``'inf'`` and ``'nan'`` for the non-finite ones -- which made
    ``imprimir(1e309)`` fail to compile with "'inf' undeclared". Those are
    emitted through compiler builtins instead.
    """
    if value != value:
        return "(__builtin_inf()-__builtin_inf())"
    if value in (float("inf"), float("-inf")):
        return "__builtin_inf()" if value > 0 else "(-__builtin_inf())"
    return value.hex()


# BUILTIN_BASE_INIT_V1: bases builtin cuyo __init__ NO pertenece nunca a una
# clase de usuario; el receiver es el OBJETO de clase (no una instancia).
_BUILTIN_BASE_INIT = {
    "object", "Exception", "BaseException",
    "classmethod", "staticmethod", "property",
}


def _bytes_c_literal(b: bytes) -> str:
    return '"' + "".join(f"\\{x:03o}" for x in b) + '"'


def _str_c_literal(s: str) -> str:
    r"""STR_C_LITERAL_V1: literal C para un str de PITON.

    ``json.dumps`` parecia suficiente mientras todo era ASCII, pero C no
    acepta nombres universales surrogate (``\ud835``) ni codigos por debajo
    de 00A0: un literal non-BMP (html/entities.py) o con un caracter de
    control rompia gcc. Aqui el texto se emite como sus bytes UTF-8, con
    octal para todo lo que no sea ASCII imprimible (octal es acotado en C,
    a diferencia de ``\x``).
    """
    if not isinstance(s, str):
        s = str(s)
    parts = ['"']
    for byte in s.encode("utf-8", "surrogatepass"):
        if byte == 0x22:
            parts.append('\\"')
        elif byte == 0x5C:
            parts.append("\\\\")
        elif 0x20 <= byte < 0x7F:
            parts.append(chr(byte))
        else:
            parts.append(f"\\{byte:03o}")
    parts.append('"')
    return "".join(parts)


def _raise_or_propagate(out, function):
    """CALL_PROPAGATE_V1: a `raise` with no handler in THIS function must RETURN
    with the exception flag set, not exit: the CALLER owns the handler and
    routes via its exc check. Exiting here made `lanzar` inside a function
    invisible to the caller's `intentar`. Only the module top level and
    generators/coroutines (which cannot propagate across their driver) keep the
    terminal report-and-exit."""
    in_plain_function = (
        function.name != "<module>"
        and not getattr(function, "is_generator", False)
        and not getattr(function, "is_coroutine", False)
        and not getattr(function, "is_async_generator", False)
    )
    if in_plain_function:
        out.append("    return 0;")
    else:
        out.extend(["    piton_report_unhandled();", "    piton_exit(1);"])


# builtins that exist only as CALL targets: a value load of them is a real
# program error (they have no C value), but as a NAME they must not shadow a
# user variable of the same name (`suma = 0`).
_FUNC_ONLY_BUILTINS = frozenset({"suma", "redondear"})
# GLOBALS_NS_V1: globals()/locals() son callable-only (como `suma`): el
# load emite un marcador y el dispatch de la llamada construye el
# namespace dict. Sin esto el load cae en el NameError de runtime.
_FUNC_ONLY_BUILTINS |= {"globals", "locals"}
# ISINSTANCE_BUILTIN_CLASS_V1: clases builtin ESTANDAR. Se cargan con el
# prefijo "class:" igual que las clases de protocolo (os.PathLike), pero su
# isinstance se resuelve por TIPO (dict/list/object/...), no por nombre de
# clase del objeto.
_STANDARD_BUILTIN_CLASSES = frozenset({
    "int", "entero", "bool", "booleano", "str", "texto", "bytes", "octetos",
    "bytearray", "arreglo_bytes", "list", "lista", "tuple", "tupla", "dict",
    "diccionario", "set", "conjunto", "range", "rango", "complex", "complejo",
    "object", "objeto", "slice", "rebanada", "frozenset", "conjunto_inmutable",
})
# USAGE_PARAM_TYPE_V1: metodos que SOLO existen en str. Si todos los usos de
# un param como receiver son de este set (y ninguno de collection), el param
# se tipa str por uso (nturl2path url.replace, getopt args.startswith).
_USAGE_STR_METHODS = frozenset({
    "upper", "lower", "title", "capitalize", "strip", "lstrip", "rstrip",
    "split", "rsplit", "splitlines", "replace", "startswith", "endswith",
    "find", "rfind", "join", "zfill", "rjust", "ljust", "center", "encode",
    "isdigit", "isalpha", "isspace", "expandtabs", "partition", "rpartition",
    "format",
})
# metodos que demuestran que el receiver NO es str (collections/objects).
_USAGE_NONSTR_METHODS = frozenset({
    "append", "extend", "insert", "remove", "sort", "reverse", "clear",
    "items", "keys", "values", "update", "setdefault", "pop", "popitem",
    "add", "discard", "difference", "union", "intersection", "__next__",
    "close", "read", "write", "flush",
})
# USAGE_LIST_SEED_V1: metodos exclusivos de list entre las colecciones
# builtin (dict/set/str/tuple no los tienen) — prueban que el receiver es
# list (bisect: a.insert(lo, x) sobre el param a). Ambiguos (pop/clear/
# remove: list/dict/set comparten) NO cuentan como evidencia; str-methods
# y dict/set/object methods prueban que NO es list.
_USAGE_LIST_METHODS = frozenset({
    "append", "extend", "insert", "sort", "reverse",
})
# RECEIVER_TYPE_V1: metodos exclusivos de dict. El pre-pass no los ve porque
# el receiver de token.py `globals().items()` es un TEMPORARIO tras store, no
# un param — el seed no lo alcanza y el receiver queda como int. Se infiere en
# tiempo de uso (ver _infer_receiver_type) antes del dispatch.
_USAGE_DICT_METHODS = frozenset({
    "items", "keys", "values", "setdefault", "popitem",
})
# DICT_METHOD_EXCLUSIVE_V1: `update` NO es exclusivo de dict (set.update,
# hashlib HASH.update, ...) — usarlo como evidencia tipaba como dict un
# objeto hash (hashlib.file_digest: `digestobj.update(buf)`). Los metodos
# de arriba son los exclusivos de dict que el corpus usa.
_USAGE_NONLIST_METHODS = frozenset(
    _USAGE_STR_METHODS
    | {
        "items", "keys", "values", "update", "setdefault", "popitem",
        "add", "discard", "difference", "union", "intersection",
        "__next__", "close", "read", "write", "flush",
    }
)


class LinuxCEmitter:
    def __init__(self) -> None:
        self.function_names: set[str] = set()
        self.generator_layouts: dict[str, dict[str, int]] = {}
        self.function_params: dict[str, list[str]] = {}
        self.function_return_types: dict[str, str] = {}
        self._func_globals: dict[str, set[str]] = {}
        self._func_stores: dict[str, set[str]] = {}
        self._module_stored: set[str] = set()
        self._module_types: dict[str, str] = {}
        self._shared_globals: set[str] = set()
        self._fn_consts: dict[str, Any] = {}
        self._tuple_elems: dict[str, tuple] = {}
        # DICT_KEY_TYPE_V1: static type of a dict's KEYS, so iterating a dict
        # yields the right static type (it used to hardcode "str", which made
        # `para k en {1: 'a'}` print the int key 1 as a string pointer -> SIGSEGV).
        self._dict_key_types: dict[str, str] = {}
        # COLL_ELEM_TYPE_V1: static type of a collection's ELEMENTS, so a
        # builtin like sum() can tell what it is accumulating instead of
        # assuming int (which made it add raw pointers for bigints).
        self._coll_elems: dict[str, str] = {}
        # MEMORYVIEW_STRIDE_V1: temps cuya vista NO es contigua (stride != 1).
        self._mv_noncontig: set[str] = set()
        self._tuple_list_elems: dict[str, tuple[str, str]] = {}
        # NESTED_TUPLE_LIST_V1: una LISTA DE TUPLAS (p.ej. stat._filemode_table)
        # anota su estructura por indice: cada temp que la referencia lleva una
        # tupla de tipos (uno por campo de la tupla elemental). iter_new copia la
        # anotacion al iterador; iter_next la copia al valor rendido; unpack_check
        # y get_item la consultan para tipar cada desempaquetado. Es el anlogo de
        # _tuple_list_elems pero con profundidad/aridad arbitraria (no 2-tupla fija).
        self._nested_tuple_lists: dict[str, tuple[str, ...]] = {}
        self._iter_source: dict[str, str] = {}
        self._enum_elem: dict[str, str] = {}
        # CALLITER_ELEM_V1: tipo del elemento de `iter(f, sent)`.
        self._calliter_elem: dict[str, str] = {}
        # CALLITER_ELEM_V1: closure -> funcion de origen (para el tipo de retorno).
        self._closure_origin: dict[str, str] = {}
        self._files: dict[str, bool] = {}
        self._builtin_bound: dict[str, str] = {}
        # builtins that are only ever CALLED (never a value): marking them
        # as a builtin value broke `suma = 0` as an ordinary variable.
        self._func_only_builtins: set[str] = set()
        self._boolh_line: dict[str, tuple[int, str, str]] = {}
        self._iter_source_r: dict[str, str] = {}
        # DICT_VAL_TYPE_V1: static type of a dict's VALUES, so `d[k]` and
        # `d.get(k)` return a correctly typed result instead of an int
        # slot holding a str/bigint pointer (printed as an address).
        self._dict_val_types: dict[str, str] = {}
        self._dict_empty: dict[str, bool] = {}
        self._dict_elems: dict[str, dict[str, tuple[str, str]]] = {}
        self._gen_layout: dict[str, int] = {}
        self._gen_resumes: list[str] = []
        self._gen_counter = 0
        # MODULE_ATTR_TYPES_V1: tracks types of module attributes set via set_attr
        self.module_attr_types: dict[str, str] = {
            "__name__": "str",
            "__file__": "str",
            "__package__": "str",
            "modules": "dict:module",
        }

    def emit(self, module: MIRModule) -> str:
        self.mir_module = module
        self.classes = getattr(module, "classes", {})
        self.class_parents = getattr(module, "class_parents", {})
        self.class_mro = getattr(module, "class_mro", {})
        # CLASS_BASES_V1: las bases directas de cada clase (`cls.__bases__`).
        self.class_bases = getattr(module, "class_bases", {})
        self.class_properties = getattr(module, "class_properties", {})
        self.static_methods = getattr(module, "static_methods", set())
        self.class_methods = getattr(module, "class_methods", set())
        self._module_funcs = list(module.functions)
        self.function_names = {function.name for function in module.functions}
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
        # USAGE_PARAM_TYPE_V1: tipo inferido por uso de cada param, por
        # funcion — el call-site lo consulta para rechazar (fail-closed)
        # argumentos de tipo conocido contradictorio.
        self._usage_param_types: dict[str, dict[str, str]] = {f.name: {} for f in module.functions}
        # CALL_SEED_V1: tipo de param sembrado desde un call-site (CALL_ARG_TYPE_V1)
        # — best-effort, NO es contrato fail-closed: el cuerpo del callee puede
        # no usar el param en absoluto (re.compile es un stub), y otro call-site
        # con un tipo distinto (re.compile(b'...')) no debe rechazarse.
        self._param_seeds: dict[str, dict[str, str]] = {f.name: {} for f in module.functions}
        # ELEM_KIND_V1: elemento de una lista parametrica deducido por uso
        # (ej. getopt: args[0].startswith -> elem str).
        self._usage_param_elems: dict[str, dict[str, str]] = {f.name: {} for f in module.functions}
        # AMBIG_SEED_V1: seeds AMBIGUOS (indice+slice no distinguen str de
        # list) — el call-site los reconcilia con el tipo real del argumento
        # (getopt: do_shorts(optstring) recibe un str, no una list).
        self._usage_param_weak: dict[str, dict[str, bool]] = {f.name: {} for f in module.functions}
        # CALL_ARG_TYPE_V1: tipo de un param deducido del call-site (el
        # argumento conocido): getopt do_longs(opt) recibe `args[0][2:]` (str)
        # y `opt.index('=')` no tiene seed local (index es metodo de str Y
        # de list). Solo se aplica a params usados como secuencia.
        self._call_param_types: dict[str, dict[str, str]] = {}
        self._param_seq_use: dict[str, set[str]] = {}
        # RUNTIME_TYPE_PARAMS_V1: params cuyo tipo se decide EN RUNTIME
        # (isinstance/issubclass, y el match de patrones que las usa) — no
        # pueden recibir un tipo estatico del call-site, o el dispatch por
        # tipo se resuelve en build y no en runtime.
        self._runtime_type_params: dict[str, set[str]] = {}
        # STORE_TARGETS_V1: nombres <- valor (para propagar elem-kinds que
        # aparecen despues del store).
        self._store_targets: dict[str, set[str]] = {}
        # INSTANCE_ATTR_TYPES_V1: {clase: {atributo: tipo}} — el tipo de un
        # atributo de instancia se deduce del `self.x = valor` de sus
        # metodos (warnings: self._filters[:] — sin el tipo, el slice de un
        # valor sin tipo falla cerrado).
        self._instance_attr_types: dict[str, dict[str, str]] = {}
        # CELL_TYPE_V1: el tipo de una variable CAPTURADA por una closure
        # (la celda) — sin el, la carga de la celda decae a "int" y las
        # operaciones sobre ella (un `+` de tuplas, un slice) fallan.
        self._cell_types: dict[str, str] = {}
        # el contador de temporales del empaquetado de varargs
        self._vararg_pack_counter = 0
        # IDENTITY_FN_V1: funciones que devuelven su param sin cambios
        # (getopt: `def _(msg): return msg`, el stub de gettext). Su
        # resultado ES el argumento, asi que un `%` con ese resultado tiene
        # plantilla literal.
        self._identity_params: dict[str, int] = {}
        self._identity_templates: dict[str, str] = {}
        self.function_params = {function.name: list(function.params) for function in module.functions}
        self.function_varargs = {function.name: function.vararg for function in module.functions}
        self.function_kwargs = {function.name: function.kwarg for function in module.functions}
        self.function_frame_abi = {function.name: bool(function.frame_abi) for function in module.functions}
        self._scan_module_globals(module)
        self._scan_usage_param_types(module)
        self._scan_identity_functions(module)
        self.function_return_types = self._infer_return_types(module)
        self.generator_layouts = {}
        for function in module.functions:
            if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                self.generator_layouts[function.name] = generator_slot_layout(function)
        # A printed collection decodes its elements by slot kind, so the bigint
        # runtime must be present whenever a collection is printed: otherwise
        # `case PK_BIGINT` in piton_print_slot would reference a function that
        # was never emitted (PK_BIGINT_SLOT_PRINT_V1).
        _print_temps = {
            instruction.result
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "load" and instruction.args
            and str(instruction.args[0]).strip("'\"") in {"imprimir", "print"}
        }
        _prints_collection = any(
            instruction.op == "call" and instruction.args and instruction.args[0] in _print_temps
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        )
        # REPR_RUNTIME_V1: los metodos __repr__/__ascii__ de clases (y el
        # builtin repr/ascii) emiten piton_slot_repr* del rich runtime;
        # sin esta deteccion __future__.py (solo define clases) compilaba
        # sin el prelude -> implicit declaration / never defined en ld.
        _repr_temps = {
            instruction.result
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "load" and instruction.args
            and str(instruction.args[0]).strip("'\"") in {"repr", "ascii", "repr_ascii"}
        }
        _uses_repr_runtime = any(f.name.endswith("____repr__") or f.name.endswith("____ascii__")
                                 for f in module.functions) or any(
            instruction.op == "call" and instruction.args and instruction.args[0] in _repr_temps
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        )
        self._has_bigint = any(
            instruction.op == "const" and instruction.args
            and isinstance(instruction.args[0], int) and abs(instruction.args[0]) > 9223372036854775807
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        ) or self._const_fold_overflows(module) or self._uses_float_mod(module) or _prints_collection
        # Rich runtime (with __argc/__argv/_start and object/dict/set structs) needed for
        # bigint or sys.argv/os.name or any object/dict/set/bytes operations
        self._has_rich_runtime = self._has_bigint or _uses_repr_runtime or any(
            instruction.op in {"object_new", "set_attr", "build_collection", "get_attr", "get_item", "collection_len", "list_append", "set_add", "dict_put", "sys_argv", "builtin_class", "os_fspath"}
            or (instruction.op == "const" and instruction.args and isinstance(instruction.args[0], (bytes, bytearray)))
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        )
        lines = [
            "typedef long i64; typedef unsigned long usize; typedef unsigned long long u64; typedef unsigned __int128 u128;",
            "/* NONE_SINGLETON_V2: None tiene direccion PROPIA (16 bytes: los inspectores de valor leen ptr[1] para el kind; con 8 bytes leian fuera del array). Antes None era el entero 0: el ABI pasa longs sin kind, asi que `b es Nada` sobre un parametro sin tipo era falso por construccion e `imprimir(None)` mostraba 0. La identidad de None es su direccion. */",
            "static const char piton_none_singleton[16]={'N','O','N','E','V','A','L',0,0,0,0,0,0,0,0,0};",
            "/* ELLIPSIS_SINGLETON_V1: `...` (Ellipsis) es otro singleton con identidad propia: antes el emisor no podia codificarlo y cualquier archivo que lo usara (types.py, _types.py) era fail-closed en build. Misma politica que None: la identidad es la direccion y la impresion da 'Ellipsis'. A diferencia de None, Ellipsis ES truthy en CPython — la verdad de unknown ya lo trata como verdadero (puntero no nulo). */",
            "static const char piton_ellipsis_singleton[16]={'E','L','L','I','P','S','I','S',0,0,0,0,0,0,0,0};",
            "#define PITON_ELLIPSIS ((long)&piton_ellipsis_singleton)",
            "#define PITON_NONE ((long)&piton_none_singleton)",

            "static long piton_write(long fd,const void*buf,usize n){long r;__asm__ volatile(\"syscall\":\"=a\"(r):\"a\"(1L),\"D\"(fd),\"S\"(buf),\"d\"(n):\"rcx\",\"r11\",\"memory\");return r;}",
            "__attribute__((noreturn)) static void piton_exit(long code){__asm__ volatile(\"syscall\"::\"a\"(60L),\"D\"(code):\"rcx\",\"r11\",\"memory\");__builtin_unreachable();}",
            "static usize piton_strlen(const char*s){usize n=0;while(s[n])++n;return n;}",
            "static int piton_strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return (unsigned char)*a-(unsigned char)*b;}",
            "/* PRINT_FD_V1: el descriptor de salida de print. Las impressionadoras\n               nativas respetan el fd activo (1 por defecto) — asi\n               print(..., file=X) escribe donde corresponde. */\n               static long piton_print_fd=1;",
            # GLOBALS_NS_V1: el namespace per-modulo que devuelve globals().
            # Es un dict (lazy) para reutilizar todo el camino dict (get/set,
            # metodos, iteracion). Parcial y documentado: solo contiene los
            # nombres escritos a traves de globals()[...]; los globales del
            # modulo compilados como static long no se reflejan.
            "static long piton_globals_ns=0;",
            "static void piton_print_str_raw(const char*s){if(s==(const char*)&piton_none_singleton){piton_write(piton_print_fd,\"None\",4);return;}if(!s){piton_write(piton_print_fd,\"None\",4);return;}piton_write(piton_print_fd,s,piton_strlen(s));}",
            "static void piton_print_str(const char*s){if(s==(const char*)&piton_none_singleton){piton_write(piton_print_fd,\"None\",4);return;}piton_print_str_raw(s);piton_write(piton_print_fd,\"\\n\",1);}",
            "static void piton_print_dynamic(long bits){if(bits==(long)&piton_none_singleton){piton_write(piton_print_fd,\"None\",4);return;}if(bits==(long)&piton_ellipsis_singleton){piton_write(piton_print_fd,\"Ellipsis\",8);return;}if(!bits){piton_write(piton_print_fd,\"None\",4);}else{piton_write(piton_print_fd,(const char*)bits,piton_strlen((const char*)bits));}}",
            "static void piton_write_int(i64 number){char b[32];usize i=sizeof(b);unsigned long value;if(number<0){piton_write(piton_print_fd,\"-\",1);value=0-(unsigned long)number;}else value=(unsigned long)number;do{b[--i]=(char)(\'0\'+value%10);value/=10;}while(value);piton_write(piton_print_fd,b+i,sizeof(b)-i);}",
            "static void piton_print_int_raw(i64 number){piton_write_int(number);}",
            "static void piton_print_int(i64 number){piton_print_int_raw(number);piton_write(piton_print_fd,\"\\n\",1);}",
            "static i64 piton_floor_div(i64 a,i64 b){i64 q=a/b,r=a%b;if(r&&((r<0)!=(b<0)))--q;return q;}",
            "static i64 piton_mod(i64 a,i64 b){i64 r=a%b;if(r&&((r<0)!=(b<0)))r+=b;return r;}",
            "static char piton_concat_buf[65536];static char*piton_concat_ptr=0;",
            "static long piton_str_concat(const char*a,const char*b){if(!piton_concat_ptr)piton_concat_ptr=piton_concat_buf;usize la=piton_strlen(a),lb=piton_strlen(b);char*r=piton_concat_ptr;for(usize i=0;i<la;++i)r[i]=a[i];for(usize i=0;i<lb;++i)r[la+i]=b[i];r[la+lb]=0;piton_concat_ptr+=la+lb+1;return(long)r;}",
        ]
        if self._has_rich_runtime:
            lines.append(_FLOAT_REPR_C)
            lines.append(_RICH_FREESTANDING_C)
        if self._has_bigint or _uses_repr_runtime:
            # REPR_RUNTIME_V1: el runtime de repr/ascii (piton_slot_repr_into
            # y familia) vive en el mismo string C que bigint; los metodos
            # __repr__ sin bigint (__future__.py: solo clases) lo necesitan.
            bigint_code = _BIGINT_FREESTANDING_C
            lines.extend(bigint_code.split("\n"))
        for function in module.functions:
            if function.name != "<module>":
                if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                    lines.append(f"static long {_name(function.name)}(PitonGenerator *piton_gen);")
                    continue
                params = "long *__piton_frame" if function.frame_abi else ", ".join(f"long {_name(param)}" for param in function.params) or "void"
                lines.append(f"static long {_name(function.name)}({params});")
        if self._shared_globals:
            # GLOBAL_DECL_V1: module-level names shared cross-function live
            # in file scope so every function references the same variable.
            for name in sorted(self._shared_globals):
                lines.append(f"static long {_name(name)};")
        # CLASS_OBJECT_INTERN_V1: cada clase tiene UN solo objeto de clase
        # (a nivel de archivo) — sin interning, cada carga del nombre creaba
        # uno nuevo y la IDENTIDAD fallaba (`A is A` -> False; `A en (A,)` ->
        # False). El objeto se crea la primera vez que se usa.
        for cls_name in sorted(self.classes):
            lines.append(f"static long {_name('_clsobj_' + cls_name)};")
        if self._has_rich_runtime:
            # Forward declarations for runtime helpers used in <module>
            lines.append("static long piton_argv_new(void);")
        # CELL_SCOPE_ORDER_V1: una closure (funcion elevada) se baja DESPUES
        # de la funcion que la contiene: la celda (y su tipo) se crea en el
        # cuerpo del padre, asi que la carga de la celda en la closure ya
        # conoce el tipo capturado. Sin este orden, el tipo de una variable
        # capturada se perdia (la closure se compilaba antes que el padre).
        # TWO_PASS_TYPES_V1: los tipos de los params de una funcion se
        # deducen de los CALL-SITES (que pueden estar en otra funcion, mas
        # tarde en el orden de emision). Con una sola pasada, un atributo
        # de instancia o una celda de closure se quedan sin tipo y las
        # operaciones que lo necesitan fallan cerradas. Se emite dos veces:
        # la primera pasada recoge los tipos de los call-sites, la segunda
        # los usa. La primera pasada se descarta.
        # El orden natural del MIR ya pone al padre antes que a la closure
        # (la closure se anade al bajar el cuerpo del padre) y conserva el
        # orden del cuerpo de la clase — no se reordena.
        _order = list(module.functions)
        for _pass in (0, 1):
            # el estado del `borrar` es POR PASADA (la primera pasada no
            # debe dejar el nombre borrado para la segunda).
            _del_state = (
                set(self._deleted_names),
                {k: set(v) for k, v in self._deleted_by_function.items()},
                dict(self._deleted_at),
            )
            _pass_lines: list[str] = []
            for function in _order:
                _pass_lines.extend(self._emit_function(function))
            if _pass == 0:
                # se descarta la primera pasada, pero sus tipos (los de los
                # call-sites) quedan en los mapas del emitter.
                self._deleted_names = _del_state[0]
                self._deleted_by_function = _del_state[1]
                self._deleted_at = _del_state[2]
                continue
            lines.extend(_pass_lines)
        if self._has_rich_runtime:
            lines.append("__attribute__((used)) int __argc=0;__attribute__((used)) char**__argv=0;")
            lines.append("static long piton_argv_new(void){PitonSeq*s=piton_seq_new(PK_LIST,__argc);for(int i=0;i<__argc;++i){piton_seq_put(s,i,(PitonSlot){(long)__argv[i],PK_STR});}return(long)s;}")
            lines.append('__attribute__((naked)) void _start(void){__asm__ volatile("mov (%rsp),%rax;mov %rax,__argc(%rip);lea 8(%rsp),%rax;mov %rax,__argv(%rip);xor %rbp,%rbp;call piton_main;mov %rax,%rdi;mov $60,%eax;syscall");}')
        return "\n".join(lines) + "\n"

    def _emit_function(self, function: MIRFunction) -> list[str]:
        if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
            return self._emit_generator_function(function)
        is_main = function.name == "<module>"
        params = "long *__piton_frame" if function.frame_abi else ", ".join(f"long {_name(param)}" for param in function.params) or "void"
        signature = "long piton_main(void)" if is_main else f"static long {_name(function.name)}({params})"
        slots = set(function.params)
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.result:
                    slots.add(instruction.result)
                if instruction.op == "store":
                    slots.add(instruction.args[0])
        self._cur_slots = slots
        locals_ = sorted((slots if function.frame_abi else slots - set(function.params)) - self._shared_globals)
        lines = [signature + " {"]
        # OBJECT_REPR_HOOK_V1: registrar __repr__ de clases para el repr de C.
        if is_main:
            for _cls in sorted(self.classes):
                for _cand in self.class_mro.get(_cls, [_cls]):
                    _target = f"{_cand}____repr__"
                    if "__repr__" in self.classes.get(_cand, set()) and _target in self.function_names:
                        lines.append(f"    piton_object_repr_hook_set({json.dumps(_cls)},(long)&{_name(_target)});")
                        break
        # the emitted lines of THIS function: a mixed-type y/o rewrites the
        # line of its first arm, which lives here (not in the per-op buffer).
        self._cur_lines = lines
        if not getattr(self, "_known_names", None):
            known: set[str] = set()
            for _fn in self._module_funcs:
                known.update(_fn.params)
                for _b in _fn.blocks:
                    for _i in _b.instructions:
                        if _i.op == "store" and isinstance(_i.args[0], str):
                            known.add(_i.args[0])
                        for _a in _i.args:
                            if isinstance(_a, str) and not _a.startswith("%") and not _a.startswith("@") and not _a.startswith("__"):
                                known.add(_a)
            self._known_names = known
        if locals_:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot in locals_) + ";")
        # VARARG_PACK_V1: pool de temporales para las tuplas de *args (el
        # empaquetado ocurre durante la emision de la llamada, donde el bloque
        # de locals ya esta escrito).
        lines.append("    long _vapack0=0, _vapack1=0, _vapack2=0, _vapack3=0;")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        self._fn_consts = {}
        self._boolh_types: dict[str, str] = {}
        self._tuple_elems = {k: v for k, v in self._tuple_elems.items() if not k.startswith('%')}
        self._dict_key_types = {k: v for k, v in self._dict_key_types.items() if not k.startswith('%')}
        self._coll_elems = {k: v for k, v in self._coll_elems.items() if not k.startswith('%')}
        self._dict_val_types = {k: v for k, v in self._dict_val_types.items() if not k.startswith('%')}
        self._chr_results = {k for k in getattr(self, '_chr_results', set()) if not k.startswith('%')}
        self._dict_elems = {k: v for k, v in self._dict_elems.items() if not k.startswith('%')}
        if function.vararg:
            types[function.vararg] = "tuple"
        if function.kwarg:
            types[function.kwarg] = "dict"
        # M6 SEQ_PARAM_DEFAULT_V1: un param cuyo default estatico es
        # list/tuple hereda ese tipo dentro del callee (el ABI no trae tipos;
        # sin esto la collection default se imprimia como puntero crudo).
        # El caller rechaza escalar explicito en ese param (ver
        # _emit_user_function_call), asi que la anotacion no se contradice
        # con argumentos observados.
        for _pd_param, _pd_value in zip(function.params, function.defaults or []):
            if isinstance(_pd_value, tuple):
                types[_pd_param] = "tuple"
                _pd_elems = []
                for _item in _pd_value:
                    if isinstance(_item, str):
                        _pd_elems.append((_item, "str"))
                    elif isinstance(_item, bool):
                        _pd_elems.append((_item, "bool"))
                    elif isinstance(_item, int):
                        _pd_elems.append((_item, "int"))
                    elif _item is None:
                        _pd_elems.append((_item, "none"))
                    else:
                        _pd_elems.append((_item, "float"))
                self._tuple_elems[_pd_param] = tuple(_pd_elems)
            elif isinstance(_pd_value, list):
                types[_pd_param] = "list"
            elif isinstance(_pd_value, str):
                # M6 STR_PARAM_DEFAULT_V1: misma familia que SEQ_PARAM_DEFAULT
                # (bug preexistente: `devolver (a, b)` con default str imprimia
                # el puntero del param como int).
                types[_pd_param] = "str"
        # USAGE_PARAM_TYPE_V1: el pre-pass de emit() escanea TODAS las
        # funciones ANTES de emitir (el call de <module> a una funcion
        # definida despues ya necesita el seed); aqui solo se aplica al
        # types local del callee.
        for _uparam, _uutype in (self._usage_param_types.get(function.name) or {}).items():
            types[_uparam] = _uutype
        # CALL_ARG_TYPE_V1: si el param no tiene seed local y el call-site
        # conoce el tipo del argumento, usarlo. El tipo del call-site es la
        # verdad del valor en esa llamada; un store posterior con valor
        # TIPADO re-anota el param (el SEED_KEEP_V1 solo protege frente a
        # stores sin tipo). getopt: do_longs(opt) <- str.
        for _cparam, _ctype in (self._call_param_types.get(function.name) or {}).items():
            if (
                types.get(_cparam) in {None, "int"}
                and _ctype != "int"
                and _cparam not in (self._runtime_type_params.get(function.name) or ())
            ):
                types[_cparam] = _ctype
                # el tipo del call-site pasa a ser seed: lo protege el
                # SEED_KEEP_V1 frente a stores de valor sin tipo. NO se
                # escribe en _usage_param_types: ese dict es el contrato
                # "usado como" derivado del CUERPO del callee; un seed de
                # call-site no debe convertirse en rechazo fail-closed
                # (glob: re.compile(str) seguido de re.compile(bytes)).
                self._param_seeds.setdefault(function.name, {})[_cparam] = _ctype
        if function.self_class and function.params:
            types[function.params[0]] = f"object:{function.self_class}"
        # WITH_PROTOCOL_V1: __exit__(self, tipo, mensaje, tb) receives the
        # exception type-name and message as strings (V1: type NAME, not the
        # exception object; traceback is passed as None).
        if function.name.endswith("__exit__") and len(function.params) >= 3:
            types[function.params[1]] = "str"
            types[function.params[2]] = "str"
        if function.frame_abi:
            for index, param in enumerate(function.params):
                lines.append(f"    {_name(param)}=__piton_frame[{index}];")
        bigint_slots: list[str] = []
        for _block_index, block in enumerate(function.blocks):
            self._cur_block = _block_index
            lines.append(f"{_name(function.name + '_' + block.label)}:")
            for instruction in block.instructions:
                lines.extend(self._emit_instruction(instruction, function, aliases, types, bigint_slots))
            if not block.instructions or block.instructions[-1].op not in {"jump", "branch", "return"}:
                lines.append(f"    goto {_name(function.name + '___exit')};")
        for slot in bigint_slots:
            lines.append(f"    piton_bigint_free((void*){_name(slot)});")
            lines.append(f"    {_name(slot)}=0;")
        lines.append(f"{_name(function.name + '___exit')}:")
        if is_main:
            lines.append("    piton_gc_collect();")
            lines.append("    piton_finalize_objects();")
        lines.append("    return 0;")
        lines.append("}")
        return lines

    def _emit_generator_function(self, function: MIRFunction) -> list[str]:
        """Emit a suspendible generator body: ``state`` dispatch over heap slots.

        Same logical ABI as the Win64 backend: ``PitonGenerator*`` in,
        yielded value out, ``state`` selects the resume point, ``finished``
        marks completion. All mutable slots are mirrored into ``piton_gen->slots``.
        """
        layout = self.generator_layouts.get(function.name)
        if layout is None:
            raise NativeBuildError(f"native generator '{function.name}' has no persisted-slot layout")
        # GEN_SLOTS_V1: el body usa los mismos nombres que el layout, asi que
        # el chequeo de nombres no debe tratarlos como undefined.
        self._cur_slots = set(layout) | set(function.params)
        # GEN_VARARG_V1: *args llega como tupla empaquetada por el call-site;
        # sin este seed, load('args') tipa 'int' e iter_new falla en build.
        if function.vararg:
            self._cur_slots.add(function.vararg)
        ordered = sorted(layout.items(), key=lambda item: item[1])
        lines = [f"static long {_name(function.name)}(PitonGenerator *piton_gen) {{"]
        if ordered:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot, _ in ordered) + ";")
        lines.append("    long __sent = 0;")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        if function.vararg:
            types[function.vararg] = "tuple"
        if function.kwarg:
            types[function.kwarg] = "dict"
        self._fn_consts = {}
        self._boolh_types: dict[str, str] = {}
        self._tuple_elems = {k: v for k, v in self._tuple_elems.items() if not k.startswith('%')}
        self._dict_key_types = {k: v for k, v in self._dict_key_types.items() if not k.startswith('%')}
        self._coll_elems = {k: v for k, v in self._coll_elems.items() if not k.startswith('%')}
        self._dict_val_types = {k: v for k, v in self._dict_val_types.items() if not k.startswith('%')}
        self._chr_results = {k for k in getattr(self, '_chr_results', set()) if not k.startswith('%')}
        self._dict_elems = {k: v for k, v in self._dict_elems.items() if not k.startswith('%')}
        bigint_slots: list[str] = []
        for slot, index in ordered:
            lines.append(f"    {_name(slot)}=piton_gen->slots[{index}];")
        yield_count = sum(
            1 for block in function.blocks for instruction in block.instructions if instruction.op in {"gen_yield", "agen_emit"}
        )
        resumes = [_name(f"{function.name}_genresume_{i}") for i in range(1, yield_count + 1)]
        if yield_count:
            for resume_id, resume in enumerate(resumes, start=1):
                lines.append(f"    if(piton_gen->state=={resume_id}) goto {resume};")
            lines.append(f"    if(piton_gen->state!=0) goto {_name(function.name + '___exit')};")
        self._gen_layout = layout
        self._gen_resumes = resumes
        self._gen_function_name = function.name
        self._gen_counter = 0
        try:
            for _block_index, block in enumerate(function.blocks):
                self._cur_block = _block_index
                lines.append(f"{_name(function.name + '_' + block.label)}:")
                for instruction in block.instructions:
                    lines.extend(self._emit_instruction(instruction, function, aliases, types, bigint_slots))
                if not block.instructions or block.instructions[-1].op not in {"jump", "branch", "return"}:
                    lines.append(f"    goto {_name(function.name + '___exit')};")
        finally:
            self._gen_layout = {}
            self._gen_resumes = []
            self._gen_function_name = None
            self._gen_counter = 0
        for slot in bigint_slots:
            lines.append(f"    piton_bigint_free((void*){_name(slot)});")
            lines.append(f"    {_name(slot)}=0;")
        lines.append(f"{_name(function.name + '___exit')}:")
        lines.append("    piton_gen->finished=1;")
        lines.append("    return 0;")
        lines.append("}")
        return lines

    def _ordering_pair_ok(self, left_type: str, right_type: str) -> bool:
        """ORDER_MIXED_TYPES_V1: can these two static types be ordered?

        CPython raises TypeError for `<`, `>`, `<=`, `>=` between operands it
        cannot order (`1 < '1'`, `None < None`). The emitter used to fall
        through to a raw C comparison of the two representations, so `None <
        None` answered False and `1 < '1'` answered True. `==`/`!=` are NOT
        restricted: Python allows equality across types.
        """
        numeric = {"int", "bool", "float", "bigint"}
        if left_type in numeric and right_type in numeric:
            return True
        if left_type in {"bytes", "bytearray"} and right_type in {"bytes", "bytearray"}:
            return True
        if left_type in {"list", "tuple", "dict", "set"} and left_type == right_type:
            return True
        return left_type == right_type and left_type not in {"none", ""}

    def _truth_expr(self, value: Any, vtype: str, strict: bool) -> str:
        """C expression for CPython truthiness of `value` with static `vtype`.

        TRUTHY_BRANCH_V1: single source of truth for both `branch` (the `si` /
        `mientras` condition) and `truth_test` (`y`/`o` operands), so the two
        cannot drift apart again. `strict=True` (truth_test) fails closed on
        types the table cannot model; `strict=False` (branch) keeps the
        historical raw test for them, because a `si <objeto>:` condition used
        to compile and must not become a new build error here.
        """
        raw = self._value(value)
        # CLASS_TYPE_V1: builtin types stored as values (e.g. _types.py:
        # `Pos = int`) have type "class:int"; normalize to plain kind for
        # truthiness checks.
        if vtype.startswith("class:"):
            vtype = vtype.split(":", 1)[1]
        if vtype in {"int", "bool"}:
            # NONE_SINGLETON_V2: un valor de tipo desconocido/int puede ser
            # None; el singleton no es 0, sin esta exclusion `si x:` sobre un
            # None seria verdadero (y un __exit__ que devuelve None suprimiria
            # la excepcion).
            return f"(({raw})!=0&&({raw})!=PITON_NONE)"
        if vtype == "float":
            return f"(piton_bits_double({raw})!=0.0)"
        if vtype == "complex":
            # COMPLEX_V1: 0j es falsy; cualquier parte distinta de cero, truthy.
            return f"(piton_cx_truthy_bits({raw}))"
        if vtype == "none":
            return "0"
        if vtype == "str":
            return f"(piton_strlen((const char*){raw})>0)"
        if vtype == "bytes":
            return f"(((PitonBytes*){raw})->length>0)"
        if vtype == "bytearray":
            return f"(((PitonByteArray*){raw})->length>0)"
        if vtype == "slot":
            # BOOL_SLOT_V1: mixed-type operands of `y`/`o` are tagged slots;
            # their truthiness is the per-kind CPython truth table.
            return f"(piton_slot_truthy(*(PitonSlot*){raw}))"
        if vtype.startswith("object:"):
            # OBJ_TRUTH_V1: CPython truthiness for an instance: __bool__()
            # first, then __len__() != 0, else the object is truthy.
            cls_name = vtype.split(":", 1)[1]
            for candidate in (self.class_mro.get(cls_name, []) or [cls_name]):
                if candidate in self.classes and "__bool__" in self.classes[candidate]:
                    return f"({_name(candidate + '__' + '__bool__')}({raw}))"
            for candidate in (self.class_mro.get(cls_name, []) or [cls_name]):
                if candidate in self.classes and "__len__" in self.classes[candidate]:
                    return f"({_name(candidate + '__' + '__len__')}({raw})!=0)"
            return f"((long)({raw})!=0)"
        if vtype in {"list", "tuple"}:
            return f"(((PitonSeq*){raw})->length>0)"
        if vtype == "dict":
            return f"(((PitonDict*){raw})->length>0)"
        if vtype == "set":
            return f"(((PitonSet*){raw})->length>0)"
        if vtype == "range":
            return f"(piton_range_len((PitonRange*){raw})>0)"
        if vtype == "slot":
            # SLOT_TRUTH_V1: un `o`/`y` de brazos de tipo distinto es un slot
            # ETIQUETADO (ver BOOL_SLOT_V1). Su verdad se decide por la kind
            # del slot, como CPython decide por el valor ganador.
            return (
                "({PitonSlot _ts=*(PitonSlot*)"
                f"{raw};_ts.kind==PK_NONE?0:(_ts.kind==PK_STR?(_ts.bits&&((const char*)_ts.bits)[0]!=0):"
                "(_ts.kind==PK_LIST||_ts.kind==PK_TUPLE?(((PitonSeq*)_ts.bits)->length>0):"
                "(_ts.kind==PK_DICT?(((PitonDict*)_ts.bits)->length>0):(_ts.bits!=0))));})"
            )
        if strict:
            raise NativeBuildError(f"Linux truth test does not support {vtype}")
        return f"({raw})"

    def _reject_complex_args(self, values: Any, types: dict, ctx: str) -> None:
        """COMPLEX_V1: el ABI de llamada tipa los parámetros del callee como int;
        un complejo cruzando esa frontera se imprimiría como puntero (divergencia
        silenciosa). Fail-closed en build hasta existir tipado de parámetros.
        M6: los defaults completados pueden ser list/tuple de Python (no son
        operands hashables) — solo los operands str se consultan en types."""
        for value in values:
            if isinstance(value, str) and types.get(value) == "complex":
                raise NativeBuildError(
                    f"Linux passing a complex value into {ctx} is not supported yet (fail closed)"
                )

    def _complex_operand(self, operand: Any, otype: str) -> str | None:
        """COMPLEX_V1: expresión C que convierte un operando de tipo estático
        conocido a PitonComplexVal (struct por valor). Devuelve None cuando el
        tipo no puede convertirse exactamente (el llamador falla cerrado)."""
        raw = self._value(operand)
        if otype == "complex":
            return f"piton_cx_of({raw})"
        if otype == "float":
            return f"piton_cx_from_float({raw})"
        if otype in {"int", "bool"}:
            return f"piton_cx_from_int({raw})"
        return None

    def _value(self, value: Any) -> str:
        if isinstance(value, tuple) and len(value) == 3 and value[0] == "__attr__":
            # NON_CONST_DEFAULT_V1 (atributo): el default es el valor nativo
            # del atributo del modulo (sys.version_info) — se materializa
            # como la tupla/valor del host.
            _mod, _attr = value[1], value[2]
            if _mod == "sys" and _attr in {"version_info", "version", "maxsize", "platform"}:
                _v = getattr(sys, _attr, None)
                if isinstance(_v, tuple):
                    _parts = [f"{{PitonSeq*_da=piton_seq_new(PK_TUPLE,{len(_v)});"]
                    for _i, _item in enumerate(_v):
                        if isinstance(_item, int):
                            _parts.append(f"_da->items[{_i}]=(PitonSlot){{{_item},PK_INT}};")
                        else:
                            _parts.append(
                                f"_da->items[{_i}]=(PitonSlot){{(long){_str_c_literal(str(_item))},PK_STR}};"
                            )
                    _parts.append("_da;}")
                    return f"(long)({''.join(_parts)})"
                if isinstance(_v, int):
                    return str(_v)
                if isinstance(_v, str):
                    return f"(long){json.dumps(_v)}"
            raise NativeBuildError(
                f"Linux default '{_mod}.{_attr}' is not statically known at the call site"
            )
        if isinstance(value, tuple) and len(value) == 2 and value[0] == "__name__":
            # NON_CONST_DEFAULT_V1: default que referencia un NOMBRE — se
            # materializa en el call-site con el valor actual del nombre.
            # * si el nombre es una variable en alcance (closure): su valor.
            # * si es una excepcion builtin: su NOMBRE (el backend compara
            #   tipos por nombre, asi que isinstance(v, cat) funciona).
            # * si no: falla cerrado (el default no es estatico).
            _dname = value[1]
            if _dname in (getattr(self, "_cur_slots", None) or ()):
                return _name(_dname)
            if _dname in self.classes:
                # el default es una CLASE: se materializa el objeto de clase
                # (mismo camino que cargar el nombre de una clase).
                return f'(long)piton_object_new("{_dname}",0)'
            if _dname in _BUILTIN_EXCEPTIONS:
                return f"(long){json.dumps(_dname)}"
            raise NativeBuildError(
                f"Linux default '{_dname}' is not statically known at the call site"
            )
        if isinstance(value, str) and value in self.classes and not (
            value in self._module_stored or value in self._shared_globals
        ):
            # CLASS_VALUE_REF_V1: el nombre de una clase sin variable C
            # declarada (`clase P:` no genera store) no es un identificador
            # válido en C — `P__f(P)` no compilaba. Se fabrica el objeto de
            # clase en el punto de uso; si la clase SÍ tiene variable global,
            # se usa esa (camino histórico).
            return f'(long)piton_object_new("{value}",0)'
        if isinstance(value, str) and (value.startswith("%") or value in self.classes):
            return _name(value)
        if isinstance(value, str):
            return f"(long){_str_c_literal(value)}"
        if isinstance(value, bytes):
            return f"(long)piton_bytes_new({len(value)},(const unsigned char*){_bytes_c_literal(value)})"
        if isinstance(value, bytearray):
            return f"(long)piton_bytearray_new({len(value)},(const unsigned char*){_bytes_c_literal(bytes(value))})"
        if value is None:
            # NONE_SINGLETON_V2: None ya no es el entero 0. Los placeholders
            # internos (dynamic_call, handlers) NO pasan por aqui: el emisor
            # los traduce a "0" explicito en su propio sitio.
            return "PITON_NONE"
        if value is Ellipsis:
            # ELLIPSIS_SINGLETON_V1: `...` tambien tiene direccion propia.
            return "PITON_ELLIPSIS"
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"piton_double_bits({_float_c_literal(value)})"
        if isinstance(value, complex):
            return f"(long)piton_complex_new({_float_c_literal(value.real)},{_float_c_literal(value.imag)})"
        if isinstance(value, (list, tuple)):
            # M6 SEQ_VALUE_V1: default/const collection -> IIFE con
            # piton_seq_new (el caller completa defaults; el callee no tiene
            # anotacion de tipo, asi que un uso interno tipo-estatico puede
            # seguir fallando — documentado). Items anidados fallan cerrados.
            seq_kind = "PK_TUPLE" if isinstance(value, tuple) else "PK_LIST"
            parts = [f"{{PitonSeq*_d=piton_seq_new({seq_kind},{len(value)});"]
            for i, item in enumerate(value):
                if item is None:
                    bits, slot_kind = "0", "PK_NONE"
                elif isinstance(item, bool):
                    bits, slot_kind = str(int(item)), "PK_BOOL"
                elif isinstance(item, int):
                    bits, slot_kind = str(item), "PK_INT"
                elif isinstance(item, float):
                    bits, slot_kind = f"piton_double_bits({_float_c_literal(item)})", "PK_FLOAT"
                elif isinstance(item, str):
                    bits, slot_kind = f"(long){json.dumps(item)}", "PK_STR"
                else:
                    raise NativeBuildError(f"Linux scalar backend cannot encode nested collection item {item!r}")
                parts.append(f"_d->items[{i}]=(PitonSlot){{{bits},{slot_kind}}};")
            parts.append("_d;}")
            return f"(long)({''.join(parts)})"
        raise NativeBuildError(f"Linux scalar backend cannot encode {value!r}")

    @staticmethod
    def _kind(value_type: str | None) -> str:
        return {
            "none": "PK_NONE", "bool": "PK_BOOL", "int": "PK_INT",
            "float": "PK_FLOAT", "str": "PK_STR", "list": "PK_LIST",
            "tuple": "PK_TUPLE", "dict": "PK_DICT", "set": "PK_SET",
            "bigint": "PK_BIGINT", "range": "PK_RANGE",
            "bytes": "PK_BYTES", "bytearray": "PK_BYTEARRAY", "complex": "PK_COMPLEX",
            "set:frozen": "PK_FROZENSET", "slice": "PK_SLICE", "memoryview": "PK_MEMORYVIEW",
        }.get(value_type or "int", "PK_OBJECT")

    def _slot(self, value: Any, types: dict[str, str]) -> str:
        return f"piton_slot({self._value(value)},{self._kind(types.get(value, 'int'))})"

    def _needle_slot(self, value: Any, types: dict[str, str]) -> str:
        """NEEDLE_SLOT_V1: el needle de un `in` como expresion C de PitonSlot.
        Un valor etiquetado (tipo 'slot', ej. la key de un dict iterado con
        kind desconocida) se desreferencia: *(PitonSlot*)v."""
        if types.get(value) == "slot":
            return f"*(PitonSlot*){self._value(value)}"
        return self._slot(value, types)

    @staticmethod
    def _parse_percent_template(template: str) -> tuple[str, list[tuple]]:
        """PCT_FORMAT_V1: translate a %-format template into a {}-template
        for the piton_str_format engine, returning (template, fields) with
        one tuple per conversion: (name, conv, flags, width, prec).

        name is None for positional fields, the mapping key for %(name)
        fields. conv is one of s d r c x X o (i/u normalized to d).
        flags is a bitmask (1 = '-', 2 = '0'), width/prec are ints or -1.
        Only static widths/precisions are accepted (* needs runtime values).
        '+', ' ', '#' flags, length modifiers and float conversions fail
        closed at BUILD time — the template is always a literal here, so
        every rejection is static. Literal braces pass through escaped
        (CPython %-formatting leaves braces alone; the {} engine would
        otherwise eat them).
        """
        out: list[str] = []
        fields: list[tuple] = []
        i, n = 0, len(template)
        while i < n:
            ch = template[i]
            if ch == "%":
                i += 1
                if i >= n:
                    raise NativeBuildError("str % formatting: trailing %")
                name = None
                if template[i] == "(":
                    j = template.find(")", i + 1)
                    if j < 0:
                        raise NativeBuildError("str % mapping: missing closing )")
                    name = template[i + 1:j]
                    i = j + 1
                    if i >= n:
                        raise NativeBuildError("str % mapping: trailing %()")
                flags = 0
                while i < n and template[i] in "-0":
                    if template[i] == "-":
                        flags |= 1
                    else:
                        flags |= 2
                    i += 1
                if i < n and template[i] == "#":
                    flags |= 4  # PCT_ALT_FLAG_V1: forma alternativa (0x, 0o, etc.)
                    i += 1
                if i < n and template[i] in "+ ":
                    raise NativeBuildError("str % + and space flags are not supported")
                width = -1
                if i < n and template[i] == "*":
                    raise NativeBuildError("str % dynamic width (*) is not supported")
                j = i
                while j < n and template[j].isdigit():
                    j += 1
                if j > i:
                    width = int(template[i:j])
                    i = j
                prec = -1
                if i < n and template[i] == ".":
                    i += 1
                    if i < n and template[i] == "*":
                        raise NativeBuildError("str % dynamic precision (.*) is not supported")
                    j = i
                    while j < n and template[j].isdigit():
                        j += 1
                    prec = int(template[i:j]) if j > i else 0
                    i = j
                if i < n and template[i] in "hlL":
                    raise NativeBuildError("str % length modifiers are not supported")
                if i >= n:
                    raise NativeBuildError("str % formatting: trailing %")
                c2 = template[i]
                if c2 == "%":
                    if name is not None or flags or width >= 0 or prec >= 0:
                        raise NativeBuildError("str %% with flags/width/precision is not supported")
                    out.append("%")
                    i += 1
                    continue
                if c2 in "srdcfeFGgE":
                    fields.append((name, c2, flags, width, prec))
                    out.append("{}")
                    i += 1
                    continue
                if c2 in "iu":
                    fields.append((name, "d", flags, width, prec))
                    out.append("{}")
                    i += 1
                    continue
                if c2 in "xXo":
                    fields.append((name, c2, flags, width, prec))
                    out.append("{}")
                    i += 1
                    continue
                raise NativeBuildError(f"str % conversion %{c2} is not supported")
            elif ch == "{" or ch == "}":
                out.append(ch * 2)
                i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out), fields

    @staticmethod
    def _literal_str_operand(operand: Any, consts: dict[str, Any]) -> str | None:
        """FMT_SPEC_V1: the operand's string value when it is PROVABLY a
        literal (a temp recorded in the constant table, or a literal embedded
        in the MIR). A plain variable name returns None — a name is not a
        template — so runtime templates keep the historical pointer path."""
        if not isinstance(operand, str):
            return None
        if operand.startswith("%"):
            value = consts.get(operand)
            return value if isinstance(value, str) else None
        if operand.isidentifier():
            return None
        return operand

    @staticmethod
    def _parse_format_template(template: str) -> tuple[str, list[tuple[int, str]]]:
        """FMT_SPEC_V1: parse a str.format() template into a simplified
        template (all placeholders replaced with {}) and a list of
        (index, spec, token) tuples. Named placeholders fail closed. Escaped
        braces ({{ }}) are left intact for the format engine, which
        renders them as single literal braces."""
        out: list[str] = []
        fields: list[tuple[int, str, str, tuple | None]] = []
        names: dict[str, int] = {}
        named_fields: set[int] = set()
        i, n = 0, len(template)
        auto_idx = 0
        named_idx = 0
        saw_manual = saw_auto = False
        while i < n:
            ch = template[i]
            if ch == "{":
                if i + 1 < n and template[i + 1] == "{":
                    out.append("{{")
                    i += 2
                    continue
                j = template.find("}", i + 1)
                if j < 0:
                    raise NativeBuildError("str.format(): unmatched '{'")
                content = template[i + 1:j]
                i = j + 1
                if ":" in content:
                    idx_str, spec = content.split(":", 1)
                else:
                    idx_str, spec = content, ""
                explicit = idx_str != ""
                accessor: tuple | None = None
                if idx_str == "":
                    idx = auto_idx
                    auto_idx += 1
                    token = "{}"
                else:
                    m_index = re.match(r"^(\d+)\[(-?\d+)\]$", idx_str)
                    m_key = re.match(r"^(\d+)\[(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_]\w*))\]$", idx_str)
                    m_attr = re.match(r"^(\d+)\.([A-Za-z_]\w*)$", idx_str)
                    if m_index:
                        idx = int(m_index.group(1))
                        accessor = ("index", int(m_index.group(2)))
                    elif m_key:
                        # FMT_KEY_V1: {0[Jack]} / {0['Jack']} — CPython trata
                        # una key no-entera como string.
                        idx = int(m_key.group(1))
                        accessor = ("key", m_key.group(2) or m_key.group(3) or m_key.group(4))
                    elif m_attr:
                        idx = int(m_attr.group(1))
                        accessor = ("attr", m_attr.group(2))
                    else:
                        try:
                            idx = int(idx_str)
                        except ValueError:
                            # FMT_NAMED_V1: un campo con NOMBRE ({name})
                            # se numera aparte (CPython numera los campos
                            # por nombre en su propio orden) y el mapa
                            # nombre->indice lo usa el emisor para colocar
                            # los argumentos por keyword.
                            if not re.match(r"^[A-Za-z_]\w*$", idx_str):
                                raise NativeBuildError(
                                    f"str.format(): unsupported placeholder: {{{content}}}"
                                )
                            idx = named_idx
                            named_idx += 1
                            names[idx_str] = idx
                            named_fields.add(idx)
                    if idx < 0:
                        raise NativeBuildError(
                            f"str.format(): negative field index {idx} is not supported"
                        )
                    token = "{" + str(idx) + "}"
                # CPython refuses to mix automatic and manual numbering
                # ("cannot switch from automatic field numbering to manual
                # field specification"); the {} engine would silently accept
                # it, so reject it here.
                if explicit and saw_auto:
                    raise NativeBuildError(
                        "str.format(): cannot switch from automatic to manual field numbering"
                    )
                if not explicit and saw_manual:
                    raise NativeBuildError(
                        "str.format(): cannot switch from manual to automatic field numbering"
                    )
                saw_manual = saw_manual or explicit
                saw_auto = saw_auto or not explicit
                fields.append((idx, spec, token, accessor))
                out.append(token)
            elif ch == "}":
                if i + 1 < n and template[i + 1] == "}":
                    out.append("}}")
                    i += 2
                    continue
                raise NativeBuildError("str.format(): single '}' in format string")
            else:
                out.append(ch)
                i += 1
        return "".join(out), fields, names

    @staticmethod
    def _parse_format_map_template(template: str) -> tuple[str, list[str]]:
        """FORMAT_MAP_V1: template de format_map() con campos {nombre} -> un
        template con campos {} (para reusar piton_str_format) y la lista de
        nombres en orden. Escaped braces ({{ }}) intactos. Specs, conversiones
        (!r), accessors ({0.x}) y campos vacios no estan soportados: se rechazan
        aqui (FAIL_CLOSED honesto, CPython los acepta)."""
        out: list[str] = []
        names: list[str] = []
        i, n = 0, len(template)
        while i < n:
            ch = template[i]
            if ch == "{":
                if i + 1 < n and template[i + 1] == "{":
                    out.append("{{")
                    i += 2
                    continue
                j = template.find("}", i + 1)
                if j < 0:
                    raise NativeBuildError("str.format_map(): unmatched '{'")
                content = template[i + 1:j]
                i = j + 1
                if not re.fullmatch(r"[A-Za-z_]\w*", content):
                    raise NativeBuildError(
                        "Linux str.format_map() supports only simple {name} fields "
                        "(specs, conversions and accessors are not supported yet)"
                    )
                names.append(content)
                out.append("{}")
            elif ch == "}":
                if i + 1 < n and template[i + 1] == "}":
                    out.append("}}")
                    i += 2
                    continue
                raise NativeBuildError("str.format_map(): unmatched '}'")
            else:
                out.append(ch)
                i += 1
        return "".join(out), names

    @staticmethod
    def _format_spec_type(spec: str) -> str:
        """FMT_SPEC_V1: extract the type char from a format spec (last char
        if it's a known type). Returns '' for no type."""
        if not spec:
            return ""
        t = spec[-1]
        return t if t in "bcdeEfFgGnosxX%" else ""

    @staticmethod
    def _validate_presentation_spec(pres: str) -> None:
        """FMT_SPEC_V1: validate [[fill]align][sign][#][0][width][,][.prec]
        statically. Unknown specs fail closed at BUILD time: the C helper
        returns NULL for them, and a NULL template argument crashed the
        format engine (observed rc=-11 on '{:.2g}').

        The `%` presentation is rejected (only used by the '%' type, which
        has its own conversion) so `{:%>5}` cannot be mistaken for valid.
        """
        if not pres:
            return
        i, n = 0, len(pres)
        if n >= 2 and pres[1] in "<>^=":
            i = 2
        elif pres[0] in "<>^=":
            i = 1
        if i < n and pres[i] in "+- ":
            i += 1
        if i < n and pres[i] == "#":
            i += 1
        if i < n and pres[i] == "0":
            i += 1
        while i < n and pres[i].isdigit():
            i += 1
        if i < n and pres[i] == ",":
            i += 1
        if i < n and pres[i] == ".":
            i += 1
            if i >= n or not pres[i].isdigit():
                raise NativeBuildError(
                    f"str.format(): precision needs at least one digit in {pres!r}"
                )
            while i < n and pres[i].isdigit():
                i += 1
        # FMT_SPEC_S_V1: `{:s}` es la presentacion de str; para un str el
        # especificador no cambia nada (como en CPython con `format(x, "s")`).
        if i < n and pres[i] == "s" and i == n - 1:
            i += 1
        if i != n:
            raise NativeBuildError(f"str.format(): unsupported format spec {pres!r}")

    @staticmethod
    def _format_spec_presentation(spec: str) -> str:
        """FMT_SPEC_V1: strip the type char from a format spec, leaving the
        presentation part (fill/align/sign/zero/width/comma/precision)."""
        if not spec:
            return ""
        t = spec[-1]
        if t in "dxXobfeEgGFgn%s":
            return spec[:-1]
        return spec

    @staticmethod
    def _is_none_side(value: Any, types: dict[str, str]) -> bool:
        """NONE_SINGLETON_V2: True si un lado de una identidad es None."""
        if isinstance(value, str) and value.startswith("%"):
            return types.get(value) == "none"
        return value is None

    @staticmethod
    def _percent_literal_type(value) -> str:
        """P14: exact static type of a frozen %-format operand literal."""
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if value is None:
            return "none"
        raise NativeBuildError("Linux str % formatting: unsupported literal argument")

    def _emit_gen_args(self, out: list[str], result: Any, function_name: str,
                         layout: dict[str, int], params: list[str], vararg: Any,
                         gen_args: list[Any], types: dict[str, str]) -> None:
        """GEN_VARARG_V1: fill generator slots; extras pack into an *args tuple.

        Named params fill their slots verbatim; when the callee declares
        *args, the remaining call arguments are packed into a fresh
        PK_TUPLE stored in the vararg slot (no intermediate temp needed:
        the tuple is built directly into the slot reference).
        """
        # El MIR incluye el nombre *args en params ADEMAS de vararg: el
        # fill nominal debe excluirlo o lo pisaria antes de empaquetar.
        named = [param for param in params if param != vararg]
        out.append(f"    {_name(result)}=piton_gen_new((long)&{_name(function_name)},{len(layout)});")
        for arg, param in zip(list(gen_args)[:len(named)], named):
            out.append(f"    ((PitonGenerator*){_name(result)})->slots[{layout[param]}]={self._value(arg)};")
        if vararg is not None:
            rest = list(gen_args)[len(named):]
            slotref = f"((PitonGenerator*){_name(result)})->slots[{layout[vararg]}]"
            out.append(f"    {slotref}=(long)piton_seq_new(PK_TUPLE,{len(rest)});")
            for index, value in enumerate(rest):
                out.append(f"    piton_seq_put((PitonSeq*){slotref},{index},{self._slot(value, types)});")

    def _emit_user_function_call(self, out: list[str], result: Any, function_name: str,
                                values: list[Any], call_handler: Any,
                                function: MIRFunction, types: dict[str, str]) -> list[str]:
        """Call a user-defined function directly. Extracted so it can run
        BEFORE the builtin chain: a user definition SHADOWS a builtin of the
        same name (`funcion suma(a, b)` is not `sum`)."""
        _gen_layout = self.generator_layouts.get(function_name)
        if _gen_layout is not None:
            _params = self.function_params.get(function_name, [])
            _vararg = self.function_varargs.get(function_name)
            _named1 = [p for p in _params if p != _vararg]
            if self.function_kwargs.get(function_name):
                raise NativeBuildError(
                    f"native generator '{function_name}' with **kwargs is not supported yet"
                )
            if _vararg is None:
                if len(values) != len(_named1):
                    raise NativeBuildError(
                        f"native generator '{function_name}' called with wrong number of arguments"
                    )
                if len(values) > 4:
                    raise NativeBuildError("native generator calls with more than four arguments are not supported yet")
            else:
                if len(values) < len(_named1):
                    raise NativeBuildError(
                        f"native generator '{function_name}' called with wrong number of arguments"
                    )
                if _vararg not in _gen_layout:
                    raise NativeBuildError(
                        f"native generator '{function_name}' *args slot is missing from the persisted layout"
                    )
            self._emit_gen_args(out, result, function_name, _gen_layout, _params, _vararg, values, types)
            types[result] = "generator"
            return out
        _params = self.function_params.get(function_name, [])
        _defaults = self.function_defaults.get(function_name, [])
        # function_defaults is a full-length list with None holes for params
        # without a default; required = count of those holes (CPython forbids
        # a required parameter after a defaulted one, so holes are leading).
        # VARARG_ARITY_V1: `*args`/`**kwargs` no son parametros
        # OBLIGATORIOS — el maximo con vararg es abierto (CPython acepta
        # mas argumentos de los declarados) y el minimo no cuenta sus
        # slots.
        _vararg = self.function_varargs.get(function_name)
        _kwarg = self.function_kwargs.get(function_name)
        _n_fixed = len(_params) - (1 if _vararg is not None else 0) - (1 if _kwarg is not None else 0)
        # DEFAULT_NONE_V1: `function_defaults` solo contiene los parametros que
        # TIENEN default (y su valor, que puede ser None). El minimo de
        # argumentos es entonces los fijos MENOS los que tienen default. Antes se
        # contaban "los None", que es al reves: `def f(a, b=None)` pedia 2
        # argumentos y rechazaba `f(1)` con un TypeError que CPython no lanza.
        _n_defaults = len(_defaults) if isinstance(_defaults, dict) else len(list(_defaults))
        _min_args = max(0, _n_fixed - _n_defaults)
        _max_args = len(_params) if _vararg is None and _kwarg is None else None
        _too_few = len(values) < _min_args
        _too_many = _max_args is not None and len(values) > _max_args
        if _too_few or _too_many:
            _max_text = str(_max_args) if _max_args is not None else "ilimitados"
            self._runtime_type_error(out, f"{function_name}() takes " + (_max_text if _min_args == _max_args else (str(_min_args) + " to " + _max_text)) + " positional arguments but " + str(len(values)) + " were given")
            types[result] = "int"
            return out
        values = self._complete_call_args(function_name, list(values))
        # IDENTITY_FN_V1: si el callee devuelve su param sin cambios y el
        # argumento es un literal str, el resultado de la llamada ES ese
        # literal — un `%` con ese resultado tiene plantilla conocida
        # (getopt: `_('option --%s ...') % opt`).
        _id_index = self._identity_params.get(function_name)
        if _id_index is not None and _id_index < len(values):
            _id_val = values[_id_index]
            # el default de un param puede ser un literal de coleccion
            # (no un temp) — no es indexable en _fn_consts.
            _id_lit = self._fn_consts.get(_id_val) if isinstance(_id_val, str) else None
            if isinstance(_id_lit, str):
                self._identity_templates[result] = _id_lit
        # M6 SEQ_PARAM_DEFAULT_V1: el callee anota el tipo del default
        # collection; un argumento escalar explicito con tipo conocido en
        # ese param es fail-closed (CPython lo admitiria, pero el subset no
        # puede re-anotar el callee por llamada).
        for _i, (_val, _pd) in enumerate(zip(values, _defaults or [])):
            _pd_is_marker = (
                isinstance(_pd, tuple) and len(_pd) >= 2
                and _pd[0] in {"__name__", "__attr__"}
            )
            if isinstance(_pd, (list, tuple, str)) and not _pd_is_marker and isinstance(_val, str):
                _pt = types.get(_val)
                # CLASS_ARG_V1: class/exception arguments (class:Foo) are
                # passed by reference and never conflict with a
                # collection/string default — allow them through.
                if _pt is not None and _pt.startswith("class:"):
                    _pt = None
                _allowed = (
                    {"list"} if isinstance(_pd, list)
                    else {"tuple"} if isinstance(_pd, tuple)
                    else {"str"}
                )
                if _pt is not None and _pt not in _allowed:
                    raise NativeBuildError(
                        f"Linux {function_name}() collection/string-default param rejects a {_pt} argument (fail closed)"
                    )
        # USAGE_PARAM_TYPE_V1: el callee tipa params por uso (str-methods);
        # un argumento de tipo estatico conocido y contradictorio (float,
        # list, dict...) es fail-closed — el callee emitiria un str-method
        # sobre un puntero no-str. "int" no se revisa: el default estatico
        # mezcla int-real con desconocido (misma garantia best-effort que
        # M6 en defaults). USAGE_LIST_SEED_V1: mismo contrato para params
        # tipados list por uso mutador (bisect: a.insert) — args str/
        # tuple/dict/set/float se rechazan; "int" (unknown) pasa.
        _usage_types = self._usage_param_types.get(function_name) or {}
        _weak_types = self._usage_param_weak.get(function_name) or {}
        for _ival, _iparam in zip(values, _params):
            _iut = _usage_types.get(_iparam)
            # CALL_ARG_TYPE_V1: registrar el tipo conocido del argumento —
            # el callee lo usa para tipar un param sin seed local (getopt:
            # do_longs(opt) <- args[0][2:] es str).
            _ipt_known = types.get(_ival) if isinstance(_ival, str) else None
            if _ipt_known in {"str", "list", "tuple", "dict", "set"}:
                self._call_param_types.setdefault(function_name, {})[_iparam] = _ipt_known
            if _weak_types.get(_iparam) and isinstance(_ival, str):
                # RECONCILE_SEED_V1: el seed del param fue AMBIGUO (indice+
                # slice son legales en str y en list) y el call-site conoce
                # el tipo real del argumento -> reconcilia el seed en vez de
                # rechazar. getopt: do_shorts(optstring) recibe `args[0][1:]`
                # (str) -> el param es str, no list.
                _ipt = types.get(_ival)
                if _ipt in {"str", "list", "tuple"}:
                    _usage_types[_iparam] = _ipt
                    _weak_types[_iparam] = False
                    if _ipt == "str":
                        self._usage_param_elems.get(function_name, {}).pop(_iparam, None)
                    continue
            if _iut == "str" and isinstance(_ival, str):
                _ipt = types.get(_ival)
                if _ipt is not None and _ipt not in {"str", "int", "bool", "none", "builtin"} and not _ipt.startswith("object:"):
                    # USED_AS_STR_V1: un parametro usado como str acepta
                    # objetos/clases (tipo object:...) — se usan como clave de
                    # dict (copyreg.dispatch_table[ob_type]) o en otros
                    # contextos donde CPython acepta cualquier objeto hashable.
                    # El runtime maneja la operación; si el uso real es como
                    # str en una operacion de string, fallara en runtime con
                    # TypeError, nunca silenciosamente.
                    raise NativeBuildError(
                        f"Linux {function_name}() param '{_iparam}' is used as str; a {_ipt} argument is rejected (fail closed)"
                    )
            elif _iut == "list" and isinstance(_ival, str):
                _ipt = types.get(_ival)
                if _ipt is not None and _ipt not in {"list", "int", "bool", "none"}:
                    raise NativeBuildError(
                        f"Linux {function_name}() param '{_iparam}' is used as list; a {_ipt} argument is rejected (fail closed)"
                    )
        self._reject_complex_args(values, types, f"function '{function_name}'")
        # VARARG_PACK_V1: una llamada directa a una funcion con `*args`
        # empaqueta los sobrantes en una tupla (el ABI del callee espera la
        # tupla en su slot vararg).
        _pack_vararg = self.function_varargs.get(function_name)
        if _pack_vararg is not None:
            _n_named = len([p for p in _params if p != _pack_vararg])
            # el camino variadico del MIR ya entrega la tupla del vararg
            # (values == params); aqui se empaqueta solo cuando la llamada
            # vino por el camino directo (values < params) y falta la tupla.
            if len(values) < len(_params) and len(values) >= _n_named:
                # la tupla se construye como TEMPORAL (una expresion inline
                # entrecomillada por _value seria un literal de string).
                _extra = list(values)[_n_named:]
                # el nombre lleva el prefijo '%' (convencion de los temps):
                # _value() lo traduce al identificador C sin entrecomillarlo.
                _vname = f"%vapack{self._vararg_pack_counter % 4}"
                self._vararg_pack_counter += 1
                out.append(f"    {{PitonSeq*_va=piton_seq_new(PK_TUPLE,{len(_extra)});")
                for _i, _ev in enumerate(_extra):
                    out.append(
                        f"    piton_seq_put(_va,{_i},piton_slot({self._value(_ev)},PK_INT));"
                    )
                out.append(f"    {_name(_vname)}=(long)_va;}}")
                values = list(values)[:_n_named] + [_vname]
        encoded_values = ",".join(self._value(value) for value in values)
        if self.function_frame_abi.get(function_name, False):
            # FRAME_CALL_ABI_V1: una funcion con frame ABI (mas de 4 params)
            # recibe su frame como puntero — la llamada directa pasaria los
            # argumentos sueltos y gcc rechazaria "too many arguments" (la
            # recursion a una funcion de frame ABI).
            out.append(f"    {{long _fc_args[]={{ {encoded_values} }};")
            out.append(
                f"    {_name(result)}=piton_frame_call((long)&{_name(function_name)},"
                f"{len(values)},_fc_args);}}"
            )
        else:
            out.append(f"    {_name(result)}={_name(function_name)}({encoded_values});")
        self._emit_exc_check(out, function, call_handler)
        types[result] = self.function_return_types.get(function_name, "int")
        return out

    def _infer_receiver_type(
        self,
        obj: Any,
        method: str,
        types: dict[str, str],
        function: Any,
    ) -> str:
        """RECEIVER_TYPE_V1: infiere el tipo del receiver de method_call cuando
        el mapa de tipos lo deja como "int" (el default de load sobre una
        variable no anotada).

        El pre-pass de params (USAGE_PARAM_TYPE_V1) solo tipa params, no
        temporarios locales tras store (getopt `args[0].startswith`,
        token `globals().items()`). Aqui se resuelve en tiempo de uso a partir
        de las evidencias de uso del metodo + el flujo def-use local:

        * si el receiver es un metodo EXCLUSIVO de dict (items/keys/...) -> dict
          si ademas el flujo def-use confirma (store de un build_collection dict
          o de un call de una funcion builtin que devuelve dict).
        * str-methods y non-str son contrarios, como en el pre-pass.

        El tipo devuelto se CACHEA en `types[obj]` para que el dispatch
        posterior (y el str_method_collection_method) vea el receiver tipado.
        Falla con "int" (sin inferir) si las evidencias son ambiguas, para no
        romper dispatch legítimos sobre int.
        """
        current = types.get(obj, "")
        if current and current != "int":
            return current
        if method in _USAGE_STR_METHODS and method not in _USAGE_NONSTR_METHODS:
            # startswith/replace/etc. son solo str — sin contradiccion.
            types[obj] = "str"
            return "str"
        if method in _USAGE_DICT_METHODS:
            # items/keys/etc. solo dict — confirmar el flujo def-use.
            # token: `tok_name = {.. for name, value in globals().items()}` —
            # el receiver %87 es el resultado de `call %86` donde %86 es un
            # `load globals` (callee resuelto via aliases/def-use), NO un call
            # directo con callee-literal `globals`. Se resuelve el callee.
            def _callee_name(callee: Any) -> str:
                if isinstance(callee, str):
                    if callee in aliases:
                        return aliases[callee]
                    return self._fn_consts.get(callee, callee) if not callee.startswith("%") else callee
                return ""
            for block in function.blocks:
                for instruction in block.instructions:
                    if (
                        instruction.op == "store"
                        and len(instruction.args) >= 2
                        and instruction.args[1] == obj
                    ):
                        src = instruction.args[0]
                        for b2 in function.blocks:
                            for i2 in b2.instructions:
                                if (
                                    i2.op == "build_collection"
                                    and i2.result == src
                                    and i2.args
                                    and i2.args[0] == "dict"
                                ):
                                    types[obj] = "dict"
                                    return "dict"
                                if i2.op == "call" and i2.result == src and isinstance(i2.args, tuple) and i2.args:
                                    callee = _callee_name(i2.args[0])
                                    if callee in {"globals", "locals", "dict"}:
                                        types[obj] = "dict"
                                        return "dict"
                                        # el callee puede ser otro temporario con
                                        # un load de globals detras (token %86).
                                    if callee.startswith("%"):
                                        for b3 in function.blocks:
                                            for i3 in b3.instructions:
                                                if i3.op == "load" and i3.result == callee and i3.args:
                                                    if i3.args[0] in {"globals", "locals"}:
                                                        types[obj] = "dict"
                                                        return "dict"
            # sin flujo confirmatorio — como fallback, si TODOS los usos del
            # receiver como method_call son metodos exclusivos de dict (token:
            # solo .items()), inferir dict. Evidencia-only: un uso contradictorio
            # (metodo no-dict) lo deja como int.
            for block in function.blocks:
                for instruction in block.instructions:
                    if (
                        instruction.op == "method_call"
                        and len(instruction.args) >= 3
                        and instruction.args[2] == obj
                    ):
                        _um = instruction.args[1]
                        if _um not in _USAGE_DICT_METHODS and _um not in ("__init__",):
                            return current  # evidencia contradictoria
            # al menos un uso dict y ninguno contradictorio -> dict.
            for block in function.blocks:
                for instruction in block.instructions:
                    if (
                        instruction.op == "method_call"
                        and len(instruction.args) >= 3
                        and instruction.args[2] == obj
                        and instruction.args[1] in _USAGE_DICT_METHODS
                    ):
                        types[obj] = "dict"
                        return "dict"
        return current

    def _emit_class_bases_attribute(self, out: list[str], result: Any, cls_name: str) -> None:
        """CLASS_BASES_V1: la clase lleva su `__bases__` (las bases
        DIRECTAS) y su `__mro__` (la linearizacion) como atributos — asi
        `cls.__bases__` y `cls.__mro__` funcionan tambien cuando la clase
        llega como parametro (abc: update_abstractmethods(cls) recorre
        cls.__bases__; copyreg: `for base in cls.__mro__`).

        Los objetos de clase del MRO se crean con su nombre; el MRO incluye
        las bases builtin (object/classmethod), que el runtime representa
        tambien como objetos de clase.
        """
        _cls_bases = list(self.class_bases.get(cls_name, ()))
        if _cls_bases:
            out.append(f"    {{PitonSeq*_bs=piton_seq_new(PK_TUPLE,{len(_cls_bases)});")
            for _bi, _base in enumerate(_cls_bases):
                out.append(
                    f'    piton_seq_put(_bs,{_bi},piton_slot((long)piton_object_new("{_base}",0),PK_OBJECT));'
                )
            out.append(
                f'    piton_object_set((PitonObject*){_name(result)},"__bases__",piton_slot((long)_bs,PK_TUPLE));}}'
            )
        _mro = list(self.class_mro.get(cls_name, ()))
        if _mro:
            out.append(f"    {{PitonSeq*_mr=piton_seq_new(PK_TUPLE,{len(_mro)});")
            for _mi, _mcls in enumerate(_mro):
                out.append(
                    f'    piton_seq_put(_mr,{_mi},piton_slot((long)piton_object_new("{_mcls}",0),PK_OBJECT));'
                )
            out.append(
                f'    piton_object_set((PitonObject*){_name(result)},"__mro__",piton_slot((long)_mr,PK_TUPLE));}}'
            )

    def _emit_collection_method(self, out: list[str], result: Any, method: str, obj: Any,
                                call_args: list[Any], coll_type: str, types: dict[str, str],
                                aliases: dict[str, str] | None = None) -> None:
        """COLL_METHODS_V1: builtin collection methods bound by static dispatch.

        Same contract as STR_METHODS_V1: exact arity checked at build time,
        mutators return None (types "none"), element-returning reads follow
        the get_item convention (types "int" — the untagged model cannot know
        the element type). Out-of-subset runtime inputs (pop from empty,
        heterogeneous sort) exit with the CPython exception name, like the
        other runtime helpers.
        """
        operand = self._value(obj)
        # DICT_VAL_TYPE_V1: the static-type maps are keyed by the MIR operand,
        # not by its already-rendered C expression (`operand`), so the lookup
        # must use `obj`.
        operand_key = obj

        def require_count(counts: tuple[int, ...], what: str) -> None:
            if len(call_args) not in counts:
                raise NativeBuildError(f"Linux {coll_type}.{method}() requires {what}")

        if coll_type == "list":
            if method == "append":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_seq_append((PitonSeq*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            elif method == "pop":
                require_count((0, 1), "zero or one int argument")
                if call_args and types.get(call_args[0]) not in {"int", "bool"}:
                    raise NativeBuildError("Linux list.pop() requires an int index or nothing")
                index = self._value(call_args[0]) if call_args else "-1"
                out.append(f"    {_name(result)}=piton_seq_pop((PitonSeq*){operand},{index}).bits;")
                types[result] = "int"
            elif method == "reverse":
                require_count((0,), "no arguments")
                out.append(f"    piton_seq_reverse((PitonSeq*){operand});")
                types[result] = "none"
            elif method == "insert":
                require_count((2,), "exactly two arguments (index, value)")
                if types.get(call_args[0]) not in {"int", "bool"}:
                    raise NativeBuildError("Linux list.insert() requires an int index")
                out.append(f"    piton_seq_insert((PitonSeq*){operand},{self._value(call_args[0])},{self._slot(call_args[1], types)});")
                types[result] = "none"
            elif method == "count":
                require_count((1,), "exactly one argument")
                out.append(f"    {_name(result)}=piton_seq_count((PitonSeq*){operand},{self._slot(call_args[0], types)});")
                types[result] = "int"
            elif method == "sort":
                # SORT_REVERSE_V1: `lista.sort(reverse=True)` — el kwarg se
                # expande a un flag posicional en el MIR (heapq:560).
                if len(call_args) == 1:
                    _rev_expr = self._value(call_args[0])
                    out.append(
                        f"    if({_rev_expr}){{piton_seq_sort_rev((PitonSeq*){operand});}}"
                        f"else{{piton_seq_sort((PitonSeq*){operand});}}"
                    )
                else:
                    require_count((0,), "no arguments")
                    out.append(f"    piton_seq_sort((PitonSeq*){operand});")
                types[result] = "none"
            elif method == "remove":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_seq_remove((PitonSeq*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            elif method == "extend":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_seq_extend((PitonSeq*){operand},(PitonSeq*){self._value(call_args[0])});")
                types[result] = "none"
            elif method == "clear":
                require_count((0,), "no arguments")
                out.append(f"    piton_seq_clear((PitonSeq*){operand});")
                types[result] = "none"
            elif method == "index":
                # SEQ_INDEX_RANGE_V1: index(x[, start[, stop]]) como CPython.
                require_count((1, 2, 3), "one, two or three arguments")
                _lo = self._value(call_args[1]) if len(call_args) >= 2 else "0"
                _hi = (
                    self._value(call_args[2])
                    if len(call_args) >= 3
                    else f"((PitonSeq*){operand})->length"
                )
                out.append(
                    f"    {_name(result)}=piton_seq_index_range((PitonSeq*){operand},"
                    f"{self._slot(call_args[0], types)},{_lo},{_hi});"
                )
                types[result] = "int"
            elif method == "copy":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_seq_copy((PitonSeq*){operand});")
                types[result] = coll_type
                _ek = self._coll_elems.get(obj)
                if _ek is not None:
                    self._coll_elems[result] = _ek
            elif method == "close":
                # LIST_CLOSE_V1: no-op on a list (validate.py wraps list-like
                # iterators; close() releases nothing in the native subset).
                require_count((0,), "no arguments")
                out.append("    /* list.close() no-op */")
                types[result] = "none"
            else:
                raise NativeBuildError(f"Linux list.{method}() is not supported")
        elif coll_type == "tuple":
            if method == "count":
                require_count((1,), "exactly one argument")
                out.append(f"    {_name(result)}=piton_seq_count((PitonSeq*){operand},{self._slot(call_args[0], types)});")
                types[result] = "int"
            elif method == "index":
                # SEQ_INDEX_RANGE_V1: index(x[, start[, stop]]) como CPython.
                require_count((1, 2, 3), "one, two or three arguments")
                _lo = self._value(call_args[1]) if len(call_args) >= 2 else "0"
                _hi = (
                    self._value(call_args[2])
                    if len(call_args) >= 3
                    else f"((PitonSeq*){operand})->length"
                )
                out.append(
                    f"    {_name(result)}=piton_seq_index_range((PitonSeq*){operand},"
                    f"{self._slot(call_args[0], types)},{_lo},{_hi});"
                )
                types[result] = "int"
            else:
                raise NativeBuildError(f"Linux tuple.{method}() is not supported")
        elif coll_type == "dict":
            if method == "get":
                require_count((1, 2), "one or two arguments (key[, default])")
                if len(call_args) == 2:
                    default = self._slot(call_args[1], types)
                    # DICT_GET_SLOT_V1: la emision concreta se difiere hasta
                    # conocer el tipo del valor (abajo): con tipo conocido se
                    # emiten los bits; sin el, un slot etiquetado.
                else:
                    # DICT_VAL_TYPE_V1: a single-arg get yields the dict's
                    # value on hit or None on miss, so the result is a
                    # tagged slot whose printed form matches CPython.
                    out.append(f"    {{PitonSlot*_gp=piton_alloc(sizeof(PitonSlot));*_gp=piton_dict_get_1((PitonDict*){operand},{self._slot(call_args[0], types)}); {_name(result)}=(long)_gp;}}")
                # DICT_VAL_TYPE_V1: the hit yields the dict's value type; the
                # miss yields the default's. A default of a different type
                # than the values cannot be represented in the untagged
                # model, so it fails closed.
                # DICT_VAL_TYPE_V1: the operand may be a load temp of the
                # dict, so trace back to the original dict temp.
                _val_key = operand
                for _ in range(8):
                    _alt = "%" + _val_key[1:] if _val_key.startswith("_") else _val_key
                    if _val_key in self._dict_val_types:
                        break
                    if _alt in self._dict_val_types:
                        _val_key = _alt
                        break
                    _al = aliases or {}
                    _src = _al.get(_val_key) or _al.get(_alt)
                    if not _src or _src == _val_key:
                        break
                    _val_key = _src
                val_type = self._dict_val_types.get(_val_key)
                default_type = types.get(call_args[1]) if len(call_args) == 2 else None
                if val_type is None and default_type is not None \
                        and _val_key in self._dict_empty:
                    # DICT_VAL_TYPE_V1: provably empty dict -> the default
                    # alone determines the result type.
                    val_type = default_type
                if len(call_args) == 1:
                    # the miss case returns None (tagged slot), so the
                    # static type is always the generic slot marker.
                    types[result] = "slot"
                    return out
                if val_type is None:
                    if len(call_args) == 2:
                        # DICT_GET_SLOT_V1: tipo del valor desconocido — el
                        # resultado es un slot etiquetado (el hit da el
                        # valor, el miss el default; ninguno sabible
                        # estaticamente). codeop.py: d.get(k, '')
                        out.append(
                            f"    {{PitonSlot*_gp=piton_alloc(sizeof(PitonSlot));"
                            f"*_gp=piton_dict_get_d((PitonDict*){operand},"
                            f"{self._slot(call_args[0], types)},{default});"
                            f" {_name(result)}=(long)_gp;}}"
                        )
                        types[result] = "slot"
                        return out
                    raise NativeBuildError(
                        "native dict.get requires a statically known value type"
                    )
                if len(call_args) == 2:
                    out.append(
                        f"    {_name(result)}=piton_dict_get_d((PitonDict*){operand},"
                        f"{self._slot(call_args[0], types)},{default}).bits;"
                    )
                if default_type is not None and default_type != val_type:
                    raise NativeBuildError(
                        f"native dict.get default of type '{default_type}' cannot be "
                        f"unioned with values of type '{val_type}' in the untagged model"
                    )
                types[result] = val_type
                if val_type == "builtin" and aliases is not None:
                    aliases[result] = aliases.get(call_args[1], call_args[1])
            elif method == "pop":
                require_count((1, 2), "one or two arguments (key[, default])")
                has_def = 1 if len(call_args) == 2 else 0
                def_slot = self._slot(call_args[1], types) if has_def else "(PitonSlot){0,PK_NONE}"
                out.append(f"    {_name(result)}=piton_dict_pop((PitonDict*){operand},{self._slot(call_args[0], types)},{has_def},{def_slot}).bits;")
                types[result] = "int"
            elif method == "setdefault":
                require_count((1, 2), "one or two arguments (key[, default])")
                def_slot = self._slot(call_args[1], types) if len(call_args) == 2 else "(PitonSlot){0,PK_NONE}"
                out.append(f"    {_name(result)}=piton_dict_setdefault((PitonDict*){operand},{self._slot(call_args[0], types)},{def_slot}).bits;")
                types[result] = "int"
            elif method == "popitem":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_dict_popitem((PitonDict*){operand});")
                types[result] = "tuple"
            elif method == "clear":
                require_count((0,), "no arguments")
                out.append(f"    piton_dict_clear((PitonDict*){operand});")
                types[result] = "none"
            elif method == "copy":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_dict_copy((PitonDict*){operand});")
                types[result] = "dict"
                if obj in self._dict_key_types:
                    self._dict_key_types[result] = self._dict_key_types[obj]
                if obj in self._dict_val_types:
                    self._dict_val_types[result] = self._dict_val_types[obj]
            elif method == "update":
                # DICT_UPDATE_V1: merge otro dict (V1: solo dict -> dict);
                # CPython args iterable/kwargs quedan fuera.
                require_count((1,), "one argument (a dict or pairs)")
                _upd_type = types.get(call_args[0], "")
                if _upd_type in {"genexpr", "iterator:genexpr"}:
                    # DICT_UPDATE_PAIRS_V1: dict.update(genexpr de pares) —
                    # CPython acepta un iterable de pares (multiprocessing/
                    # __init__.py: globals().update((name, getattr(...)) ...)).
                    # El genexpr ya esta materializado como secuencia de tuplas
                    # y dict() lo convierte con el mismo helper de
                    # DICT_FROM_PAIRS_V1.
                    out.append(
                        f"    piton_dict_update((PitonDict*){operand},"
                        f"(PitonDict*)piton_conv_dict_from_genexpr({self._value(call_args[0])}));"
                    )
                    types[result] = "none"
                    return out
                if _upd_type != "dict":
                    raise NativeBuildError("Linux dict.update() requires a dict argument")
                out.append(f"    piton_dict_update((PitonDict*){operand},(PitonDict*){self._value(call_args[0])});")
                types[result] = "none"
            elif method in {"keys", "values", "items"}:
                require_count((0,), "no arguments")
                helper = {"keys": "piton_dict_keys", "values": "piton_dict_values", "items": "piton_dict_items"}[method]
                out.append(f"    {_name(result)}={helper}((PitonDict*){operand});")
                types[result] = "list"
                if method == "keys":
                    _kt = self._dict_key_types.get(obj)
                    if _kt is not None:
                        self._coll_elems[result] = _kt
                elif method == "values":
                    _k2 = obj
                    for _ in range(8):
                        _v = self._dict_val_types.get(_k2)
                        if _v is not None:
                            break
                        _al = aliases or {}
                        _k2n = _al.get(_k2)
                        if _k2n is None or _k2n == _k2:
                            break
                        _k2 = _k2n
                    _vt = self._dict_val_types.get(_k2)
                    if _vt is not None:
                        self._coll_elems[result] = _vt
                else:
                    self._coll_elems[result] = "tuple"
                    _ktype = self._dict_key_types.get(obj)
                    _vtype = None
                    _k2 = obj
                    for _ in range(8):
                        _v = self._dict_val_types.get(_k2)
                        if _v is not None:
                            _vtype = _v
                            break
                        _al = aliases or {}
                        _k2n = _al.get(_k2)
                        if _k2n is None or _k2n == _k2:
                            break
                        _k2 = _k2n
                    self._tuple_list_elems[result] = (_ktype if isinstance(_ktype, str) else "int", _vtype if isinstance(_vtype, str) else "int")
            else:
                raise NativeBuildError(f"Linux dict.{method}() is not supported")
        elif coll_type in {"set", "set:frozen"}:
            # SET_FROZEN_METHODS_V1: los métodos de consulta de set (issubset
            # /issuperset/isdisjoint/union/...) aplican igual sobre frozenset;
            # los mutadores fallan cerrado en el backend (frozenset inmutable).
            _mv_is_frozen = coll_type == "set:frozen"
            if _mv_is_frozen and method in {"add", "discard", "remove", "pop", "clear",
                                            "update", "intersection_update",
                                            "difference_update", "symmetric_difference_update"}:
                raise NativeBuildError(f"Linux frozenset.{method}() is not supported (immutable)")
            if method == "add":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_set_add((PitonSet*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
                require_count((1,), "exactly one argument")
                out.append(f"    piton_set_add((PitonSet*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            elif method == "discard":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_set_discard((PitonSet*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            elif method == "remove":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_set_remove((PitonSet*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            elif method == "clear":
                require_count((0,), "no arguments")
                out.append(f"    piton_set_clear((PitonSet*){operand});")
                types[result] = "none"
            elif method == "copy":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_set_copy((PitonSet*){operand});")
                types[result] = "set"
            elif method == "pop":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_set_pop((PitonSet*){operand}).bits;")
                types[result] = "int"
            elif method == "union":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_union((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                # SET_FROZEN_METHODS_V1: frozenset.union(...) devuelve frozenset.
                if _mv_is_frozen:
                    out.append(f"    ((PitonSet*){_name(result)})->kind=PK_FROZENSET;")
                    types[result] = "set:frozen"
                else:
                    types[result] = "set"
            elif method == "intersection":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_intersection((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                if _mv_is_frozen:
                    out.append(f"    ((PitonSet*){_name(result)})->kind=PK_FROZENSET;")
                    types[result] = "set:frozen"
                else:
                    types[result] = "set"
            elif method == "difference":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_difference((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                if _mv_is_frozen:
                    out.append(f"    ((PitonSet*){_name(result)})->kind=PK_FROZENSET;")
                    types[result] = "set:frozen"
                else:
                    types[result] = "set"
            elif method == "symmetric_difference":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_sym_diff((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                if _mv_is_frozen:
                    out.append(f"    ((PitonSet*){_name(result)})->kind=PK_FROZENSET;")
                    types[result] = "set:frozen"
                else:
                    types[result] = "set"
            elif method == "issubset":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_issubset((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "bool"
            elif method == "issuperset":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_issuperset((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "bool"
            elif method == "isdisjoint":
                require_count((1,), "one argument (a set)")
                out.append(f"    {_name(result)}=piton_set_isdisjoint((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "bool"
            elif method == "update":
                require_count((1,), "one argument (a set)")
                out.append(f"    piton_set_update((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "none"
            elif method == "intersection_update":
                require_count((1,), "one argument (a set)")
                out.append(f"    piton_set_intersection_update((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "none"
            elif method == "difference_update":
                require_count((1,), "one argument (a set)")
                out.append(f"    piton_set_difference_update((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "none"
            elif method == "symmetric_difference_update":
                require_count((1,), "one argument (a set)")
                out.append(f"    piton_set_symmetric_difference_update((PitonSet*){operand},(PitonSet*){self._value(call_args[0])});")
                types[result] = "none"
            else:
                raise NativeBuildError(f"Linux set.{method}() is not supported")

    def _emit_file_method(self, out: list[str], result: Any, method: str, obj: Any,
                          call_args: list[Any], types: dict[str, str]) -> None:
        """FILE_IO_V1: metodos del handle de fichero devuelto por abrir()."""
        operand = self._value(obj)

        def arity(counts, what):
            if len(call_args) not in counts:
                raise NativeBuildError("Linux file." + method + "() requires " + what)

        if method == "read":
            arity((0, 1), "no arguments or one int")
            num = self._value(call_args[0]) if call_args else "-1"
            out.append("    " + _name(result) + "=(long)piton_file_read((PitonFile*)" + operand + "," + num + ");")
            types[result] = "str"
        elif method == "readline":
            arity((0,), "no arguments")
            out.append("    " + _name(result) + "=(long)piton_file_readline((PitonFile*)" + operand + ");")
            types[result] = "str"
        elif method == "readlines":
            arity((0,), "no arguments")
            out.append("    {PitonSeq*_fl=piton_seq_new(PK_LIST,0);")
            out.append("     while(piton_file_has_more((PitonFile*)" + operand + "))")
            out.append("         {const char*_ln=piton_file_next_line((PitonFile*)" + operand + ");")
            out.append("          piton_seq_append(_fl,(PitonSlot){(long)_ln,PK_STR});}")
            out.append("     " + _name(result) + "=(long)_fl;}")
            types[result] = "list"
            self._coll_elems[result] = "str"
        elif method == "close":
            arity((0,), "no arguments")
            out.append("    piton_file_close((PitonFile*)" + operand + ");")
            types[result] = "none"
        elif method == "write":
            arity((1,), "exactly one str argument")
            out.append("    " + _name(result) + "=piton_file_write((PitonFile*)" + operand + ",(const char*)" + self._value(call_args[0]) + ");")
            types[result] = "int"
        elif method == "__enter__":
            arity((0,), "no arguments")
            out.append("    " + _name(result) + "=(long)" + operand + ";")
            types[result] = "file"
        elif method == "__exit__":
            arity((0, 3), "no arguments or three")
            out.append("    piton_file_close((PitonFile*)" + operand + ");" + _name(result) + "=0;")
            types[result] = "int"
        elif method == "fileno":
            arity((0,), "no arguments")
            out.append("    " + _name(result) + "=((PitonFile*)" + operand + ")->fd;")
            types[result] = "int"
        elif method == "tell":
            # FILE_SEEK_V1: la posicion de lectura vive en f->pos sobre el
            # buffer completo (f->data); tell es esa posicion.
            arity((0,), "no arguments")
            out.append(f"    {_name(result)}=((PitonFile*){operand})->pos;")
            types[result] = "int"
        elif method == "seek":
            # FILE_SEEK_V1: seek(offset[, whence]) sobre el buffer. whence 0
            # (SET), 1 (CUR), 2 (END, requiere piton_file_size). Devuelve la
            # nueva posicion absoluta, clampada a [0, size] como CPython.
            arity((1, 2), "one or two arguments")
            _whence = "0"
            if len(call_args) == 2:
                _wt = types.get(call_args[1], "int")
                if _wt not in {"int", "bool"}:
                    raise NativeBuildError("Linux file.seek() whence must be an int")
                _whence = self._value(call_args[1])
            out.append(
                f"    {{PitonFile*_sf=(PitonFile*){operand};"
                f"long _sp=_sf->pos;long _ssz=(long)piton_file_size(_sf);"
                f"long _so={self._value(call_args[0])};"
                f"if({_whence}==1)_sp+=_so;else if({_whence}==2)_sp=_ssz+_so;else _sp=_so;"
                f"if(_sp<0)_sp=0;if(_sp>_ssz)_sp=_ssz;_sf->pos=_sp;"
                f"{_name(result)}=_sp;}}"
            )
            types[result] = "int"
        elif method == "flush":
            # FILE_FLUSH_V1: el buffer se escribe al cerrar; flush es no-op.
            arity((0,), "no arguments")
            out.append("    /* file.flush() no-op */")
            types[result] = "none"
        elif method in {"truncate", "detach", "isatty"}:
            # FILE_IO_GAP_V1: CPython si las soporta; el subconjunto no las
            # modela. Fail-closed.
            raise NativeBuildError("Linux file." + method + "() is not available in the native subset")
        else:
            raise NativeBuildError("Linux file." + method + "() is not supported")

    def _emit_bytes_method(self, out: list[str], result: Any, method: str, obj: Any,
                           call_args: list[Any], is_bytearray: bool, types: dict[str, str]) -> None:
        operand = self._value(obj)
        target_type = "bytearray" if is_bytearray else "bytes"

        def require_count(counts: tuple[int, ...], what: str) -> None:
            if len(call_args) not in counts:
                raise NativeBuildError(f"Linux {target_type}.{method}() requires {what}")

        if method == "decode":
            require_count((0, 1), "zero or one str argument")
            enc = f"(const char*){self._value(call_args[0])}" if call_args else '(const char*)0'
            out.append(f"    {_name(result)}=(long)piton_bytes_decode((void*){operand},{enc});")
            types[result] = "str"
        elif method == "hex":
            require_count((0,), "no arguments")
            out.append(f"    {_name(result)}=(long)piton_bytes_hex((void*){operand});")
            types[result] = "str"
        elif method == "count":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_count((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "int"
        elif method == "find":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_find((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "int"
        elif method == "rfind":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_rfind((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "int"
        elif method == "index":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_index((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "int"
        elif method == "rindex":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_rindex((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "int"
        elif method == "startswith":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_startswith((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "bool"
        elif method == "endswith":
            require_count((1,), "exactly one argument")
            out.append(f"    {_name(result)}=piton_bytes_endswith((void*){operand},{self._slot(call_args[0], types)});")
            types[result] = "bool"
        elif method == "join":
            # BYTES_JOIN_V1: `sep.join(list_of_bytes)` como en CPython.
            require_count((1,), "exactly one argument")
            if types.get(call_args[0]) not in {"list", "tuple"}:
                raise NativeBuildError("Linux bytes.join() requires a list or tuple argument")
            out.append(f"    {_name(result)}=(long)piton_bytes_join((void*){operand},(void*){self._value(call_args[0])});")
            types[result] = "bytes"
        elif method == "append" and is_bytearray:
            require_count((1,), "exactly one int argument")
            out.append(f"    piton_bytearray_append((PitonByteArray*){operand},{self._value(call_args[0])});")
            types[result] = "none"
        elif method == "extend" and is_bytearray:
            require_count((1,), "exactly one iterable argument")
            out.append(f"    piton_bytearray_extend((PitonByteArray*){operand},{self._slot(call_args[0], types)});")
            types[result] = "none"
        else:
            raise NativeBuildError(f"Linux {target_type}.{method}() is not supported")

    def _emit_str_method(self, out: list[str], result: Any, method: str, obj: Any,
                         call_args: list[Any], types: dict[str, str],
                         _fmt_kw: tuple = ()) -> None:
        """STR_METHODS_V1: builtin str methods bound by static dispatch.

        Arity AND argument static types are validated at build time; the
        wrong shape fails closed with NativeBuildError. The C helpers raise
        clean process errors for inputs that are valid Python but outside
        the subset (non-ASCII case conversion, non-str join elements, bad
        format fields) — the same uncatchable-error convention as the other
        runtime helpers (IndexError, TypeError in seq_get and friends).
        """
        operand = self._value(obj)

        def require_str(index: int, what: str) -> str:
            if len(call_args) <= index or types.get(call_args[index]) != "str":
                raise NativeBuildError(f"Linux str.{method}() requires {what}")
            return self._value(call_args[index])

        def require_count(count: int, what: str) -> None:
            if len(call_args) != count:
                raise NativeBuildError(f"Linux str.{method}() requires {what}")

        if method in {"upper", "lower"}:
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_case((const char*){operand},{1 if method == 'upper' else 0});")
            types[result] = "str"
        elif method == "find":
            # STR_FIND_OFFSET_V1: `s.find(sub, start)` — el offset opcional
            # baja a piton_str_find_at (startswith/endswith ya tenian su
            # variante _at). _markupbase: `rawdata.find(">", j)`.
            if len(call_args) == 2:
                if types.get(call_args[1]) not in {"int", "bool"}:
                    raise NativeBuildError("Linux str.find() requires an int start offset")
                out.append(
                    f"    {_name(result)}=piton_str_find_at((const char*){operand},"
                    f"{require_str(0, 'a str argument')},{self._value(call_args[1])});"
                )
                types[result] = "int"
                return
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_find((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method in {"startswith", "endswith"}:
            # STR_OFFSET_PREFIX_V1: `s.startswith(p, start)` /
            # `s.endswith(p, start)` — la comparacion es sobre s[start:].
            if len(call_args) == 2:
                if types.get(call_args[1]) not in {"int", "bool"}:
                    raise NativeBuildError(f"Linux str.{method}() requires an int start offset")
                helper_at = (
                    "piton_str_startswith_at" if method == "startswith" else "piton_str_endswith_at"
                )
                out.append(
                    f"    {_name(result)}={helper_at}((const char*){operand},"
                    f"{require_str(0, 'a str argument')},{self._value(call_args[1])});"
                )
                types[result] = "bool"
                return
            require_count(1, "exactly one str argument")
            helper = "piton_str_startswith" if method == "startswith" else "piton_str_endswith"
            _p0 = call_args[0]
            _p0t = types.get(_p0)
            if _p0t in {"", "int", "none", "bool", "float", "builtin", "bytes", "bytearray"} or (_p0t or "").startswith("object:"):
                # STR_PREFIX_DYNAMIC_V1: el prefijo no tiene tipo estatico str;
                # el runtime levanta el TypeError de CPython si no es un str.
                # Aceptamos bool, float, builtin y objetos — el runtime
                # intentara str() o fallara con TypeError claro.
                out.append(
                    f"    {_name(result)}=piton_str_prefix_dyn((const char*){operand},"
                    f"{self._value(_p0)},{1 if method == 'endswith' else 0});"
                )
            else:
                out.append(f"    {_name(result)}={helper}((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "bool"
        elif method in {"removeprefix", "removesuffix"}:
            require_count(1, "exactly one str argument")
            helper = "piton_str_removeprefix" if method == "removeprefix" else "piton_str_removesuffix"
            out.append(f"    {_name(result)}=(long){helper}((const char*){operand},(const char*){require_str(0, 'a str argument')});")
            types[result] = "str"
        elif method == "replace":
            require_count(2, "exactly two str arguments")
            out.append(
                f"    {_name(result)}=(long)piton_str_replace((const char*){operand},"
                f"{require_str(0, 'two str arguments')},{require_str(1, 'two str arguments')});"
            )
            types[result] = "str"
        elif method == "split":
            # SPLIT_MAXSPLIT_V1: s.split(sep, maxsplit) — segundo arg int
            # (el kwarg lo expande MIR a posicional).
            if (
                len(call_args) == 2
                and types.get(call_args[0]) in {"str", "none"}
                and types.get(call_args[1]) in {"int", "bool"}
            ):
                sep = "(const char*)0" if types.get(call_args[0]) == "none" else f"(const char*){self._value(call_args[0])}"
                out.append(f"    {_name(result)}=piton_str_split_max((const char*){operand},{sep},{self._value(call_args[1])});")
                types[result] = "list"
                # SPLIT_ELEM_STR_V1: los elementos son str (comp[0].upper()
                # necesita el tipo; nturl2path:34 — antes "method receiver
                # class is not statically known", igual que splitlines/rsplit).
                self._coll_elems[result] = "str"
                return out
            if len(call_args) > 1:
                raise NativeBuildError("Linux str.split() requires zero or one argument")
            if call_args and types.get(call_args[0]) not in {"str", "none"}:
                raise NativeBuildError("Linux str.split() requires a str separator or nothing")
            sep = "(const char*)0" if not call_args or types.get(call_args[0]) == "none" else f"(const char*){self._value(call_args[0])}"
            out.append(f"    {_name(result)}=piton_str_split((const char*){operand},{sep});")
            types[result] = "list"
            # SPLIT_ELEM_STR_V1: idem — elems str.
            self._coll_elems[result] = "str"
        elif method in {"strip", "lstrip", "rstrip"}:
            if len(call_args) not in {0, 1}:
                raise NativeBuildError(f"Linux str.{method}() requires no or one argument")
            mode = {"strip": 0, "lstrip": 1, "rstrip": 2}[method]
            if call_args and types.get(call_args[0]) == "str":
                # STRIP_CHRS_CAST_V1: el chars es un long (temp o literal con
                # cast); pasarlo directo a `const char*` no compila.
                out.append(
                    f"    {_name(result)}=(long)piton_str_strip_chrs((const char*){operand},"
                    f"(const char*)(long){self._value(call_args[0])},{mode});"
                )
            else:
                require_count(0, "no arguments")
                out.append(f"    {_name(result)}=(long)piton_str_strip((const char*){operand},{mode});")
            types[result] = "str"
        elif method == "capitalize":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_capitalize((const char*){operand});")
            types[result] = "str"
        elif method == "title":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_title((const char*){operand});")
            types[result] = "str"
        elif method == "casefold":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_casefold((const char*){operand});")
            types[result] = "str"
        elif method == "isascii":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isascii((const char*){operand});")
            types[result] = "bool"
        elif method == "isprintable":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isprintable((const char*){operand});")
            types[result] = "bool"
        elif method == "encode":
            if len(call_args) > 1:
                raise NativeBuildError("Linux str.encode() takes at most one argument")
            enc_expr = "0"
            if call_args:
                if types.get(call_args[0]) != "str":
                    raise NativeBuildError("Linux str.encode() encoding must be a str")
                enc_expr = self._value(call_args[0])
            out.append(f"    {_name(result)}=piton_str_encode((const char*){operand},{enc_expr});")
            out.append("    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}")
            types[result] = "bytes"
        elif method == "isidentifier":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isidentifier((const char*){operand});")
            types[result] = "bool"
        elif method == "swapcase":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_swapcase((const char*){operand});")
            types[result] = "str"
        elif method == "maketrans":
            # STR_MAKETRANS_V1: str.maketrans(dict) -> tabla int -> str|None,
            # que es exactamente la que str.translate ya consume. El dict puede
            # ser un valor de tiempo de ejecucion (shlex:
            # dict.fromkeys(punctuation_chars)), asi que la validacion vive en
            # el helper C, no aqui.
            require_count(1, "exactly one argument (a dict)")
            if types.get(call_args[0], "") != "dict":
                raise NativeBuildError("Linux str.maketrans() requires a dict argument")
            out.append(f"    {_name(result)}=(long)piton_str_maketrans((PitonDict*){self._value(call_args[0])});")
            types[result] = "dict"
        elif method == "translate":
            # STR_TRANSLATE_V1: str.translate(dict) — mapeo str -> str/''.
            require_count(1, "exactly one argument (a dict)")
            if types.get(call_args[0], "") != "dict":
                raise NativeBuildError("Linux str.translate() requires a dict argument")
            out.append(f"    {_name(result)}=piton_str_translate((const char*){operand},(PitonDict*){self._value(call_args[0])});")
            types[result] = "str"
        elif method == "zfill":
            require_count(1, "exactly one int argument")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.zfill() requires an int")
            out.append(f"    {_name(result)}=(long)piton_str_zfill((const char*){operand},{self._value(call_args[0])});")
            types[result] = "str"
        elif method == "ljust":
            if len(call_args) not in {1, 2}:
                raise NativeBuildError(f"Linux str.ljust() requires one int argument and an optional str fill")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.ljust() requires an int")
            _fill = f"*((const char*){self._value(call_args[1])})" if len(call_args) == 2 else "' '"
            if len(call_args) == 2 and types.get(call_args[1]) != "str":
                raise NativeBuildError(f"Linux str.{method}() fill must be a str")
            out.append(f"    {_name(result)}=(long)piton_str_padw((const char*){operand},{self._value(call_args[0])},0,{_fill});")
            types[result] = "str"
        elif method == "rjust":
            if len(call_args) not in {1, 2}:
                raise NativeBuildError(f"Linux str.rjust() requires one int argument and an optional str fill")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.rjust() requires an int")
            _fill = f"*((const char*){self._value(call_args[1])})" if len(call_args) == 2 else "' '"
            if len(call_args) == 2 and types.get(call_args[1]) != "str":
                raise NativeBuildError(f"Linux str.{method}() fill must be a str")
            out.append(f"    {_name(result)}=(long)piton_str_padw((const char*){operand},{self._value(call_args[0])},1,{_fill});")
            types[result] = "str"
        elif method == "center":
            if len(call_args) not in {1, 2}:
                raise NativeBuildError("Linux str.center() requires one int argument and an optional str fill")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.center() requires an int")
            _fill = f"*((const char*){self._value(call_args[1])})" if len(call_args) == 2 else "' '"
            if len(call_args) == 2 and types.get(call_args[1]) != "str":
                raise NativeBuildError("Linux str.center() fill must be a str")
            out.append(f"    {_name(result)}=(long)piton_str_center((const char*){operand},{self._value(call_args[0])},{_fill});")
            types[result] = "str"
        elif method in {"isalpha", "isdigit", "isalnum", "isspace"}:
            require_count(0, "no arguments")
            helper = {"isalpha": 0, "isdigit": 1, "isalnum": 2, "isspace": 3}[method]
            out.append(f"    {_name(result)}=piton_str_islapha_impl((const char*){operand},{helper});")
            types[result] = "bool"
        elif method in {"isupper", "islower"}:
            require_count(0, "no arguments")
            helper = 1 if method == "isupper" else 0
            out.append(f"    {_name(result)}=piton_str_isupper_impl((const char*){operand},{helper});")
            types[result] = "bool"
        elif method == "istitle":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_istitle_impl((const char*){operand});")
            types[result] = "bool"
        elif method == "count":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_count((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "index":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_index_f((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "rfind":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_rfind((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "rindex":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_rindex_f((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "partition":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_partition((const char*){operand},{require_str(0, 'a str separator')});")
            types[result] = "tuple"
            self._tuple_elems[result] = (("%pre", "str"), ("%sep", "str"), ("%suf", "str"))
        elif method == "rpartition":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_rpartition((const char*){operand},{require_str(0, 'a str separator')});")
            types[result] = "tuple"
            self._tuple_elems[result] = (("%pre", "str"), ("%sep", "str"), ("%suf", "str"))
        elif method == "rsplit":
            if len(call_args) not in {0, 1, 2}:
                raise NativeBuildError("Linux str.rsplit() requires zero, one or two arguments")
            if call_args and types.get(call_args[0]) != "str":
                raise NativeBuildError("Linux str.rsplit() requires a str separator")
            sep = "(const char*)0" if not call_args else f"(const char*){self._value(call_args[0])}"
            maxsplit = "-1"
            if len(call_args) == 2:
                if types.get(call_args[1]) not in {"int", "bool"}:
                    raise NativeBuildError("Linux str.rsplit() maxsplit must be an int")
                maxsplit = self._value(call_args[1])
            out.append(f"    {_name(result)}=piton_str_rsplit((const char*){operand},{sep},{maxsplit});")
            types[result] = "list"
            self._coll_elems[result] = "str"
        elif method == "splitlines":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_splitlines((const char*){operand});")
            types[result] = "list"
            self._coll_elems[result] = "str"
        elif method == "expandtabs":
            if len(call_args) not in {0, 1}:
                raise NativeBuildError("Linux str.expandtabs() requires zero or one argument")
            size = self._value(call_args[0]) if call_args else "8"
            if call_args and types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.expandtabs() requires an int tabsize")
            out.append(f"    {_name(result)}=piton_str_expandtabs((const char*){operand},{size});")
            types[result] = "str"
        elif method == "join":
            require_count(1, "exactly one list or tuple argument")
            if types.get(call_args[0]) in {"genexpr", "iterator:genexpr"}:
                # JOIN_GENEXPR_V1: `sep.join(f(x) for x in xs)` materializa el
                # genexpr (sqlite3/dump.py: join de una comprehension de strs).
                out.append(
                    f"    {{PitonSeq*_jl=(PitonSeq*)piton_genexpr_to_list({self._value(call_args[0])});"
                )
                out.append(
                    f"    {_name(result)}=(long)piton_str_join((const char*){operand},_jl);}}"
                )
                types[result] = "str"
                return out
            if types.get(call_args[0]) in {"iterator:map", "iterator:filter"}:
                # JOIN_ITER_V1: `sep.join(map(f, xs))` materializa el iterador
                # antes de unir (como hace CPython internamente).
                out.append(
                    f"    {{PitonSeq*_jl=(PitonSeq*)piton_collect_any_iter({self._value(call_args[0])});"
                )
                out.append(
                    f"    {_name(result)}=(long)piton_str_join((const char*){operand},_jl);}}"
                )
                types[result] = "str"
                return out
            if types.get(call_args[0]) in {"", "int", "none", "str"}:
                # JOIN_OF_DYNAMIC_V1: el argumento no tiene tipo estatico; el
                # runtime acepta lista/tupla/str y levanta el TypeError de
                # CPython para el resto.
                out.append(f"    {_name(result)}=piton_str_join_dyn((const char*){operand},{self._value(call_args[0])});")
                types[result] = "str"
                return out
            if types.get(call_args[0]) not in {"list", "tuple"}:
                raise NativeBuildError("Linux str.join() requires one list or tuple argument")
            out.append(f"    {_name(result)}=(long)piton_str_join((const char*){operand},(PitonSeq*){self._value(call_args[0])});")
            types[result] = "str"
        elif method == "format":
            # FMT_SPEC_V1: {}/ {N} positional substitution with format
            # specs. Each argument is converted to the target type's
            # string (type char in the spec), then the presentation part
            # (fill/align/sign/zero/width/comma/precision) is applied
            # via piton_str_apply_spec. Named placeholders and keyword
            # arguments fail closed.
            # obj can be: a temp (%12) holding a literal, a variable name,
            # or the literal string itself. Look up temps in constants,
            # names in _fn_consts, and use literals directly.
            template = self._literal_str_operand(obj, self._fn_consts)
            if template is None:
                # no-spec {} substitution on a runtime string: the old
                # pointer-passing path still handles {} and {N} exactly.
                pieces = []
                for index, value in enumerate(call_args):
                    arg_type = types.get(value, "int")
                    if arg_type == "str":
                        pieces.append(f"const char*_pf{index}=(const char*){self._value(value)};")
                    elif arg_type == "int":
                        pieces.append(f"const char*_pf{index}=(const char*)piton_str_from_int({self._value(value)});")
                    elif arg_type == "float":
                        pieces.append(f"const char*_pf{index}=(const char*)piton_str_from_float({self._value(value)});")
                    elif arg_type == "bool":
                        pieces.append(f'const char*_pf{index}={self._value(value)}?"True":"False";')
                    elif arg_type == "none":
                        pieces.append(f'const char*_pf{index}="None";')
                    else:
                        raise NativeBuildError(f"Linux str.format() does not support {arg_type} arguments")
                if not pieces:
                    # FMT_NOFIELD_V1: idem, plantilla dinamica (todos los
                    # campos nombrados): array vacio y argc 0.
                    out.append(
                        f"    {{const char*_fa[]={{0}};"
                        f" {_name(result)}=(long)piton_str_format((const char*){self._value(obj)},0,_fa);}}"
                    )
                    types[result] = "str"
                    return out
                names = ",".join(f"_pf{index}" for index in range(len(call_args)))
                out.append(
                    f"    {{{''.join(pieces)}const char*_fa[]={{{names}}};"
                    f" {_name(result)}=(long)piton_str_format((const char*){self._value(obj)},{len(call_args)},_fa);}}"
                )
                types[result] = "str"
                return out
            if not isinstance(template, str):
                raise NativeBuildError(
                    "Linux str.format() with a format spec requires a literal template"
                )
            simplified, fields, _fmt_names = self._parse_format_template(template)
            # a field may be referenced twice ({0} {0}), so the count is not
            # an equality: automatic numbering may not exceed the arguments,
            # and explicit indices are bounds-checked below.
            if len(fields) > len(call_args) and any(f[2] == "{}" for f in fields):
                raise NativeBuildError(
                    f"Linux str.format(): {len(fields)} placeholder(s) but {len(call_args)} argument(s)"
                )
            # FMT_ACCESS_V1: {0[1]} / {0.real} resuelven a un temporal C antes
            # de entrar al engine; el template queda como {0}.
            accessors: dict[int, tuple] = {}
            for arg_idx, _spec, _token, accessor in fields:
                if accessor is None:
                    continue
                if arg_idx in accessors and accessors[arg_idx] != accessor:
                    raise NativeBuildError(
                        f"str.format(): conflicting accessors for field index {arg_idx}"
                    )
                accessors[arg_idx] = accessor
            # FMT_NAMED_V1: los argumentos por keyword se colocan en los
            # campos con NOMBRE (el mapa nombre->indice del parser). Un
            # campo con nombre sin su keyword es un error ESTATICO (CPython
            # levanta KeyError; el backend falla cerrado en build).
            if _fmt_names and not _fmt_kw:
                raise NativeBuildError(
                    f"str.format(): named placeholder {sorted(_fmt_names)[0]!r} "
                    "requires a keyword argument"
                )
            if _fmt_kw:
                _by_name = {name: value for name, value in _fmt_kw}
                _named_idxs = set(_fmt_names.values())
                for _n, _v in _fmt_kw:
                    if _n not in _fmt_names:
                        raise NativeBuildError(
                            f"str.format(): unexpected keyword argument '{_n}'"
                        )
                _max_named = max(_named_idxs) + 1 if _named_idxs else 0
                if _max_named > len(call_args):
                    call_args = list(call_args) + [None] * (_max_named - len(call_args))
                for _n, _v in _fmt_kw:
                    call_args[_fmt_names[_n]] = _v
            by_index: dict[int, str] = {}
            for arg_idx, spec, _token, _accessor in fields:
                if arg_idx in by_index:
                    continue
                if arg_idx < 0 or arg_idx >= len(call_args):
                    raise NativeBuildError(
                        f"str.format(): field index {arg_idx} out of range "
                        f"for {len(call_args)} argument(s)"
                    )
                by_index[arg_idx] = spec
            pre_lines: list[str] = []
            pieces = []
            for index in range(len(call_args)):
                value = call_args[index]
                spec = by_index.get(index, "")
                arg_type = types.get(value, "int")
                expr = self._value(value)
                resolved_type = arg_type
                accessor = accessors.get(index)
                if accessor is not None:
                    if accessor[0] == "index":
                        if arg_type not in {"list", "tuple"}:
                            raise NativeBuildError(
                                f"str.format(): subscript field requires a list or tuple, not {arg_type}"
                            )
                        expr = f"_fi{index}"
                        pre_lines.append(
                            f"    long {expr}=piton_seq_get((PitonSeq*){self._value(value)},{accessor[1]}).bits;"
                        )
                        elem = self._coll_elems.get(value) or self._tuple_elems.get(value) or "int"
                        resolved_type = elem
                    elif accessor[0] == "key":
                        # FMT_KEY_V1: {0[Jack]} sobre un dict — la key string
                        # se resuelve con piton_dict_get_1 (miss -> None slot,
                        # que CPython elevaria a KeyError: gap documentado).
                        if arg_type not in {"dict", "dict:module"}:
                            raise NativeBuildError(
                                f"str.format(): key field requires a dict, not {arg_type}"
                            )
                        expr = f"_fi{index}"
                        pre_lines.append(
                            f"    long {expr}=piton_dict_get_1((PitonDict*){self._value(value)},"
                            f"piton_slot((long){json.dumps(accessor[1])},PK_STR)).bits;"
                        )
                        resolved_type = self._dict_val_types.get(value) or "int"
                    elif accessor[0] == "attr" and accessor[1] == "real":
                        if arg_type not in {"int", "float"}:
                            raise NativeBuildError(
                                f"str.format(): .real requires a numeric argument, not {arg_type}"
                            )
                        resolved_type = arg_type
                    else:
                        raise NativeBuildError(
                            f"str.format(): unsupported accessor {accessor!r}"
                        )
                type_char = self._format_spec_type(spec)
                pres = self._format_spec_presentation(spec)
                self._validate_presentation_spec(pres)
                # convert to target type string
                if type_char in {"x", "X", "o"}:
                    base = {"x": 16, "X": 16, "o": 8}[type_char]
                    if arg_type not in {"int", "bool"}:
                        raise NativeBuildError(f"Linux str.format() %{type_char} requires an int, not {arg_type}")
                    converted = f"piton_str_from_int_base({expr},{base},{1 if type_char == 'X' else 0})"
                elif type_char == "b":
                    if arg_type not in {"int", "bool"}:
                        raise NativeBuildError(f"Linux str.format() %b requires an int, not {arg_type}")
                    # binary without "0b" prefix (CPython {:b} is bare digits)
                    converted = f"piton_str_from_int_base({expr},2,0)"
                elif type_char == "d":
                    if resolved_type in {"int", "bool"}:
                        converted = f"piton_str_from_int({expr})"
                    elif resolved_type == "float":
                        converted = f"piton_str_from_int((long)piton_bits_double({expr}))"
                    else:
                        raise NativeBuildError(f"Linux str.format() %d requires a real number, not {resolved_type}")
                elif type_char in {"f", "e", "E", "g", "G"}:
                    # FMT_FLOAT_V1: real decimal-based rounding for float specs.
                    prec = 6
                    if "." in pres:
                        try:
                            prec = int(pres.split(".")[1])
                        except ValueError:
                            prec = 6
                    _foldv = f"({expr})" if resolved_type == "float" else f"piton_double_bits((double){expr})"
                    if type_char in {"f"}:
                        converted = f"piton_float_fmt_fixed({_foldv},{prec})"
                    elif type_char in {"e", "E"}:
                        converted = f"piton_float_fmt_exp({_foldv},{prec},{1 if type_char == 'E' else 0})"
                    else:
                        converted = f"piton_float_fmt_g({_foldv},{1 if type_char == 'G' else 0})"
                elif type_char == "%":
                    _foldv = f"({expr})" if resolved_type == "float" else f"piton_double_bits((double){expr})"
                    prec = 6
                    if "." in pres:
                        try:
                            prec = int(pres.split(".")[1])
                        except ValueError:
                            prec = 6
                    converted = f"piton_float_fmt_pct({_foldv},{prec})"
                else:
                    # no type char: use the natural string conversion
                    if resolved_type == "int":
                        converted = f"piton_str_from_int({expr})"
                    elif resolved_type == "float":
                        converted = f"piton_str_from_float({expr})"
                    elif resolved_type == "bool":
                        converted = f'{expr}?"True":"False"'
                    elif resolved_type == "none":
                        converted = '"None"'
                    elif resolved_type == "str":
                        converted = f"(const char*){expr}"
                    elif resolved_type == "complex":
                        converted = f"piton_complex_str({expr})"
                    else:
                        raise NativeBuildError(f"Linux str.format() does not support {resolved_type} arguments")
                # apply presentation spec
                if pres:
                    _presented = pres.split(".", 1)[0] if type_char in {"f", "e", "E", "g", "G", "%"} else pres
                    if _presented:
                        pieces.append(f"const char*_pf{index}=(const char*)piton_str_apply_spec((const char*){converted},{_str_c_literal(_presented)});")
                    else:
                        pieces.append(f"const char*_pf{index}=(const char*){converted};")
                else:
                    pieces.append(f"const char*_pf{index}=(const char*){converted};")
            out.extend(pre_lines)
            if not pieces:
                # FMT_NOFIELD_V1: sin campos que sustituir. CPython ignora los
                # argumentos extra: argc 0 y array vacio. Antes se emitia
                # `_fa[]={_pf0}` sin declarar _pf0 cuando HABIA argumentos pero
                # ningun campo, y ese C no compilaba (warnings.py).
                out.append(
                    f"    {{const char*_fa[]={{0}}; {_name(result)}=(long)piton_str_format((const char*){_str_c_literal(simplified)},0,_fa);}}"
                )
                types[result] = "str"
            else:
                names = ",".join(f"_pf{index}" for index in range(len(call_args)))
                out.append(f"    {{{''.join(pieces)}const char*_fa[]={{{names}}}; {_name(result)}=(long)piton_str_format((const char*){_str_c_literal(simplified)},{len(call_args)},_fa);}}")
                types[result] = "str"
        elif method in {"isalpha", "isdigit", "isalnum", "isspace", "istitle", "isupper", "islower"}:
            # STR_PREDS_V1: per-kind predicates (ASCII subset). Empty
            # strings are False for all of them; isupper/islower/istitle
            # also need at least one cased character (helper-internal).
            require_count(0, "no arguments")
            mode = {"isalpha": 0, "isdigit": 1, "isalnum": 2, "isspace": 3,
                    "istitle": 4, "isupper": 5, "islower": 6}[method]
            out.append(f"    {_name(result)}=piton_str_pred((const char*){operand},{mode});")
            types[result] = "bool"
        elif method == "capitalize":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_capitalize((const char*){operand});")
            types[result] = "str"
        elif method == "title":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_title((const char*){operand});")
            types[result] = "str"
        elif method == "casefold":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_casefold((const char*){operand});")
            types[result] = "str"
        elif method == "isascii":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isascii((const char*){operand});")
            types[result] = "bool"
        elif method == "isprintable":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isprintable((const char*){operand});")
            types[result] = "bool"
        elif method == "encode":
            if len(call_args) > 1:
                raise NativeBuildError("Linux str.encode() takes at most one argument")
            enc_expr = "0"
            if call_args:
                if types.get(call_args[0]) != "str":
                    raise NativeBuildError("Linux str.encode() encoding must be a str")
                enc_expr = self._value(call_args[0])
            out.append(f"    {_name(result)}=piton_str_encode((const char*){operand},{enc_expr});")
            out.append("    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}")
            types[result] = "bytes"
        elif method == "isidentifier":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=piton_str_isidentifier((const char*){operand});")
            types[result] = "bool"
        elif method == "swapcase":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_swapcase((const char*){operand});")
            types[result] = "str"
        elif method == "maketrans":
            # STR_MAKETRANS_V1: str.maketrans(dict) -> tabla int -> str|None,
            # que es exactamente la que str.translate ya consume. El dict puede
            # ser un valor de tiempo de ejecucion (shlex:
            # dict.fromkeys(punctuation_chars)), asi que la validacion vive en
            # el helper C, no aqui.
            require_count(1, "exactly one argument (a dict)")
            if types.get(call_args[0], "") != "dict":
                raise NativeBuildError("Linux str.maketrans() requires a dict argument")
            out.append(f"    {_name(result)}=(long)piton_str_maketrans((PitonDict*){self._value(call_args[0])});")
            types[result] = "dict"
        elif method == "translate":
            # STR_TRANSLATE_V1: str.translate(dict) — mapeo str -> str/''.
            require_count(1, "exactly one argument (a dict)")
            if types.get(call_args[0], "") != "dict":
                raise NativeBuildError("Linux str.translate() requires a dict argument")
            out.append(f"    {_name(result)}=piton_str_translate((const char*){operand},(PitonDict*){self._value(call_args[0])});")
            types[result] = "str"
        elif method == "zfill":
            # STR_PADS_V1: sign-aware zero fill ('-5'.zfill(3) -> '-05').
            require_count(1, "exactly one int argument")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError("Linux str.zfill() requires an int width")
            out.append(f"    {_name(result)}=(long)piton_str_zfill((const char*){operand},{self._value(call_args[0])});")
            types[result] = "str"
        elif method in {"ljust", "rjust", "center"}:
            # STR_PADS_V1: width + optional single-char fill (default ' ').
            if len(call_args) not in (1, 2):
                raise NativeBuildError(f"Linux str.{method}() requires one or two arguments")
            if types.get(call_args[0]) not in {"int", "bool"}:
                raise NativeBuildError(f"Linux str.{method}() requires an int width")
            if len(call_args) == 2:
                fill = self._fn_consts.get(call_args[1])
                if not (isinstance(fill, str) and len(fill) == 1):
                    raise NativeBuildError(f"Linux str.{method}() fill must be a 1-character str literal")
                fill_c = ord(fill)
            else:
                fill_c = 32
            mode = {"ljust": 0, "rjust": 1, "center": 2}[method]
            out.append(f"    {_name(result)}=(long)piton_str_just((const char*){operand},{self._value(call_args[0])},{fill_c},{mode});")
            types[result] = "str"
        elif method == "count":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_count((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "rfind":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_rfind((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "rindex":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_rindex_f((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method == "index":
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_index_f((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        else:
            if method == "format_map":
                # FORMAT_MAP_V1: '{a}'.format_map({'a': 1}) — template literal
                # y mapping literal; cada campo named se resuelve (clave
                # garantizada por el record del dict literal, asi que no puede
                # haber KeyError) a un valor y el render reusa
                # piton_str_format. Specs/conversiones/accessors y mappings no
                # literales quedan fail-closed arriba.
                require_count(1, "exactly one argument (a mapping)")
                mapping = call_args[0]
                if types.get(mapping, "") != "dict":
                    raise NativeBuildError("Linux str.format_map() requires a dict mapping")
                template = self._literal_str_operand(obj, self._fn_consts)
                if template is None:
                    raise NativeBuildError("Linux str.format_map() requires a literal template")
                drec = self._dict_elems.get(mapping)
                if drec is None:
                    raise NativeBuildError(
                        "Linux str.format_map() requires a literal dict mapping"
                    )
                simplified, field_names = self._parse_format_map_template(template)
                lines: list[str] = []
                for k, fname in enumerate(field_names):
                    if fname not in drec:
                        raise NativeBuildError(
                            f"Linux str.format_map(): {fname!r} is not a literal key of the mapping"
                        )
                    value_operand, value_type = drec[fname]
                    if value_operand in self._fn_consts:
                        lit = self._fn_consts[value_operand]
                        if isinstance(lit, str):
                            lit_c = json.dumps(lit)
                        elif isinstance(lit, bool):
                            lit_c = '"True"' if lit else '"False"'
                        elif isinstance(lit, int):
                            lit_c = f"piton_str_from_int({lit}L)"
                        elif isinstance(lit, float):
                            # piton_str_from_float recibe BITS double (long),
                            # no un double literal: hay que codificarlos.
                            if lit != lit or lit in (float("inf"), float("-inf")):
                                raise NativeBuildError(
                                    "Linux str.format_map(): non-finite float literals are not supported"
                                )
                            lit_c = f"piton_str_from_float(piton_double_bits({lit!r}))"
                        elif lit is None:
                            lit_c = '"None"'
                        else:
                            raise NativeBuildError(
                                "Linux str.format_map(): unsupported literal value"
                            )
                        lines.append(f"    const char*_fmv{k}=(const char*){lit_c};")
                    else:
                        conv = {
                            "str": f"(const char*)_fm{k}.bits",
                            "int": f"piton_str_from_int(_fm{k}.bits)",
                            "float": f"piton_str_from_float(_fm{k}.bits)",
                            "bool": f'(_fm{k}.bits?"True":"False")',
                            "none": '"None"',
                        }.get(value_type)
                        if conv is None:
                            raise NativeBuildError(
                                f"Linux str.format_map() does not support {value_type} values"
                            )
                        lines.append(
                            f"    PitonSlot _fm{k}=piton_dict_get((PitonDict*){self._value(mapping)},"
                            f"piton_slot((long){json.dumps(fname)},PK_STR));"
                        )
                        lines.append(f"    const char*_fmv{k}=(const char*){conv};")
                out.append("    {")
                out.extend(lines)
                if not field_names:
                    out.append('    const char*_fmv0="";')
                    names_c, count_c = "_fmv0", 0
                else:
                    names_c = ",".join(f"_fmv{k}" for k in range(len(field_names)))
                    count_c = len(field_names)
                out.append(
                    f"    const char*_fma[]={{{names_c}}};"
                    f" {_name(result)}=(long)piton_str_format((const char*){_str_c_literal(simplified)},{count_c},_fma);"
                )
                out.append("    }")
                types[result] = "str"
                return out
            raise NativeBuildError(f"Linux str.{method}() is not supported")

    def _runtime_type_error(self, out: list[str], message: str) -> None:
        """ORDER_MIXED_TYPES_V1 & friends: CPython raises TypeError at
        runtime; emit the same unhandled TypeError instead of failing at
        build time (FAIL_CLOSED -> RAISE_EQ)."""
        out.append(f"    piton_raise_set(\"TypeError\", {_str_c_literal(message)});")
        out.append("    piton_report_unhandled();")
        out.append("    piton_exit(1);")

    def _runtime_name_error(self, out: list[str], name: str) -> None:
        out.append(f"    piton_raise_set(\"NameError\", {_str_c_literal('name ' + name + ' is not defined')});")
        out.append("    piton_report_unhandled();")
        out.append("    piton_exit(1);")

    def _emit_exc_check(self, out: list[str], function: MIRFunction, handler_label: Any) -> None:
        """Route a live native exception (piton_raise_set from a helper) to
        the enclosing try handler. With no handler in THIS function, RETURN
        with the flag set so the CALLER routes it (multi-frame propagation);
        only the module top level (and generators/coroutines, which cannot
        propagate across their driver) report and exit terminally.

        MULTI_EXCEPT_V1: `handler_label` is normally a single label; when the
        enclosing try has several `excepto` clauses it is a list of
        (accepted, label), and we emit a static type-dispatch on the live
        `piton_exc_type`: matching `excepto X:` catches X-and-subtypes per
        the exception's chain (as CPython), and the fallback mirrors the
        single-handler semantics."""
        # MULTI_EXCEPT_V1: a multi-except enclosing try emits a list of
        # (accepted, label). Route the LIVE exception by its type:
        # if(piton_exc_flag){
        #   if(exc_type matches accepted1) goto h1;
        #   ...
        #   if(acceptedN is catch-all) goto hN;
        #   <propagate-unhandled, since none of this try's clauses match>
        # }
        if isinstance(handler_label, list):
            out.append("    if(piton_exc_flag){")
            specific: list[tuple[str | None, str]] = []
            catch_all: tuple[str, str] | None = None
            for accepted, label in handler_label:
                if accepted is None or accepted in {"Exception", "BaseException"}:
                    catch_all = (accepted, label)
                else:
                    specific.append((accepted, label))
            for accepted, label in specific:
                out.append(
                    f"        if(!piton_strcmp(piton_exc_type, {json.dumps(accepted)})) "
                    f"goto {_name(function.name + '_' + label)};"
                )
            if catch_all is not None:
                out.append(f"        goto {_name(function.name + '_' + catch_all[1])};")
            else:
                _raise_or_propagate(out, function)  # flag stays set
            out.append("    }")
            return
        out.append("    if(piton_exc_flag){")
        if handler_label:
            out.append(f"        goto {_name(function.name + '_' + handler_label)};")
        elif (
            function.name != "<module>"
            and not getattr(function, "is_generator", False)
            and not getattr(function, "is_coroutine", False)
            and not getattr(function, "is_async_generator", False)
        ):
            out.append("    return 0;")
        else:
            out.append("        piton_report_unhandled();piton_exit(1);")
        out.append("    }")

    def _emit_contains(self, out: list[str], result: Any, left: Any, right: Any,
                       types: dict[str, str], negate: bool,
                       function: Any = None, handler_label: Any = None) -> None:
        """CONTAINS_V1: CPython membership (`in` / `no en`).

        The haystack dispatches statically; the needle becomes a PitonSlot so
        elements compare by value (slot_eq: int bits, str strcmp). Needles of
        any other type fail closed — value equality for floats (0.0 vs -0.0),
        bigints and objects needs either a tagged runtime or __eq__ dispatch
        (follow-up), and comparing raw bits would be silent approximation.
        """
        needle_type = types.get(left, "int")
        haystack_type = types.get(right, "int")
        if haystack_type is not None and haystack_type.startswith("object:"):
            # CONTAINS_DUNDER_V1: `x en obj` despacha a __contains__ por MRO.
            # Antos caía al fallback de seq y leía un PitonObject* como
            # PitonSeq* -> SIGSEGV.
            cls_name = haystack_type.split(":", 1)[1]
            hit = None
            for candidate in self.class_mro.get(cls_name, [cls_name]):
                if "__contains__" in self.classes.get(candidate, set()):
                    hit = candidate
                    break
            if hit is None and "__contains__" in self.classes.get(cls_name, set()):
                hit = cls_name
            if hit is None:
                raise NativeBuildError(
                    f"Linux native 'in' on a '{cls_name}' instance requires __contains__"
                )
            target = _name(hit + "____contains__")
            neg = "!" if negate else ""
            if self.function_frame_abi.get(hit + "____contains__", False):
                out.append(f'    {{long _ct_args[]={{ {self._value(right)},{self._value(left)} }}; {_name(result)}={neg}piton_frame_call((long)&{target},2,_ct_args);}}')
            else:
                out.append(f"    {_name(result)}={neg}{target}({self._value(right)},{self._value(left)});")
            types[result] = "bool"
            return out
        # CONTAINS_OBJECT_NEEDLE_V1: un needle OBJETO (una clase, por
        # ejemplo) se compara por IDENTIDAD (los bits del puntero) — que es
        # lo que hace CPython para `base in (object, type)`.
        if (
            haystack_type != "str"
            and needle_type not in {"int", "bool", "str", "bytes", "bytearray", "complex",
                                    "tuple", "list", "none", "slot"}
            and not needle_type.startswith(("object:", "class:"))
        ):
            # IN_STR_DYNAMIC_V1: con haystack str el needle lo decide el
            # runtime (piton_str_contains_dynamic), asi que el guard no
            # aplica; para el resto de haystacks sigue fail-closed.
            raise NativeBuildError(
                f"Linux 'in' requires an int/bool/str/bytes/bytearray/complex/"
                f"tuple/list/object needle, not {needle_type}"
            )
        if haystack_type in {"bytes", "bytearray"}:
            expression = f"piton_bytes_contains((void*){self._value(right)},{self._needle_slot(left, types)})"
            prefix = "!" if negate else ""
            out.append(f"    {_name(result)}={prefix}{expression};")
            if function is not None:
                self._emit_exc_check(out, function, handler_label)
            else:
                out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            types[result] = "bool"
            return
        if haystack_type == "str":
            if needle_type != "str":
                # IN_STR_DYNAMIC_V1: el needle que no es str estatico no es
                # un fallo de build: en CPython es un TypeError de runtime
                # (`5 in 'ab'`) o un str que la inferencia no vio (netrc).
                _h_op = self._value(right)
                _n_op = self._value(left)
                expression = f"piton_str_contains_dynamic((const char*){_h_op},{_n_op})"
                prefix = "!" if negate else ""
                out.append(f"    {_name(result)}={prefix}{expression};")
                if function is not None:
                    self._emit_exc_check(out, function, handler_label)
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
                types[result] = "bool"
                return
            expression = f"piton_str_contains((const char*){self._value(right)},(const char*){self._value(left)})"
        elif haystack_type in {"list", "tuple"}:
            expression = f"piton_seq_contains((PitonSeq*){self._value(right)},{self._needle_slot(left, types)})"
        elif haystack_type in {"dict", "dict:module"}:
            expression = f"piton_dict_contains((PitonDict*){self._value(right)},{self._needle_slot(left, types)})"
        elif haystack_type == "set":
            expression = f"piton_set_contains((PitonSet*){self._value(right)},{self._needle_slot(left, types)})"
        elif haystack_type == "range":
            # RANGE_VALUE_V1: CPython answers False for a non-int needle
            # (`'a' in range(3)`) instead of raising, so this is a static False
            # rather than an error.
            if needle_type not in {"int", "bool"}:
                out.append(f"    {_name(result)}={'!' if negate else ''}0;")
                types[result] = "bool"
                return
            expression = f"piton_range_contains((PitonRange*){self._value(right)},{self._value(left)})"
        else:
            if needle_type == "str":
                expression = f"piton_dict_contains((PitonDict*){self._value(right)},{self._needle_slot(left, types)})"
            else:
                expression = f"piton_seq_contains((PitonSeq*){self._value(right)},{self._needle_slot(left, types)})"
        prefix = "!" if negate else ""
        out.append(f"    {_name(result)}={prefix}{expression};")
        types[result] = "bool"

    def _resolve_method(self, class_name: str, method: str) -> str:
        for candidate in self.class_mro.get(class_name, []):
            if method in self.classes.get(candidate, set()):
                return candidate
        current = class_name
        while current:
            if method in self.classes.get(current, set()):
                return current
            current = self.class_parents.get(current)
        raise NativeBuildError(f"native method not found: {class_name}.{method}")

    def _resolve_property_class(self, owner_type: str, name: str) -> str | None:
        if not owner_type.startswith("object:"):
            return None
        class_name = owner_type.split(":", 1)[1]
        for candidate in self.class_mro.get(class_name, []):
            if name in self.class_properties.get(candidate, {}):
                return candidate
        return None

    def _type_match_c_expr(self, obj_val: str, target_name: str, types: dict, aliases: dict) -> str:
        target_clean = aliases.get(target_name, target_name)
        obj_t = types.get(obj_val)
        if obj_t is not None and obj_t != "int":
            slot = self._slot(obj_val, types)
            if target_clean in {"int", "entero"}:
                return f"({slot}.kind==PK_INT||{slot}.kind==PK_BOOL)"
            elif target_clean in {"bool", "booleano"}:
                return f"({slot}.kind==PK_BOOL)"
            elif target_clean in {"float", "decimal", "flotante"}:
                return f"({slot}.kind==PK_FLOAT)"
            elif target_clean in {"complex", "complejo"}:
                return f"({slot}.kind==PK_COMPLEX)"
            elif target_clean in {"str", "texto"}:
                return f"({slot}.kind==PK_STR)"
            elif target_clean in {"bytes", "octetos"}:
                return f"({slot}.kind==PK_BYTES)"
            elif target_clean in {"bytearray", "arreglo_bytes"}:
                return f"({slot}.kind==PK_BYTEARRAY)"
            elif target_clean in {"list", "lista"}:
                return f"({slot}.kind==PK_LIST)"
            elif target_clean in {"tuple", "tupla"}:
                return f"({slot}.kind==PK_TUPLE)"
            elif target_clean in {"dict", "diccionario"}:
                return f"({slot}.kind==PK_DICT)"
            elif target_clean in {"set", "conjunto"}:
                return f"({slot}.kind==PK_SET)"
            elif target_clean in {"range", "rango"}:
                return f"({slot}.kind==PK_RANGE)"
            elif target_clean in {"object", "objeto"}:
                return "1"
        else:
            kind_map = {
                "int": "PK_INT", "entero": "PK_INT",
                "bool": "PK_BOOL", "booleano": "PK_BOOL",
                "str": "PK_STR", "texto": "PK_STR",
                "bytes": "PK_BYTES", "octetos": "PK_BYTES",
                "bytearray": "PK_BYTEARRAY", "arreglo_bytes": "PK_BYTEARRAY",
                "list": "PK_LIST", "lista": "PK_LIST",
                "tuple": "PK_TUPLE", "tupla": "PK_TUPLE",
                "dict": "PK_DICT", "diccionario": "PK_DICT",
                "set": "PK_SET", "conjunto": "PK_SET",
                "range": "PK_RANGE", "rango": "PK_RANGE",
                "complex": "PK_COMPLEX", "complejo": "PK_COMPLEX",
            }
            if target_clean in kind_map:
                return f"piton_isinstance_dynamic({self._value(obj_val)},{kind_map[target_clean]})"
            elif target_clean in {"object", "objeto"}:
                return "1"
        if target_clean in self.classes:
            obj_t_str = types.get(obj_val, "")
            if obj_t_str.startswith("object:"):
                cname = obj_t_str.split(":", 1)[1]
                mro = self.class_mro.get(cname, [cname])
                if target_clean in mro:
                    return "1"
                else:
                    return "0"
            return f'piton_isinstance_class({self._value(obj_val)},"{target_clean}")'
        return "0"

    def _print_end_expr(self, print_end: Any, types: dict) -> str:
        """PRINT_END_KW_V1: el terminador de `print(..., end=X)`.

        Sin `end=`, es "\\n" (CPython). Con `end=`, es el valor dado — que
        debe ser un str (el backend no tiene objetos de archivo, asi que
        `end=None` (CPython lo trata como "\\n") tampoco se modela: es
        fail-closed.
        """
        if print_end is None:
            return '    piton_write(piton_print_fd,"\\n",1);'
        end_type = types.get(print_end, "")
        if end_type != "str":
            raise NativeBuildError(
                f"Linux print(end=) requires a str, not {end_type or 'unknown'}"
            )
        return f'    piton_write(piton_print_fd,(const char*){self._value(print_end)},piton_strlen((const char*){self._value(print_end)}));'

    def _build_isinstance_expr(self, obj_val: str, target_arg: str, types: dict, aliases: dict) -> str:
        t_items = self._tuple_elems.get(target_arg) or self._tuple_elems.get(aliases.get(target_arg, target_arg))
        if t_items is not None:
            sub_exprs = []
            for item in t_items:
                iname = item[0] if isinstance(item, tuple) else item
                sub_exprs.append(self._build_isinstance_expr(obj_val, iname, types, aliases))
            if sub_exprs:
                return f"({' || '.join(sub_exprs)})"
            return "0"
        target_name = aliases.get(target_arg, target_arg)
        # ISINSTANCE_BUILTIN_CLASS_V1: el target puede ser una clase builtin
        # de PROTOCOLO (os.PathLike -> tipo "class:PathLike"), que no existe
        # como tipo por nombre. En ese caso el match es por nombre de clase
        # del objeto, igual que una clase de usuario. CPython:
        # isinstance('x', os.PathLike) es False (str no implementa
        # __fspath__), y un PathLike real si lo seria.
        #
        # OJO: el prefijo "class:" lo usan TAMBIEN las clases de usuario, las
        # excepciones builtin y las clases builtin estandar (dict/list/
        # object/...). Esos casos ya los resuelve _type_match_c_expr por su
        # nombre (MRO para clases de usuario, kind_map para las estandar) y
        # este camino NO debe capturarlos.
        _target_type = types.get(target_arg) or types.get(target_name) or ""
        if _target_type.startswith("class:"):
            _cls_name = _target_type.split(":", 1)[1]
            if (
                _cls_name not in self.classes
                and _cls_name not in _STANDARD_BUILTIN_CLASSES
                and _cls_name not in _BUILTIN_EXCEPTIONS
            ):
                # Solo se consulta la clase del objeto si el backend sabe que
                # el valor es un objeto: un int crudo no es un puntero y
                # castearlo seria UB (SIGSEGV). Un str/int no es un PathLike.
                _obj_t = types.get(obj_val, "")
                if _obj_t.startswith("object:"):
                    _cname = _obj_t.split(":", 1)[1]
                    _mro = self.class_mro.get(_cname, [_cname])
                    return "1" if _cls_name in _mro else "0"
                return "0"
        return self._type_match_c_expr(obj_val, target_name, types, aliases)

    def _build_issubclass_expr(self, cls1_arg: str, cls2_arg: str, aliases: dict) -> str:
        c1 = aliases.get(cls1_arg, cls1_arg)
        t_items = self._tuple_elems.get(cls2_arg) or self._tuple_elems.get(aliases.get(cls2_arg, cls2_arg))
        if t_items is not None:
            sub_exprs = []
            for item in t_items:
                iname = item[0] if isinstance(item, tuple) else item
                sub_exprs.append(self._build_issubclass_expr(cls1_arg, iname, aliases))
            if sub_exprs:
                return f"({' || '.join(sub_exprs)})"
            return "0"
        c2 = aliases.get(cls2_arg, cls2_arg)
        type_norm = {
            "int": "int", "entero": "int",
            "bool": "bool", "booleano": "bool",
            "float": "float", "flotante": "float", "decimal": "float",
            "str": "str", "texto": "str",
            "bytes": "bytes", "octetos": "bytes",
            "bytearray": "bytearray", "arreglo_bytes": "bytearray",
            "list": "list", "lista": "list",
            "tuple": "tuple", "tupla": "tuple",
            "dict": "dict", "diccionario": "dict",
            "set": "set", "conjunto": "set",
            "range": "range", "rango": "range",
            "object": "object", "objeto": "object",
        }
        c1_norm = type_norm.get(c1, c1)
        c2_norm = type_norm.get(c2, c2)
        if c2_norm == "object" or c1_norm == c2_norm:
            return "1"
        if c1_norm == "bool" and c2_norm == "int":
            return "1"
        if c1 in self.classes and c2 in self.classes:
            mro = self.class_mro.get(c1, [c1])
            if c2 in mro:
                return "1"
            return "0"
        return "0"

    def _complete_call_args(self, function_name: str, values: list[Any]) -> list[Any]:
        # DEFAULT_NONE_V1: un parametro cuyo default es None se rellena con None
        # (el default mas comun: `def __init__(self, file=None)`). Antes se
        # cortaba el bucle y la llamada se emitia con un argumento menos, lo que
        # ni siquiera compilaba ("too few arguments to function ..."). El
        # centinela de "no hay mas info" es la lista vacia, no un None.
        defaults = self.function_defaults.get(function_name)
        if not defaults:
            return values
        while len(values) < len(defaults):
            default_value = defaults[len(values)]
            # NONE_SINGLETON_V2: el argumento omitido viaja como la direccion
            # del singleton. `b es Nada` lo reconoce por identidad sin
            # depender del kind del parametro, y imprimirlo muestra None.
            values.append(default_value)
        return values

    def _emit_linux_gen_suspend(
        self, out: list[str], types: dict[str, str],
        value: Any, result: Optional[str], await_flag: bool,
    ) -> None:
        """Emit one suspension point for a generator / coroutine / async-generator
        body (Linux freestanding backend). Writes the async-generator await marker
        into reserved slot 63 (1 = coroutine to run — esperar; 0 = plain data —
        producir), stores the resume id, returns the yielded value and emits the
        resume label that reloads sent_value into the yield's result slot."""
        for slot, index in sorted(self._gen_layout.items(), key=lambda item: item[1]):
            out.append(f"    piton_gen->slots[{index}]={_name(slot)};")
        resume_id = self._gen_counter + 1
        self._gen_counter += 1
        resume = self._gen_resumes[resume_id - 1] if resume_id - 1 < len(self._gen_resumes) else _name(f"{self._gen_function_name}_genresume_{resume_id}")
        out.append(f"    piton_gen->slots[63]={1 if await_flag else 0};")
        out.append(f"    piton_gen->state={resume_id};")
        out.append(f"    return {self._value(value)};")
        out.append(f"    {resume}:;")
        # Load sent_value from generator struct into yield result slot
        out.append(f"    __sent = piton_gen->sent_value;")
        out.append(f"    piton_gen->sent_value = 0;")
        if result:
            out.append(f"    {_name(result)} = __sent;")
            awaited_type = types.get(value, "int") if isinstance(value, str) else "int"
            if awaited_type == "gather":
                # TASK_SCHEDULER_V1: awaiting a gather yields a real list.
                awaited_type = "list"
            if str(awaited_type).startswith("iterator:") or awaited_type in {"generator", "genexpr"}:
                # RETURNTYPE_V1 regression guard: the awaited operand is a
                # coroutine/generator OBJECT; `esperar` yields its return
                # value, whose static type is not tracked here. Fall back to
                # the historical default instead of propagating the
                # iterator marker into the print lowering (which now fails
                # closed on such markers).
                awaited_type = "int"
            types[result] = awaited_type

    _UNKNOWN = "\x00?"

    def _uses_float_mod(self, module: MIRModule) -> bool:
        """FLOAT_MOD_V1: the software fmod lives in the bigint prelude
        section (it reuses the bigint mod), so any float % in the program
        must pull that section in."""
        for function in module.functions:
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.op == "binary" and instruction.args and instruction.args[0] == "%":
                        return True
        return False

    def _const_fold_overflows(self, module: MIRModule) -> bool:
        """INTOVF_GUARD_V1: does any constant int ``+ - *`` fold exceed i64?

        Must mirror the binary lowering's fold conditions exactly: a promoted
        fold result emits ``piton_bigint_from_str``, and the bigint runtime
        prelude is only included when this (or a huge literal) demands it —
        the prelude choice is made before function emission, so it needs this
        pre-scan instead of a lazy flag.
        """
        for function in module.functions:
            consts: dict[str, Any] = {}
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.op == "binary" and instruction.args and instruction.args[0] == "**":
                        # INT_POW_V1: the runtime ** path always emits bigint
                        # code (pow_small/print/free), so its mere presence
                        # needs the bigint prelude — independently of folds.
                        return True
                    if instruction.op == "const" and instruction.result and instruction.args:
                        consts[instruction.result] = instruction.args[0]
                    elif instruction.op == "unary" and instruction.result and len(instruction.args) >= 2 and instruction.args[0] in {"+", "-"}:
                        operand_const = consts.get(instruction.args[1]) if isinstance(instruction.args[1], str) else None
                        if isinstance(operand_const, int) and not isinstance(operand_const, bool):
                            consts[instruction.result] = operand_const if instruction.args[0] == "+" else -operand_const
                    elif instruction.op == "binary" and len(instruction.args) >= 3:
                        operator = instruction.args[0]
                        if operator not in {"+", "-", "*", "**"}:
                            continue
                        left_const = consts.get(instruction.args[1]) if isinstance(instruction.args[1], str) else None
                        right_const = consts.get(instruction.args[2]) if isinstance(instruction.args[2], str) else None
                        if (
                            isinstance(left_const, int) and not isinstance(left_const, bool)
                            and isinstance(right_const, int) and not isinstance(right_const, bool)
                        ):
                            if operator == "**" and not (0 <= right_const <= 1000000):
                                continue
                            try:
                                # NOTE: never dispatch through a dict literal
                                # here: it would evaluate a ** b for EVERY
                                # binary op (a 3037000499 ** 3037000499-shaped
                                # hang). Lazy branches only.
                                if operator == "+":
                                    folded = left_const + right_const
                                elif operator == "-":
                                    folded = left_const - right_const
                                elif operator == "*":
                                    folded = left_const * right_const
                                else:
                                    folded = left_const ** right_const
                            except ZeroDivisionError:
                                continue
                            # mirror the lowering: folded results are recorded
                            # so chained folds keep propagating
                            if instruction.result:
                                consts[instruction.result] = folded
                            if not (-(2 ** 63) <= folded < 2 ** 63):
                                return True
        return False

    def _scan_module_globals(self, module: MIRModule) -> None:
        """GLOBAL_DECL_V1: file-scope shared globals are the module-stored
        names declared global in at least one function. Also records, per
        function, the locally stored names (shadowing legitimately wins
        over the module binding, like CPython) and the module-level value
        types for constant initializers."""
        self._func_globals = {}
        self._deleted_names = set()
        self._deleted_by_function: dict[str, set[str]] = {}
        # DEL_ORDER_V1: (funcion, nombre) -> secuencia de instruccion del
        # `borrar`. El NameError estatico solo aplica a los loads POSTERIORES
        # al delete (hashlib: `del __get_hash` al final del modulo, con el
        # load antes).
        self._deleted_at: dict[tuple[str, str], tuple[int, int]] = {}
        self._instr_seq = 0
        self._cur_block = 0
        self._func_stores = {}
        self._module_stored = set()
        self._module_types = {}

        def literal_type(value: Any) -> str | None:
            if value is None:
                return "none"
            if isinstance(value, bool):
                return "bool"
            if isinstance(value, str):
                return "str"
            if isinstance(value, bytes):
                return "bytes"
            if isinstance(value, bytearray):
                return "bytearray"
            if isinstance(value, float):
                return "float"
            if isinstance(value, int):
                return "bigint" if abs(value) > 9223372036854775807 else "int"
            return None

        for function in module.functions:
            declared: set[str] = set()
            stored: set[str] = set()
            local_consts: dict[str, Any] = {}
            module_temps: set[str] = set()
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.op == "global_decl" and instruction.args:
                        declared.add(instruction.args[0])
                    elif (
                        instruction.op == "object_new"
                        and instruction.result
                        and instruction.args
                        and instruction.args[0] == "module"
                    ):
                        # module alias bindings (`importar b` stores the fresh
                        # module object) are not user variables: skipping them
                        # keeps import machinery (often dead loads consumed
                        # through qualified names) out of the global checks.
                        module_temps.add(instruction.result)
                    elif instruction.op == "store" and instruction.args:
                        stored.add(instruction.args[0])
                        if function.name == "<module>":
                            target, source = instruction.args[0], instruction.args[1]
                            if target.startswith("__") and target.endswith("__"):
                                continue
                            if isinstance(source, str) and source in module_temps:
                                continue
                            self._module_stored.add(target)
                            if isinstance(source, str) and source in local_consts:
                                kind = local_consts[source]
                                if kind:
                                    self._module_types[target] = kind
                    elif instruction.op == "const" and instruction.result and instruction.args:
                        local_consts[instruction.result] = literal_type(instruction.args[0])
                    elif instruction.op == "build_collection" and instruction.result and instruction.args:
                        if instruction.args[0] in {"list", "tuple", "dict", "set"}:
                            local_consts[instruction.result] = instruction.args[0]
                    elif instruction.op == "object_new" and instruction.result and instruction.args:
                        local_consts[instruction.result] = f"object:{instruction.args[0]}"
            if declared:
                self._func_globals[function.name] = declared
            if stored:
                self._func_stores[function.name] = stored
        declared_anywhere: set[str] = set()
        for names in self._func_globals.values():
            declared_anywhere |= names
        # MODULE_GLOBAL_READ_V1: CPython lee el binding del modulo cuando la
        # funcion no lo asigna localmente ni declara `global` (solo la
        # escritura es local sin declaracion). Esos nombres se promueven a
        # static como los declarados, para que el load del op alcance la
        # variable file-scope (opcode/stat: constantes de modulo leidas en
        # funciones).
        read_anywhere: set[str] = set()
        for function in module.functions:
            if function.name == "<module>":
                continue
            func_params = set(function.params or ())
            func_stored = self._func_stores.get(function.name, ())
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.op == "load" and instruction.args and isinstance(instruction.args[0], str):
                        name = instruction.args[0]
                        if (
                            name in self._module_stored
                            and name not in func_params
                            and name not in func_stored
                        ):
                            read_anywhere.add(name)
        self._shared_globals = self._module_stored & (declared_anywhere | read_anywhere)

    def _scan_usage_param_types(self, module: MIRModule) -> None:
        """USAGE_PARAM_TYPE_V1: pre-pass — si TODOS los usos de un param como
        receiver de method_call son metodos exclusivos de str (y ninguno
        contradice collections/objects), el param se tipa str (nturl2path:
        url.replace; getopt: args.startswith). Otros usos (truthy, len,
        comparaciones, indexado) no contradicen: son legales sobre str. Se
        computa antes de emitir porque el call de <module> a una funcion
        posterior necesita el seed para el check fail-closed del call-site.
        """
        for function in module.functions:
            if function.name == "<module>" or not function.params:
                continue
            _param_set = set(function.params)
            _loads_of: dict[str, set[str]] = {}
            _all_load_src: dict[str, str] = {}
            for block in function.blocks:
                for instruction in block.instructions:
                    if (
                        instruction.op == "load" and instruction.args
                        and isinstance(instruction.args[0], str)
                    ):
                        if instruction.result:
                            _all_load_src[instruction.result] = instruction.args[0]
                        if instruction.args[0] in _param_set and instruction.result:
                            _loads_of.setdefault(instruction.args[0], set()).add(instruction.result)
            # STR_ARG_EVIDENCE_V1: un param usado como ARGUMENTO de un metodo
            # exclusivo de str (getopt: longopt.startswith(opt)) es un str:
            # esos metodos exigen un argumento str.
            _str_arg_params: set[str] = set()
            # RUNTIME_TYPE_PARAMS_V1: el primer argumento de isinstance /
            # issubclass decide el tipo EN RUNTIME.
            _runtime_type_params: set[str] = set()
            for _block in function.blocks:
                for _ins in _block.instructions:
                    if (
                        _ins.op == "method_call"
                        and len(_ins.args) >= 4
                        and _ins.args[1] in _USAGE_STR_METHODS
                    ):
                        for _a in _ins.args[3]:
                            _src = _all_load_src.get(_a)
                            if _src in _param_set:
                                _str_arg_params.add(_src)
                    elif (
                        _ins.op == "call"
                        and len(_ins.args) >= 2
                        and isinstance(_ins.args[1], tuple)
                        and _ins.args[1]
                        and (
                            _ins.args[0] in {"isinstance", "es_instancia", "issubclass", "es_subclase"}
                            if isinstance(_ins.args[0], str)
                            else _all_load_src.get(_ins.args[0]) in {
                                "isinstance", "es_instancia", "issubclass", "es_subclase"
                            }
                        )
                    ):
                        _src = _all_load_src.get(_ins.args[1][0])
                        if _src in _param_set:
                            _runtime_type_params.add(_src)
            if _runtime_type_params:
                self._runtime_type_params[function.name] = _runtime_type_params
            for _uparam, _uloads in _loads_of.items():
                # VARARG_KWARG_SEED_V1: `*args` es una tupla y `**kwargs` un
                # dict — el seed de secuencia (list) no aplica a esos params.
                if _uparam in {getattr(function, "vararg", None), getattr(function, "kwarg", None)}:
                    continue
                _has_str_evidence = False
                _contradicts = False
                _has_list_evidence = False
                _contradicts_list = False
                # CALL_ARG_TYPE_V1: el param se usa como SECUENCIA (indice,
                # slice, receiver de metodo o len) — el tipo del call-site
                # puede darle el tipo real (getopt: do_longs(opt) recibe un
                # str y `opt.index('=')` no tiene seed local).
                self._param_seq_use.setdefault(function.name, set())
                for block in function.blocks:
                    for instruction in block.instructions:
                        if (
                            instruction.op == "method_call"
                            and len(instruction.args) >= 3
                            and instruction.args[2] in _uloads
                        ):
                            self._param_seq_use[function.name].add(_uparam)
                        elif (
                            instruction.op in {"get_item", "get_slice"}
                            and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            self._param_seq_use[function.name].add(_uparam)
                        elif instruction.op == "compare" and len(instruction.args) >= 3:
                            # un param comparado necesita tipo conocido (la
                            # comparacion exige tipos ordenables/iguales).
                            for _side in instruction.args[1:3]:
                                if _side in _uloads:
                                    self._param_seq_use[function.name].add(_uparam)
                # SEQ_USAGE_SEED_V1: len() + get_item sobre el param sin
                # metodo contradictorio (bisect_right: len(a), a[mid] — el
                # param no tiene method_call, asi que el seed de mutadores
                # no lo alcanza). Best-effort: str/tuple comparten estas
                # operaciones, pero el subset asume seq-lineal y el
                # call-site rechaza args de tipo conocido contradictorio.
                _uses_len = False
                _uses_get_item = False
                _uses_get_slice = False
                _get_item_results: set[str] = set()
                for block in function.blocks:
                    for instruction in block.instructions:
                        if (
                            instruction.op == "method_call"
                            and len(instruction.args) >= 3
                            and instruction.args[2] in _uloads
                        ):
                            _umethod = instruction.args[1]
                            if _umethod in _USAGE_NONSTR_METHODS:
                                _contradicts = True
                            elif _umethod in _USAGE_STR_METHODS:
                                _has_str_evidence = True
                            # USAGE_LIST_SEED_V1: evidencia/contradiccion
                            # independiente del juicio str (insert es
                            # NONSTR y a la vez LIST).
                            if _umethod in _USAGE_LIST_METHODS:
                                _has_list_evidence = True
                            elif _umethod in _USAGE_NONLIST_METHODS:
                                _contradicts_list = True
                        elif (
                            instruction.op == "get_item" and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            _uses_get_item = True
                            _get_item_results.add(instruction.result)
                        elif (
                            instruction.op == "subscript_store" and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            # SUBSCRIPT_STORE_SEED_V1: `param[i] = v` es
                            # evidencia INEQUIVOCA de secuencia mutable
                            # (list): un str no admite asignacion por
                            # indice (heapq: heappop(heap) -> heap[0] = x).
                            _has_list_evidence = True
                            _contradicts = True
                        elif (
                            instruction.op == "iter_new" and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            # ITER_SEED_V1: un param que se ITERA es una
                            # secuencia (list/tuple): el `para x en param` de un
                            # modulo empaquetado (functools.reduce) exige un
                            # tipo conocido en el receptor. Misma ambiguedad
                            # str/list que la regla len+get_item.
                            _has_list_evidence = True
                        elif (
                            instruction.op == "get_slice" and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            _uses_get_slice = True
                        elif (
                            instruction.op == "call" and len(instruction.args) >= 2
                            and isinstance(instruction.args[1], tuple)
                            and instruction.args[1]
                            and _all_load_src.get(instruction.args[0]) in {"len", "longitud"}
                            and instruction.args[1][0] in _uloads
                        ):
                            # LEN_CALL_V1: `longitud(x)` (PITON) y `len(x)`
                            # son el mismo builtin — sin el nombre espanol la
                            # evidencia de secuencia no se detectaba.
                            _uses_len = True
                        elif (
                            instruction.op == "collection_len" and instruction.args
                            and instruction.args[0] in _uloads
                        ):
                            # LEN_COLL_V1: `len(lista)` se lowers como
                            # collection_len (no como call a len) — es la
                            # misma evidencia de secuencia.
                            _uses_len = True
                if _has_str_evidence and not _contradicts:
                    self._usage_param_types[function.name][_uparam] = "str"
                elif _uparam in _str_arg_params and not _contradicts_list:
                    # STR_ARG_EVIDENCE_V1: el param se pasa como argumento de
                    # un metodo str -> es un str (getopt: long_has_args(opt)).
                    self._usage_param_types[function.name][_uparam] = "str"
                elif _has_list_evidence and not _contradicts_list:
                    # bisect: a.insert(lo, x) — el param mutador se tipa
                    # list; el call-site rechaza args conocidos no-list.
                    self._usage_param_types[function.name][_uparam] = "list"
                elif (
                    not _contradicts_list
                    and not _has_str_evidence
                    and not _contradicts
                    and ((_uses_len and _uses_get_item) or (_uses_get_slice and _uses_get_item))
                ):
                    # SEQ_USAGE_SEED_V1: indexado/sliceado del param sin
                    # metodos str (bisect: len(a), a[mid]; getopt:
                    # args[0], args[1:]) -> el param es una secuencia lineal,
                    # se asume list.
                    self._usage_param_types[function.name][_uparam] = "list"
                    # AMBIG_SEED_V1: SOLO el seed por indice+SLICE es ambiguo
                    # (slice es legal en str y en list; el call-site lo
                    # reconcilia con el tipo real del argumento). El seed por
                    # len+indice conserva el contrato fail-closed del
                    # call-site (un argumento str a un param seq se rechaza).
                    self._usage_param_weak[function.name][_uparam] = bool(
                        _uses_get_slice and not _uses_len
                    )
                # ELEM_KIND_V1: para un param list, deducir el tipo de
                # sus elementos por el uso de `param[i]` (get_item) como
                # receiver de metodos str; getopt: args[0].startswith('-')
                # -> elem str. Sin esto args[0] queda int y los slices
                # / comparaciones de sus elementos fallan cerrado.
                _up_type = self._usage_param_types.get(function.name, {}).get(_uparam)
                if _up_type == "list":
                    # ELEM_KIND_V1: el tipo del elemento se deduce del uso de
                    # `param[i]`: receiver de metodo str, directo o a traves
                    # de un store (getopt: o = longopts[i]; o.startswith(opt)).
                    _stores_of: dict[str, set[str]] = {}
                    for _b in function.blocks:
                        for _i in _b.instructions:
                            if _i.op == "store" and len(_i.args) >= 2:
                                _stores_of.setdefault(_i.args[1], set()).add(_i.args[0])
                    _item_names: set[str] = set()
                    for _r in _get_item_results:
                        _item_names |= _stores_of.get(_r, set())
                    _uses_str_elem = any(
                        instruction.op == "method_call"
                        and len(instruction.args) >= 3
                        and instruction.args[1] in _USAGE_STR_METHODS
                        and instruction.args[1] not in _USAGE_NONSTR_METHODS
                        and (
                            instruction.args[2] in _get_item_results
                            or _all_load_src.get(instruction.args[2]) in _item_names
                        )
                        for block in function.blocks
                        for instruction in block.instructions
                    )
                    self._usage_param_elems[function.name][_uparam] = (
                        "str" if _uses_str_elem else None
                    )
                    if _uses_str_elem:
                        # evidencia de elem str: el param es una LIST de str
                        # (los chars de un str no se usan con metodos str).
                        self._usage_param_weak[function.name][_uparam] = False
        self._propagate_param_types(module)

    def _propagate_param_types(self, module: MIRModule) -> None:
        """PARAM_TYPE_PROPAGATION_V1: propaga el tipo CONFIRMADO del param de
        un callee al param del caller que se le pasa como argumento.

        Motivo: `heapify(heap)` solo hace `len(heap)` (evidencia ambigua
        str/list, el seed no tipa); su callee `_siftup(heap, i)` si tiene
        evidencia inequivoca (subscript store) -> heap es list. Sin
        propagacion, el `len(heap)` de heapify no compila.

        Solo rellena params SIN tipo y solo con tipos confirmados (nunca
        propaga un seed marcado weak). Fixpoint acotado (3 rondas): una
        cadena caller->callee puede tener varios saltos."""
        confirmed = {"list", "str", "tuple", "dict", "set"}
        for _round in range(3):
            changed = False
            for function in module.functions:
                params = set(getattr(function, "params", ()) or ()) - {
                    getattr(function, "vararg", None), getattr(function, "kwarg", None)
                }
                if not params:
                    continue
                load_src: dict[str, str] = {}
                calls: list[tuple[Any, tuple, tuple]] = []
                for block in function.blocks:
                    for instruction in block.instructions:
                        if (
                            instruction.op == "load" and instruction.args
                            and instruction.result
                        ):
                            load_src[instruction.result] = instruction.args[0]
                        elif (
                            instruction.op == "call" and instruction.result
                            and isinstance(instruction.args, tuple) and instruction.args
                        ):
                            calls.append((instruction.args[0], instruction.args[1], instruction.args))
                own_types = self._usage_param_types.setdefault(function.name, {})
                own_weak = self._usage_param_weak.setdefault(function.name, {})
                for callee_temp, values, raw_args in calls:
                    callee = load_src.get(callee_temp)
                    if callee is None and isinstance(raw_args[0], str):
                        callee = raw_args[0]
                    if not isinstance(callee, str) or not isinstance(values, tuple):
                        continue
                    callee_params = self.function_params.get(callee) or []
                    if not callee_params:
                        continue
                    callee_types = self._usage_param_types.get(callee, {})
                    callee_weak = self._usage_param_weak.get(callee, {})
                    for index, value in enumerate(values):
                        source = load_src.get(value)
                        if source is None or source not in params:
                            continue
                        if own_types.get(source) or own_weak.get(source):
                            continue
                        if index >= len(callee_params):
                            break
                        got = callee_types.get(callee_params[index])
                        if got in confirmed and not callee_weak.get(callee_params[index]):
                            own_types[source] = got
                            changed = True
            if not changed:
                break

    def _scan_identity_functions(self, module: MIRModule) -> None:
        """IDENTITY_FN_V1: detecta funciones que devuelven su param sin
        cambios y sin efectos (getopt: `def _(msg): return msg`, el stub de
        gettext). Su resultado ES el argumento recibido, asi que un `%` con
        ese resultado tiene una plantilla literal conocida.

        Solo se acepta un unico `return` cuyo valor es un load de un param, y
        el cuerpo no tiene store/call/method_call/raise/print (sin efectos).
        """
        for function in module.functions:
            if function.name == "<module>" or not function.params:
                continue
            returns: list[Any] = []
            has_effect = False
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.op == "return" and instruction.args:
                        returns.append(instruction.args[0])
                    elif instruction.op in {
                        "store", "set_attr", "call", "method_call",
                        "raise_typed", "print", "del",
                    }:
                        has_effect = True
            if len(returns) != 1 or has_effect:
                continue
            for block in function.blocks:
                for instruction in block.instructions:
                    if (
                        instruction.op == "load"
                        and instruction.result == returns[0]
                        and instruction.args
                        and instruction.args[0] in function.params
                    ):
                        self._identity_params[function.name] = function.params.index(
                            instruction.args[0]
                        )
                        break
                else:
                    continue
                break

    def _infer_return_types(self, module: MIRModule) -> dict[str, str]:
        """RETURNTYPE_V1: narrow, sound return-type inference over MIR.

        Only statically unambiguous origins count: constant literals,
        build_collection kinds, object_new classes, and direct calls to known
        functions (recursive, cycle-safe). Variables accumulate the union of
        every store origin (fixpoint). A function is typed only when EVERY
        return resolves to the SAME type; anything ambiguous stays ``"int"``,
        the historical default. Functions that only return ``Nada`` (or never
        return) infer ``"none"`` — that fixes ``imprimir(f())`` printing ``0``
        instead of ``None``. Generators/coroutines are marked
        ``"iterator:generator"``: their printed form can never match CPython,
        so the print lowering fails closed on that marker instead of printing
        a raw pointer as an int.
        """
        by_name = {function.name: function for function in module.functions}
        memo: dict[str, str | None] = {}
        resolving: set[str] = set()

        def literal_type(value: Any) -> str:
            if value is None:
                return "none"
            if isinstance(value, bool):
                return "bool"
            if isinstance(value, str):
                return "str"
            if isinstance(value, bytes):
                return "bytes"
            if isinstance(value, bytearray):
                return "bytearray"
            if isinstance(value, float):
                return "float"
            if isinstance(value, complex):
                return "complex"
            if isinstance(value, int):
                return "bigint" if abs(value) > 9223372036854775807 else "int"
            # M6 SEQ_LITERAL_V1: literales compuestos de consts (defaults).
            if isinstance(value, tuple):
                return "tuple"
            if isinstance(value, list):
                return "list"
            return self._UNKNOWN

        cycle_cache: dict[str, str | None] = {}

        def infer(name: str, _use_cache: bool = True) -> str | None:
            # MUTUAL_RECURSION_V1: `memo` cachea el resultado DEFINITIVO de una
            # función; `cycle_cache` guarda el valor provisional que se vio al
            # topar un ciclo (par llama a impar y viceversa: sin esto, la
            # primera pasada fijaba None y el fixpoint nunca avanzaba).
            if name in memo:
                return memo[name]
            if name in resolving:
                return cycle_cache.get(name)
            if _use_cache and name in cycle_cache and cycle_cache[name] is not None:
                return cycle_cache[name]
            # _use_cache=False NO consulta cycle_cache: fuerza el recálculo
            if name in {"complex", "complejo"}:
                # COMPLEX_V1: el constructor nativo devuelve complejo, así el
                # tipo de retorno de `devolver complejo(a, a)` se propaga.
                memo[name] = "complex"
                return memo[name]
            function = by_name.get(name)
            if function is None:
                return None
            if (
                getattr(function, "is_generator", False)
                or getattr(function, "is_coroutine", False)
                or getattr(function, "is_async_generator", False)
            ):
                memo[name] = "iterator:generator"
                return memo[name]
            resolving.add(name)
            try:
                var_sets: dict[str, set[str]] = {}
                origins: dict[str, tuple[str, Any]] = {}
                load_src: dict[str, str] = {}
                returns: list[Any] = []
                seeded: set[str] = set()

                def note_var(key: str, kind: str | None) -> bool:
                    kinds = {kind} if kind else {self._UNKNOWN}
                    if kinds <= var_sets.get(key, set()):
                        return False
                    var_sets.setdefault(key, set()).update(kinds)
                    return True

                def single(key: str) -> str | None:
                    kinds = var_sets.get(key, set())
                    if len(kinds) == 1 and self._UNKNOWN not in kinds:
                        return next(iter(kinds))
                    # BOOL_SLOT_RET_V1: un holder `@boolh_` con brazos de
                    # tipo DISTINTO es un slot ETIQUETADO en runtime
                    # (BOOL_SLOT_V1). Su tipo es "slot" para que el caller
                    # que hace `no f()` use la verdad por kind (hashlib:
                    # `not (a or b)` sobre brazos int/str).
                    if (
                        key.startswith("@boolh_")
                        and len(kinds) >= 2
                        and self._UNKNOWN not in kinds
                    ):
                        return "slot"
                    return None

                def operand_type(operand: Any) -> str | None:
                    if operand is None or operand == "None":
                        return "none"
                    if isinstance(operand, str) and not operand.startswith("%"):
                        return single(operand)
                    if isinstance(operand, str):
                        origin = origins.get(operand)
                        if origin is None:
                            return None
                        if origin[0] == "literal":
                            return literal_type(origin[1])
                        if origin[0] in {"kind", "object"}:
                            return origin[1] if origin[0] == "kind" else f"object:{origin[1]}"
                        if origin[0] == "var":
                            return single(origin[1])
                        if origin[0] == "call":
                            return infer(origin[1])
                        return None
                    return literal_type(operand)


                # RETBLOCK_REACHABLE_V1: el lowering SIEMPRE cierra con un
                # bloque `return None` aunque sea inalcanzable (curses/ascii.
                # _ctoi: las dos ramas retornan y aun asi hay un b3 huerfano
                # con `return None`). Contarlo tipaba la funcion "none" y el
                # caller veia un needle/valor none. Solo se descarta ese
                # bloque final si NINGUN jump/branch lo referencia (los bloques
                # de handler viven dentro del cuerpo y no son el ultimo).
                _referenced: set[str] = set()
                for _b in function.blocks:
                    for _i in _b.instructions:
                        if _i.op in {"jump", "branch"}:
                            for _t in _i.args:
                                if isinstance(_t, str):
                                    _referenced.add(_t)
                _dead_tail: set[str] = set()
                for _b in function.blocks:
                    # solo la forma SINTETICA exacta: `const None` + `return`
                    # y sin ninguna referencia. Un bloque con `return n` (el
                    # del bucle) NO es sintetico aunque no lo salte nadie: la
                    # caida natural lo alcanza.
                    _ins = _b.instructions
                    if _ins and _b.label not in _referenced and all(
                        _i.op in {"const", "return"} for _i in _ins
                    ) and _ins[-1].op == "return":
                        # dos formas del mismo bloque sintetico:
                        #   [return (None,)]                 (const plegada)
                        #   [const (None,), return ('%4',)]  (const como temp)
                        _ret_arg = _ins[-1].args[0] if _ins[-1].args else None
                        if _ret_arg is None or any(
                            _i.op == "const" and _i.args == (None,) and _i.result == _ret_arg
                            for _i in _ins
                        ):
                            _dead_tail.add(_b.label)

                changed = True
                while changed:
                    changed = False
                    for block in function.blocks:
                        for instruction in block.instructions:
                            op, iargs = instruction.op, instruction.args
                            result = instruction.result
                            if op == "const" and result and iargs:
                                origins[result] = ("literal", iargs[0])
                            elif op == "build_collection" and result:
                                origins[result] = ("kind", iargs[0])
                            elif op == "object_new" and result:
                                origins[result] = ("object", iargs[0])
                            elif op == "load" and result and iargs:
                                load_src[result] = iargs[0]
                                origins[result] = ("var", iargs[0])
                            elif op == "call" and result:
                                callee = load_src.get(iargs[0], iargs[0] if isinstance(iargs[0], str) else None)
                                if isinstance(callee, str) and callee not in function.params and (
                                    callee in by_name or callee in {"complex", "complejo"}
                                ):
                                    origins[result] = ("call", callee)
                                else:
                                    origins[result] = ("opaque", None)
                            elif op == "method_call" and result:
                                # METHODTYPE_V1: a method call resolves to the
                                # defining method (MRO) so its inferred return
                                # type flows to the caller. Without this,
                                # `B.f` returning `'B' + super().f()` inferred
                                # int, and the string result printed as an
                                # address. Unresolvable receivers stay opaque.
                                cls_name, method = iargs[0], iargs[1]
                                target = None
                                if isinstance(cls_name, str):
                                    try:
                                        hit = self._resolve_method(cls_name, method)
                                    except Exception:
                                        hit = None
                                    if hit is not None and f"{hit}__{method}" in by_name:
                                        target = f"{hit}__{method}"
                                    elif f"{cls_name}__{method}" in by_name:
                                        target = f"{cls_name}__{method}"
                                origins[result] = ("call", target) if target else ("opaque", None)
                            elif op == "binary" and result:
                                # METHODTYPE_V1: `'B' + <str>` must stay str.
                                # Only the unambiguous string-concat shape is
                                # typed; everything else keeps the historical
                                # int default.
                                _bop, _l, _r = iargs[0], iargs[1], iargs[2]
                                if _bop == "+" and operand_type(_l) == "str" and operand_type(_r) == "str":
                                    origins[result] = ("kind", "str")
                                else:
                                    origins[result] = ("opaque", None)
                            elif op == "store" and result is None and iargs:
                                target, source = iargs[0], iargs[1]
                                if isinstance(source, str) and source.startswith("%"):
                                    origin = origins.get(source)
                                    if origin is None:
                                        changed = note_var(target, None) or changed
                                    elif origin[0] == "literal":
                                        changed = note_var(target, literal_type(origin[1])) or changed
                                    elif origin[0] in {"kind", "object"}:
                                        changed = note_var(target, origin[1]) or changed
                                    elif origin[0] == "var":
                                        kinds = var_sets.get(origin[1], set())
                                        if not kinds:
                                            changed = note_var(target, None) or changed
                                        else:
                                            for kind in kinds:
                                                changed = note_var(target, None if kind == self._UNKNOWN else kind) or changed
                                    elif origin[0] == "call":
                                        changed = note_var(target, infer(origin[1])) or changed
                                    else:
                                        changed = note_var(target, None) or changed
                                elif isinstance(source, str):
                                    # plain name (variable reference, never a raw literal here)
                                    changed = note_var(target, single(source)) or changed
                                else:
                                    changed = note_var(target, literal_type(source)) or changed
                            elif op == "return":
                                if block.label not in _dead_tail:
                                    returns.append(iargs[0] if iargs else None)
                    if function.self_class and function.params and function.params[0] not in seeded:
                        seeded.add(function.params[0])
                        changed = note_var(function.params[0], f"object:{function.self_class}") or changed
                    if function.vararg and function.vararg not in seeded:
                        seeded.add(function.vararg)
                        changed = note_var(function.vararg, "tuple") or changed
                    if function.kwarg and function.kwarg not in seeded:
                        seeded.add(function.kwarg)
                        changed = note_var(function.kwarg, "dict") or changed
                    # M6 SEQ_PARAM_DEFAULT_V1: param con default collection
                    # se seedea con ese tipo; `devolver b` infiere tuple y
                    # el caller imprime la collection, no el puntero.
                    for _pd_param, _pd_value in zip(function.params, function.defaults or []):
                        if _pd_param in seeded:
                            continue
                        if isinstance(_pd_value, tuple):
                            seeded.add(_pd_param)
                            changed = note_var(_pd_param, "tuple") or changed
                        elif isinstance(_pd_value, list):
                            seeded.add(_pd_param)
                            changed = note_var(_pd_param, "list") or changed
                        elif isinstance(_pd_value, str):
                            seeded.add(_pd_param)
                            changed = note_var(_pd_param, "str") or changed
                    # MIR-REACH-V1 (RETURNS PARAM TYPE): param seedeado por
                    # uso (str/list) devuelve ese tipo al retornarlo directo
                    # (bisect insort_right: `a.insert(...)` muta y el caller
                    # de `devolver xs` imprime la collection, no el puntero).
                    for _up_param, _up_type in (self._usage_param_types.get(function.name) or {}).items():
                        if _up_param in seeded:
                            continue
                        seeded.add(_up_param)
                        changed = note_var(_up_param, _up_type) or changed

                if not returns:
                    inferred: str | None = "none"
                else:
                    candidates = {operand_type(operand) for operand in returns}
                    if len(candidates) == 1:
                        inferred = next(iter(candidates))
                    elif len(candidates) > 1 and None in candidates:
                        # MUTUAL_RECURSION_V1: un return sin resolver (llamada
                        # mutua en ciclo) no debe descartar el tipo de los
                        # returns SÍ resueltos: se propaga el único conocido.
                        known = {c for c in candidates if c is not None and c != self._UNKNOWN}
                        inferred = next(iter(known)) if len(known) == 1 else None
                    else:
                        inferred = None
                if inferred is None or inferred == self._UNKNOWN:
                    inferred = None
            finally:
                resolving.discard(name)
            memo[name] = inferred
            if inferred is not None:
                cycle_cache[name] = inferred
            return inferred

        # MUTUAL_RECURSION_V1: `par`/`impar` se llaman entre sí. La primera
        # pasada ve el ciclo y memo guarda None -> "int" (imprimía 1, no True).
        # FIXPOINT: se limpia memo y se reejecuta usando cycle_cache (que solo
        # guarda resultados no-None ya definitivos) hasta que nada cambie.
        for _round in range(len(module.functions) + 1):
            changed = False
            for function in module.functions:
                previous = memo.get(function.name)
                memo.pop(function.name, None)
                inferred = infer(function.name, _use_cache=False)
                if inferred != previous:
                    changed = True
            if not changed:
                break
        resolved: dict[str, str] = {}
        for function in module.functions:
            inferred = memo.get(function.name)
            resolved[function.name] = inferred if inferred else "int"
        return resolved

    def _emit_instruction(
        self, instruction: MIRInstruction, function: MIRFunction,
        aliases: dict[str, str], types: dict[str, str], bigint_slots: list[str],
    ) -> list[str]:
        op, args, result = instruction.op, instruction.args, instruction.result
        self._instr_seq += 1
        out: list[str] = []
        if op == "iter_new":
            source = args[0]
            source_type = types.get(source, "")
            if source_type in {"genexpr", "iterator:genexpr"}:
                out.append(f"    {_name(result)}=piton_genexpr_iter((PitonGenExpr*){self._value(source)});")
                types[result] = "iterator:genexpr"
                return out
            elif source_type.startswith("object:"):
                class_name = source_type.split(":", 1)[1]
                method_class = self._resolve_method(class_name, "__iter__")
                out.append(f"    {_name(result)}={_name(method_class+'__'+'__iter__')}({self._value(source)});")
                # ITER_DISPATCH_V1: si la clase NO tiene __next__, __iter__()
                # retorna un iterador independiente (ej: iter([1,2])),
                # no self. No marcar como iterator:object:P porque iter_next
                # llamaría a P____next__ que no existe.
                has_next = False
                for candidate in self.class_mro.get(class_name, []):
                    if "__next__" in self.classes.get(candidate, set()):
                        has_next = True
                        break
                if has_next:
                    types[result] = f"iterator:object:{class_name}"
                else:
                    types[result] = "iterator:any"
                return out
            elif source_type == "generator":
                out.append(f"    {_name(result)}={self._value(source)};")
                types[result] = "generator"
                return out
            elif source_type == "file":
                # FILE_ITER_V1: iterar un fichero da sus lineas restantes (como
                # CPython). Se materializa la lista de lineas y se envuelve en
                # el iterador generico, reusando todo el camino de `para`.
                out.append(
                    f"    {{PitonSeq*_fil=piton_seq_new(PK_LIST,0);"
                    f" while(piton_file_has_more((PitonFile*){self._value(source)}))"
                    f" {{piton_seq_append(_fil,(PitonSlot){{(long)piton_file_next_line((PitonFile*){self._value(source)}),PK_STR}});}}"
                    f" {_name(result)}=piton_iterator_new_any((void*)_fil,PK_LIST);}}"
                )
                types[result] = "iterator:list"
                return out
            elif source_type.startswith("iterator:"):
                # ITER_PASSTHROUGH_V1: iter(x) on an iterator returns x
                # itself (CPython semantics) — this unblocks for-loops over
                # builtin iterators (enumerate/zip/map/filter/reversed),
                # whose iter_next dispatch already exists.
                out.append(f"    {_name(result)}={self._value(source)};")
                types[result] = source_type
                if source_type.startswith("iterator:"):
                    self._iter_source[result] = source
                    if source in self._iter_source_r:
                        self._iter_source_r[result] = self._iter_source_r[source]
                    # CALLITER_ELEM_V1: el iter() sobre un iterador mantiene
                    # el tipo de ELEMENTO (`iter(f, sent)` entrega el retorno
                    # de f). La fuente suele ser un LOAD del store, asi que se
                    # busca tambien en sus aliases.
                    _ce = self._calliter_elem.get(source)
                    if not _ce:
                        for _k in (aliases.get(source, ""), self._iter_source.get(source, "")):
                            if _k:
                                _ce = self._calliter_elem.get(_k)
                                if _ce:
                                    break
                    if _ce:
                        self._calliter_elem[result] = _ce
                return out
            elif source_type == "str":
                out.append(f"    {_name(result)}=piton_str_iterator_new((char*){self._value(source)});")
                types[result] = "iterator:str"
                return out
            elif source_type in {"bytes", "bytearray"}:
                out.append(f"    {_name(result)}=piton_bytes_iterator_new((void*){self._value(source)});")
                types[result] = "iterator:bytes"
                return out
            elif source_type == "memoryview":
                # MEMORYVIEW_ITER_V1: iterar la vista da los bytes como enteros.
                out.append(f"    {_name(result)}=piton_memoryview_iter_new((PitonMemoryView*){self._value(source)});")
                types[result] = "iterator:memoryview"
                return out
            else:
                iterator_kind = {"list": "PK_LIST", "tuple": "PK_TUPLE", "dict": "PK_DICT", "set": "PK_SET", "set:frozen": "PK_FROZENSET", "range": "PK_RANGE"}.get(source_type)
                if iterator_kind is None:
                    # ITER_DYNAMIC_V1: tipo desconocido -> lo decide el runtime
                    # (secuencia si lo es, exit 2/TypeError si no).
                    out.append(f"    {_name(result)}=piton_iter_dynamic({self._value(source)});")
                    types[result] = "iterator:any"
                    return out
                out.append(f"    {_name(result)}=piton_iterator_new_any((void*){self._value(source)},{iterator_kind});")
                # COMP_TUPLE_UNPACK_V1: a comprehension over a list of tuples
                # must unpack each item, so the element type follows the
                # comprehension's target (a tuple) rather than the source.
                if result and source_type == "list" and source in self._coll_elems:
                    self._coll_elems[result] = self._coll_elems[source]
                # COLL_ELEM_TYPE_V1: a `para` over a str/float/bigint element
                # collection must print values, not pointers.
                if result and source_type in {"list", "tuple", "set"} and source in self._coll_elems:
                    self._coll_elems[result] = self._coll_elems[source]
                if result and source_type == "list" and source in self._tuple_list_elems:
                    self._tuple_list_elems[result] = self._tuple_list_elems[source]
                # NESTED_TUPLE_LIST_V1: el iterador sobre una lista O tupla de
                # tuplas conserva la estructura anotada de la fuente.
                if result and source_type in {"list", "tuple"} and source in self._nested_tuple_lists:
                    self._nested_tuple_lists[result] = self._nested_tuple_lists[source]
                # DICT_KEY_TYPE_V1: `iter_new` receives a load temp, so the key
                # type is followed through the load as well as the store.
                if result and source_type == 'dict':
                    if source in self._dict_key_types:
                        self._dict_key_types[result] = self._dict_key_types[source]
            types[result] = f"iterator:{source_type or 'unknown'}"
        elif op == "builtin_iter_new":
            builtin, source, start = args
            if builtin == "calliter":
                callable_src, sentinel_src = source
                out.append(f"    {_name(result)}=piton_calliter_new({self._value(callable_src)},{self._value(sentinel_src)});")
                # CALLITER_ELEM_V1: `iter(f, sent)` entrega lo que f
                # devuelve — el tipo de retorno de f (si se conoce) es el
                # tipo del ELEMENTO. Sin esto, `ch en self.whitespace`
                # (netrc) fallaba: el needle se tipaba "int" y el backend
                # rechazaba `x en <str>` con un needle no-str.
                _cb_name = aliases.get(callable_src, callable_src)
                _cb_rt = self.function_return_types.get(_cb_name)
                if _cb_rt is None:
                    # CALLITER_ELEM_V1: el callable puede ser un closure de
                    # metodo ligado (`self._read_char`) — su origen se sabe
                    # por el mapa closure->funcion.
                    _origin = self._closure_origin.get(callable_src)
                    if _origin:
                        _cb_rt = self.function_return_types.get(_origin)
                if _cb_rt and _cb_rt not in {"none", "int"}:
                    self._calliter_elem[result] = _cb_rt
            elif builtin == "enumerate":
                _enum_type = types.get(source)
                start_value = self._value(start) if start is not None else "0"
                if _enum_type not in {"list", "tuple", "str"} and isinstance(_enum_type, str) and _enum_type.startswith("iterator:"):
                    # ENUM_MATERIALIZE_V1: enumerate sobre un ITERADOR nativo
                    # lo materializa primero con piton_collect_any_iter
                    # (heapq: `enumerate(map(iter, iterables))`).
                    _mat = f"_enmat{abs(hash(result)) % 100000}"
                    out.append(
                        f"    {{PitonSeq*{_mat}=(PitonSeq*)piton_collect_any_iter({self._value(source)});"
                    )
                    out.append(
                        f"    {_name(result)}=piton_enumerate_new(piton_slot((long){_mat},PK_LIST),{start_value});}}"
                    )
                    types[result] = "iterator:enumerate"
                elif _enum_type in {"bool", "none", "float", "complex", "bytes", "bytearray"}:
                    # ENUM_SCALAR_V1: scalares evidentes no son iterables.
                    raise NativeBuildError("native enumerate currently requires a list or tuple")
                else:
                    # ENUM_ANY_V1: enumerate acepta cualquier iterable en CPython.
                    # set/dict/range/desconocido se convierten a lista en el
                    # runtime o fallan con TypeError claro.
                    if types.get(source) == "set":
                        _tmp = f"_enset{abs(hash(result)) % 100000}"
                        out.append(f"    long {_tmp}=piton_list_of_dynamic({self._value(source)});")
                        out.append(f"    if(!piton_exc_flag){{")
                        out.append(f"        {_name(result)}=piton_enumerate_new(piton_slot((long){_tmp},PK_LIST),{start_value});")
                        out.append("    }")
                    else:
                        out.append(f"    {_name(result)}=piton_enumerate_new({self._slot(source, types)},{start_value});")
                    if _enum_type == "str":
                        # ENUM_STR_V1: el runtime explota el str; el elemento
                        #-yielded sigue siendo str (no un puntero suelto).
                        self._enum_elem[result] = "str"
                    self._iter_source[result] = source
            elif builtin == "reversed":
                _rev_src_type = types.get(source)
                _rev_src = self._value(source)
                if _rev_src_type == "range":
                    # REVERSED_RANGE_V1: reversed(range(n)) -> lista de los
                    # indices en orden descendente (heapq:
                    # `for i in reversed(range(n//2))`). El runtime expone
                    # piton_range_get, asi que sirve cualquier start/step.
                    out.append(
                        f"    {{PitonSeq*_rv=piton_seq_new(PK_LIST,piton_range_len((PitonRange*){_rev_src}));"
                    )
                    out.append(
                        f"     for(long _ri=0;_ri<_rv->length;++_ri){{piton_seq_put(_rv,_ri,"
                        f"piton_slot(piton_range_get((PitonRange*){_rev_src},_rv->length-1-_ri),PK_INT));}}"
                    )
                    # el resultado de un builtin_iter_new es un ITERADOR (el
                    # `para` llama next() directamente): envolver la lista.
                    out.append(
                        f"     {_name(result)}=piton_iterator_new_any((void*)_rv,PK_LIST);}}"
                    )
                    types[result] = "iterator:list"
                elif _rev_src_type not in {"list", "tuple"}:
                    raise NativeBuildError("native reversed currently requires a list, tuple or range")
                else:
                    out.append(f"    {_name(result)}=piton_reversed_new((void*){_rev_src});")
            elif builtin == "zip":
                left, right = source

                def _zip_materialize(value: Any) -> str:
                    # ZIP_DYNAMIC_V1: cada lado se materializa a una lista antes
                    # de zippear (heapq: `zip(range(n), it)` con it de
                    # `iter(iterable)`). Un range usa piton_range_get; un
                    # iterador/genexpr, su colector. Devuelve el nombre C de la
                    # variable local.
                    name = f"_zl{len(out)}"
                    vtype = types.get(value, "")
                    if vtype in {"list", "tuple"}:
                        # camino directo: ninguna materializacion
                        return f"(void*){self._value(value)}", False
                    if vtype == "range":
                        out.append(
                            f"    {{PitonSeq*{name}=piton_seq_new(PK_LIST,piton_range_len((PitonRange*){self._value(value)}));"
                        )
                        out.append(
                            f"     for(long _zi=0;_zi<{name}->length;++_zi)"
                            f"{{piton_seq_put({name},_zi,piton_slot(piton_range_get((PitonRange*){self._value(value)},_zi),PK_INT));}}"
                        )
                        return f"(void*){name}", True
                    if vtype in {"iterator:genexpr", "genexpr"}:
                        out.append(f"    {{PitonSeq*{name}=(PitonSeq*)piton_genexpr_to_list({self._value(value)});")
                        return f"(void*){name}", True
                    if str(vtype).startswith("iterator:"):
                        out.append(f"    {{PitonSeq*{name}=(PitonSeq*)piton_collect_any_iter({self._value(value)});")
                        return f"(void*){name}", True
                    # ZIP_ATTR_V1: un atributo con tipo estatico desconocido
                    # (ej. exc.exceptions de un parametro) se asume secuencia
                    # (test/support/testcase.py: zip(exc.exceptions,
                    # template.exceptions)) y se usa directo como PitonSeq*.
                    if vtype in ("", "int") or vtype.startswith(("object:", "class:", "slot")):
                        return f"(void*){self._value(value)}", False
                    raise NativeBuildError("native zip requires two lists, tuples or iterables")

                _zl, _lopen = _zip_materialize(left)
                _zr, _ropen = _zip_materialize(right)
                if _lopen:
                    _zl = "(void*)" + _zl[len("(void*)"):]
                out.append(f"    {_name(result)}=piton_zip_new({_zl},{_zr});")
                if _lopen:
                    out.append("    }")
                if _ropen:
                    out.append("    }")
                self._iter_source[result] = left
                self._iter_source_r[result] = right
            elif builtin in {"map", "filter"}:
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native map/filter require a list or tuple")
                if types.get(start) == "builtin":
                    # BUILTIN_CALLBACK_V1: `map(str, xs)`, `map(repr, xs)` etc.
                    # el nombre builtin no tiene direccion de funcion nativa,
                    # pero se resuelve a un helper de runtime. Otros builtins
                    # fallan cerrado.
                    _builtin_name = aliases.get(start, start)
                    if _builtin_name == "str":
                        callback = "piton_str_of_value"
                    elif _builtin_name == "repr":
                        callback = "piton_repr_of_value"
                    elif _builtin_name == "len":
                        callback = "piton_len_of_value"
                    else:
                        raise NativeBuildError(
                            f"native {builtin} requires a user function callback, not a builtin name"
                        )
                else:
                    callback = self._value(start)
                out.append(f"    {_name(result)}=piton_callback_iterator_new((void*){self._value(source)},(long){callback},{1 if builtin == 'filter' else 0});")
            elif builtin == "sorted":
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native sorted currently requires one list or tuple")
                out.append(f"    {_name(result)}=piton_sorted_new((void*){self._value(source)});")
            else:
                raise NativeBuildError(f"native builtin iterator not supported: {builtin}")
            # REVERSED_RANGE_V1: el caso range ya dejo su tipo ("iterator:list",
            # porque materializa la lista y la envuelve en un iterador generico);
            # no sobrescribir con "iterator:reversed", que haria que el `para`
            # llamara piton_reversed_next sobre un PitonAnyIterator.
            types.setdefault(result, f"iterator:{builtin}")
        elif op == "iter_next":
            iterator, handler_label = args[0], args[1]
            iterator_type = types.get(iterator, "")
            # ITER_PASSTHROUGH_V1: a nested loop's exhaustion flag would
            # otherwise trip the outer loop's check (same stale-flag class
            # the Windows backend clears at the loop-exit block). Any live
            # flag here is stale by construction — live raises route or exit
            # at their own emission site — so clearing on entry is sound.
            out.append("    piton_catch_clear();")
            # DICT_KEY_SLOT_V1: `para k en d:` sobre un dict cuya key type
            # estatica se desconoce (graphlib.py: _find_cycle itera
            # self._node2info). CPython itera keys de cualquier tipo: la
            # key se empaqueta como SLOT ETIQUETADO con la kind derivada
            # en runtime (piton_slot_guess, misma politica que los bound
            # methods nativos) en vez de fallar cerrado. Limitacion
            # honesta: una key float se deriva como int (sus bits no pasan
            # maybe_ptr).
            if (
                iterator_type == "iterator:dict"
                and self._dict_key_types.get(iterator) is None
            ):
                out.append(
                    f"    {{PitonSlot*_dk=piton_alloc(sizeof(PitonSlot));"
                    f"*_dk=piton_slot_guess(piton_iterator_next_any({self._value(iterator)}));"
                    f" {_name(result)}=(long)_dk;}}"
                )
                types[result] = "slot"
                return out
            if iterator_type in {"genexpr", "iterator:genexpr"}:
                out.append(f"    {_name(result)}=piton_genexpr_next((PitonGenExpr*){self._value(iterator)});")
            elif iterator_type == "generator":
                out.append(f"    {_name(result)}=piton_gen_next({self._value(iterator)});")
            elif iterator_type == "iterator:str":
                out.append(f"    {_name(result)}=piton_str_iterator_next({self._value(iterator)});")
            elif iterator_type == "iterator:bytes":
                out.append(f"    {_name(result)}=piton_bytes_iterator_next({self._value(iterator)});")
            elif iterator_type == "iterator:memoryview":
                out.append(f"    {_name(result)}=piton_memoryview_iter_next({self._value(iterator)});")
            elif iterator_type == "iterator:any":
                out.append(f"    {_name(result)}=piton_iterator_next_any({self._value(iterator)});")
            elif iterator_type.startswith("iterator:object:") or iterator_type.startswith("object:"):
                class_name = iterator_type.split(":", 2)[2] if iterator_type.startswith("iterator:") else iterator_type.split(":", 1)[1]
                method_class = self._resolve_method(class_name, "__next__")
                out.append(f"    {_name(result)}={_name(method_class+'__'+'__next__')}({self._value(iterator)});")
            else:
                next_helper = {"iterator:enumerate": "piton_enumerate_next", "iterator:reversed": "piton_reversed_next", "iterator:zip": "piton_zip_next", "iterator:map": "piton_callback_iterator_next", "iterator:filter": "piton_callback_iterator_next", "iterator:calliter": "piton_calliter_next"}.get(iterator_type, "piton_iterator_next_any")
                out.append(f"    {_name(result)}={next_helper}({self._value(iterator)});")
            # DICT_KEY_TYPE_V1: a dict iterator yields its KEYS, whose static
            # type comes from the dict it was built from. Only when it is
            # unknown do we keep the historical "str"; a value that is really
            # a str pointer must never be printed as a raw int, and vice versa.
            if iterator_type == "iterator:enumerate":
                src = None
                _elem = None
                _k = args[0]
                for _ in range(12):
                    _elem = _elem or self._enum_elem.get(_k)
                    _h = self._iter_source.get(_k)
                    if _h is not None:
                        src = _h
                        _k = _h
                        if _h not in self._iter_source:
                            break
                        continue
                    _k2 = aliases.get(_k)
                    if _k2 is None or _k2 == _k:
                        break
                    _k = _k2
                ekind = (self._coll_elems.get(src) if src else None) or _elem
                self._tuple_elems[result] = (("%idx", "int"), (src or "%src", ekind if ekind and "|" not in ekind else "int"))
            if iterator_type == "iterator:dict":
                key_type = self._dict_key_types.get(args[0])
                if key_type is None:
                    raise NativeBuildError(
                        "Linux native dict iteration requires a statically known key type "
                        "(CPython iterates keys of any type)"
                    )
                types[result] = key_type
            elif iterator_type in {"iterator:list", "iterator:tuple", "iterator:set"}:
                # COLL_ELEM_TYPE_V1: the yielded value carries the element kind.
                elem_kind = self._coll_elems.get(args[0])
                types[result] = elem_kind if elem_kind and "|" not in elem_kind else "int"
                _cands = {args[0], aliases.get(args[0], ""), self._iter_source.get(args[0], "")}
                for _k in list(_cands):
                    if _k:
                        _cands.add(aliases.get(_k, ""))
                        _cands.add(self._iter_source.get(_k, ""))
                _tx = None
                for _k in _cands:
                    _tx = self._tuple_list_elems.get(_k)
                    if _tx is not None:
                        break
                if _tx is not None and elem_kind == "tuple":
                    self._tuple_elems[result] = (("%k", _tx[0]), ("%v", _tx[1]))
                # NESTED_TUPLE_LIST_V1: si el elem es tuple, el item rendido es
                # una TUPLA — propaga _coll_elems para que el load del loop var
                # (table) y el iter_new siguiente la hereden.
                if elem_kind == "tuple":
                    self._coll_elems[result] = "tuple"
                    # NESTED_TUPLE_LIST_V1: el source es una lista/tupla de
                    # tuplas. El shape del ITEM es el del PRIMER sub-item de la
                    # coleccion. Busca _tuple_elems en el source y sus aliases;
                    # si no, en _nested_tuple_lists (shape de la primera tupla).
                    if types.get(args[0]) in {"list", "tuple"}:
                        _src_te = self._tuple_elems.get(args[0])
                        if _src_te is None:
                            _nt = self._nested_tuple_lists.get(args[0])
                            if _nt is not None:
                                _src_te = tuple((f"%{i}", t) for i, t in enumerate(_nt))
                        if _src_te is not None:
                            self._tuple_elems[result] = _src_te
                # NESTED_TUPLE_LIST_V1: el valor rendido por un iterador sobre
                # una lista/tupla de tuplas es una TUPLA. Copia la estructura
                # del shape de la PRIMERA sub-tupla de la fuente (CPython
                # desempaqueta contra la coincidencia de la primera). Para
                # colecciones heterogeneas (stat: tupla de tablas de aridades
                # distintas), cada ITEM ya es una tupla individual homogenea:
                # su shape vive en _tuple_elems de la fuente, no en
                # _nested_tuple_lists. Fallback: si la fuente es tuple pero no
                # nested, buscar el shape en _tuple_elems del primer sub-item.
                _nt = None
                for _k in _cands:
                    _nt = self._nested_tuple_lists.get(_k)
                    if _nt is not None:
                        break
                if _nt is not None and elem_kind == "tuple":
                    self._tuple_elems[result] = tuple((f"%{i}", t) for i, t in enumerate(_nt))
                    # NESTED_TUPLE_LIST_V1: el valor rendido apunta a la lista
                    # fuente, para que unpack_check pueda resolver la aridad
                    # leyendo _nested_tuple_lists sobre el mismo source.
                    self._iter_source[result] = self._iter_source.get(args[0], args[0])
                elif elem_kind == "tuple" and types.get(args[0]) in {"list", "tuple"}:
                    # FALLBACK: la fuente es una coleccion cuyo elem es tuple
                    # pero no tiene nested anotado (outer heterogeneo). El item
                    # rendido ES una tupla; su shape se resuelve en el
                    # unpack_check via _tuple_elems del SOURCE (cada sub-item
                    # del outer tiene _tuple_elems del build). Marca el source
                    # para que unpack_check lea el shape del primer sub-item.
                    self._iter_source[result] = self._iter_source.get(args[0], args[0])
            elif iterator_type == "iterator:bytes":
                types[result] = "int"
            elif iterator_type == "iterator:calliter":
                # CALLITER_ELEM_V1: el elemento de `iter(f, sent)` tiene el
                # tipo de retorno de f (netrc: `iter(self._read_char, "")`
                # entrega str, y `ch en self.whitespace` lo necesita).
                types[result] = self._calliter_elem.get(args[0], "int")
            else:
                types[result] = "tuple" if iterator_type in {"iterator:enumerate", "iterator:zip"} else "str" if iterator_type == "iterator:str" else "int"
            out.append("    if(piton_exc_flag){")
            if handler_label:
                out.append(f"        goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("        piton_report_unhandled();piton_exit(1);")
            out.append("    }")
        elif op == "branch_exc":
            # FINALBODY_UNWIND_V1: if an exception is still live (the finally
            # was reached through unwinding), propagate it; otherwise continue
            # to the end of the try/finally.
            self._emit_exc_check(out, function, None)
            out.append(f"    goto {_name(function.name + '_' + args[0])};")
        elif op == "const":
            value = args[0]
            # INTOVF_GUARD_V1: record constant values so the binary lowering
            # can fold int + - * exactly (Python bignum arithmetic IS the
            # oracle) instead of emitting wrapping i64 C arithmetic.
            if result:
                self._fn_consts[result] = value
            if isinstance(value, str):
                out.append(f"    {_name(result)}=(long){_str_c_literal(value)};")
                types[result] = "str"
            elif isinstance(value, bytes):
                out.append(f"    {_name(result)}=(long)piton_bytes_new({len(value)},(const unsigned char*){_bytes_c_literal(value)});")
                types[result] = "bytes"
            elif isinstance(value, bytearray):
                out.append(f"    {_name(result)}=(long)piton_bytearray_new({len(value)},(const unsigned char*){_bytes_c_literal(bytes(value))});")
                types[result] = "bytearray"
            elif isinstance(value, (int, bool)) or value is None:
                if isinstance(value, int) and abs(value) > 9223372036854775807:
                    out.append(f"    {_name(result)}=(long)piton_bigint_from_str({json.dumps(str(value))});")
                    types[result] = "bigint"
                    bigint_slots.append(result)
                else:
                    out.append(f"    {_name(result)}=(long)({self._value(value)});")
                    types[result] = "bool" if isinstance(value, bool) else "none" if value is None else "int"
            elif isinstance(value, float):
                out.append(f"    {_name(result)}=piton_double_bits({_float_c_literal(value)});")
                types[result] = "float"
            elif isinstance(value, complex):
                self._fn_consts[result] = value
                out.append(f"    {_name(result)}=(long)piton_complex_new({_float_c_literal(value.real)},{_float_c_literal(value.imag)});")
                types[result] = "complex"
            elif isinstance(value, (list, tuple)):
                # M6 SEQ_CONST_V1: los call paths de kwonly/frame-ABI emiten
                # el default collection como const; _value lo convierte a
                # IIFE piton_seq_new y se anota el tipo (+ elems de tuple).
                out.append(f"    {_name(result)}={self._value(value)};")
                types[result] = "list" if isinstance(value, list) else "tuple"

                def _seq_elem_kind(item: Any) -> str:
                    if isinstance(item, str):
                        return "str"
                    if isinstance(item, bool):
                        return "bool"
                    if isinstance(item, int):
                        return "int"
                    if item is None:
                        return "none"
                    return "float"

                if isinstance(value, tuple):
                    self._tuple_elems[result] = tuple((item, _seq_elem_kind(item)) for item in value)
                elif value:
                    kinds = {_seq_elem_kind(item) for item in value}
                    if len(kinds) == 1:
                        self._coll_elems[result] = kinds.pop()
            elif value is Ellipsis:
                # ELLIPSIS_SINGLETON_V1: `...` baja como el singleton del
                # runtime (identidad propia, igual que None).
                out.append(f"    {_name(result)}=PITON_ELLIPSIS;")
                types[result] = "ellipsis"
            else:
                raise NativeBuildError(f"Linux backend cannot encode constant {value!r}")
        elif op == "load":
            source = args[0]
            # DEL_FN_SCOPE_V1: el NameError estatico de `borrar` aplica al
            # flujo lineal de <module> y a deletes anteriores EN LA MISMA
            # funcion — no a funciones que leen un nombre que el modulo
            # borra despues (opcode.py:282 del def_op; las calls ocurren
            # antes del del).
            # DEL_ORDER_V1: el delete solo mata los loads POSTERIORES a el
            # dentro del MISMO bloque. El orden entre bloques no es
            # modelable con el orden de emision (hashlib: `del __get_hash`
            # al final del modulo, con el load DENTRO de un loop, que se
            # emite despues).
            _del_at = self._deleted_at.get((function.name, source))
            _deleted_for_this_scope = (
                source in self._deleted_names
                and (
                    function.name == "<module>"
                    or source in self._deleted_by_function.get(function.name, ())
                )
                and _del_at is not None
                and _del_at[0] == self._cur_block
                and _del_at[1] < self._instr_seq
            )
            if _deleted_for_this_scope:
                # DEL_NAME_V1: borrado estatico -> NameError en build modelado
                raise NativeBuildError(f"NameError: name '{source}' is not defined")

            aliases[result] = source
            types[result] = types.get(source, "int")
            if source in self._tuple_elems:
                self._tuple_elems[result] = self._tuple_elems[source]
            if source in self._dict_elems:
                self._dict_elems[result] = self._dict_elems[source]
            # DICT_KEY_TYPE_V1: the key type follows a load, so
            # `para k en d` (whose iter_new source is the load temp) resolves.
            if source in self._dict_key_types:
                self._dict_key_types[result] = self._dict_key_types[source]
            if source in self._coll_elems:
                self._coll_elems[result] = self._coll_elems[source]
            if source in self._nested_tuple_lists:
                self._nested_tuple_lists[result] = self._nested_tuple_lists[source]
            if source in self._tuple_elems:
                self._tuple_elems[result] = self._tuple_elems[source]
            if source in self._chr_results:
                self._chr_results.add(result)
            if source in self._dict_val_types:
                self._dict_val_types[result] = self._dict_val_types[source]
            # ELEM_KIND_V1: al cargar un param list con elem-kind conocido,
            # anotar el temp result (getopt: args[0] es str, no int, por
            # que args[0].startswith('-') resuelve el elemento como str).
            # Va DESPUES de las copias genericas: `_coll_elems[source]` puede
            # traer el elem-kind de otro binding del mismo nombre.
            if source in function.params:
                _ek = self._usage_param_elems.get(function.name, {}).get(source)
                if _ek:
                    self._coll_elems[result] = _ek
            if source in self.function_names:
                out.append(f"    {_name(result)}=(long)&{_name(source)};")
                return out
            if source in self.classes:
                # CLASS_OBJECT_LOAD_V1: cargar el nombre de una clase produce
                # un objeto de clase REAL (no un alias al identificador, que
                # no existe en C: `P.f()` emitía `P__f(P)` -> undeclared).
                # El objeto además hace observable `cls` en classmethods y
                # `P()` vía el path PK_OBJECT de piton_closure_call_frame.
                types[result] = f"class:{source}"
                if function.name == "<module>" and source in self._module_stored:
                    out.append(f"    {_name(result)}={_name(source)};")
                else:
                    # CLASS_OBJECT_INTERN_V1: el objeto de clase es UNO por
                    # clase (variable a nivel de archivo) — la identidad del
                    # objeto de clase se comporta como en CPython.
                    _cvar = _name("_clsobj_" + source)
                    out.append(
                        f"    if(!{_cvar}){{{_cvar}=(long)piton_object_new(\"{source}\",0);"
                        f"    piton_object_set((PitonObject*){_cvar},\"__name__\","
                        f"piton_slot((long)\"{source}\",PK_STR));}}"
                    )
                    out.append(f"    {_name(result)}={_cvar};")
                    self._emit_class_bases_attribute(out, result, source)
                return out
            if source in _BUILTIN_EXCEPTIONS:
                # BUILTIN_EXC_CLASS_V1: cargar el nombre de una excepcion
                # builtin (Exception, ValueError, ...) produce un objeto de
                # clase — la llamada `Exception.__init__(self, ...)` es
                # DESBOUND (el self ya viene en los argumentos), y sin esta
                # marca el dispatch la tomaba por una llamada ligada y
                # duplicaba el self.
                types[result] = f"class:{source}"
                out.append(f'    {_name(result)}=(long)piton_object_new("{source}",0);')
                return out
            if source in {"object", "objeto"}:
                types[result] = "class:object"
                aliases[result] = source
                out.append(f"    {_name(result)}=0;")
                return out
            if source in {"bytes", "octetos"}:
                types[result] = "class:bytes"
                aliases[result] = source
                out.append(f"    {_name(result)}=0;")
                return out
            if source in {"bytearray", "arreglo_bytes"}:
                types[result] = "class:bytearray"
                aliases[result] = source
                out.append(f"    {_name(result)}=0;")
                return out
            if source in {"dict", "diccionario", "slice", "rebanada"}:
                # CLASS_LOAD_V1: dict.fromkeys / slice(...) necesitan el tipo
                # de clase para rutear el classmethod.
                _cn = "dict" if source in {"dict", "diccionario"} else "slice"
                types[result] = f"class:{_cn}"
                aliases[result] = source
                out.append(f"    {_name(result)}=0;")
                return out
            if (
                source in _BUILTINS
                and source not in function.params
                and source not in getattr(self, "_cur_slots", set())
                and source not in _FUNC_ONLY_BUILTINS
            ):
                # BUILTIN_MARKER_V1: this load emits NO C code — the builtin is
                # only resolved by the call dispatch. Mark the operand so any
                # other consumer fails closed instead of reading an
                # uninitialized C variable (previously: SIGSEGV / garbage).
                types[result] = "builtin"
                return out
            # Module owners are lowered before their specialized attribute
            # call is recognized. They are only a marker here, not C values.
            module_prefix = any(name.startswith(f"{source}__") for name in self.function_names)
            if (
                (source in {"math", "asyncio", "sys"} or module_prefix)
                and source not in function.params
                and types.get(source) != "object:module"
            ):
                out.append(f"    {_name(result)}=0;")
                types[result] = "module"
                return out
            # GLOBAL_DECL_V1: the declaration wins over local stores
            # (`global g` + `g = g + 1` reads the shared cell, like CPython).
            if source in self._shared_globals and (
                function.name == "<module>" or source in self._func_globals.get(function.name, ())
            ):
                if function.name != "<module>" and (
                    getattr(function, "is_generator", False)
                    or getattr(function, "is_coroutine", False)
                    or getattr(function, "is_async_generator", False)
                ):
                    raise NativeBuildError(f"Linux global '{source}' inside generators is not supported yet")
                out.append(f"    {_name(result)}={_name(source)};")
                types[result] = self._module_types.get(source, types.get(source, "int"))
                if source in self._tuple_elems:
                    self._tuple_elems[result] = self._tuple_elems[source]
                if source in self._dict_elems:
                    self._dict_elems[result] = self._dict_elems[source]
                # DICT_KEY_TYPE_V1: the key type follows the value through an
                # assignment, so `d = {1: 'a'}` then `para k en d` still knows
                # the keys are ints.
                if source in self._dict_key_types:
                    self._dict_key_types[result] = self._dict_key_types[source]
                if source in self._coll_elems:
                    self._coll_elems[result] = self._coll_elems[source]
                if source in self._nested_tuple_lists:
                    self._nested_tuple_lists[result] = self._nested_tuple_lists[source]
                if source in self._dict_val_types:
                    self._dict_val_types[result] = self._dict_val_types[source]
                return out
            if (
                source in self._module_stored
                and function.name != "<module>"
                and source not in function.params
                and source not in self._func_stores.get(function.name, ())
            ):
                # MODULE_GLOBAL_READ_V1: CPython permite leer un nombre de
                # modulo sin declarar `global` (solo la escritura es local
                # sin declaracion). La funcion no lo asigna localmente ni
                # lo declara, asi que el load es el module global — mismo
                # emit que GLOBAL_DECL_V1, sin el opt-in explicito
                # (opcode/stat del corpus: lectura de constantes de modulo
                # dentro de funciones; la escritura sigue exigiendo
                # `global` para no volverse silenciosamente local).
                # MODULE_GLOBAL_GEN_V1: leer un global de modulo dentro de un
                # generador es valido y CORRECTO: el C lee la variable de
                # ambito de archivo en cada ejecucion del body, igual que
                # CPython ve el valor vivo al reanudar. El guard previo era
                # conservador (solo aplicaba a la escritura, que sigue
                # exigiendo `global`).
                out.append(f"    {_name(result)}={_name(source)};")
                types[result] = self._module_types.get(source, types.get(source, "int"))
                if source in self._tuple_elems:
                    self._tuple_elems[result] = self._tuple_elems[source]
                if source in self._dict_elems:
                    self._dict_elems[result] = self._dict_elems[source]
                if source in self._dict_key_types:
                    self._dict_key_types[result] = self._dict_key_types[source]
                if source in self._coll_elems:
                    self._coll_elems[result] = self._coll_elems[source]
                if source in self._nested_tuple_lists:
                    self._nested_tuple_lists[result] = self._nested_tuple_lists[source]
                if source in self._dict_val_types:
                    self._dict_val_types[result] = self._dict_val_types[source]
                return out
            # NAME_RESOLVE_V1: every resolution above declined, so this bare
            # identifier would reach the C file undeclared (gcc naming a
            # Spanish builtin like `suma` is not a PITON diagnostic).
            if source in _FUNC_ONLY_BUILTINS and source not in getattr(self, "_cur_slots", set()):
                # callable-only builtin used as a callee: a marker, no value
                types[result] = "builtin"
                aliases[result] = source
                return out
            if (
                source not in getattr(self, "_cur_slots", set())
                and source not in self._shared_globals
                and source not in getattr(function, "cell_params", ())
                and source not in getattr(function, "params", ())
                and source not in {"Verdadero", "Falso", "Nada", "Verdadera", "Falsa", "Ninguno"}
                and source not in (self._func_only_builtins or _FUNC_ONLY_BUILTINS)
            ):
                self._runtime_name_error(out, source)
                types[result] = "int"
                return out
            out.append(f"    {_name(result)}={_name(source)};")
        elif op == "global_decl":
            # GLOBAL_DECL_V1: pure metadata (resolved in the pre-scan); no code.
            return out
        elif op == "store":
            if types.get(args[1]) == "builtin":
                # BUILTIN_MARKER_V1: almacenar un builtin como valor (ej.
                # _types.py: `Pos = int`). El backend nativo no permite
                # almacenar builtins directamente como valores (fail-closed).
                raise NativeBuildError(
                    f"native store of builtin '{args[1]}' is not supported"
                )
            if (
                args[0] in self._shared_globals
                and (function.name == "<module>" or args[0] in self._func_globals.get(function.name, ()))
            ):
                if function.name != "<module>" and (
                    getattr(function, "is_generator", False)
                    or getattr(function, "is_coroutine", False)
                    or getattr(function, "is_async_generator", False)
                ):
                    raise NativeBuildError(f"Linux global '{args[0]}' inside generators is not supported yet")
                out.append(f"    {_name(args[0])}={self._value(args[1])};")
                types[args[0]] = types.get(args[1], "int")
                if args[1] in self._tuple_elems:
                    self._tuple_elems[args[0]] = self._tuple_elems[args[1]]
                if args[1] in self._dict_elems:
                    self._dict_elems[args[0]] = self._dict_elems[args[1]]
                if args[1] in self._dict_key_types:
                    self._dict_key_types[args[0]] = self._dict_key_types[args[1]]
                # SHARED_COLL_ELEMS_V1: los globals promovidos a static (por
                # `global` o por MODULE_GLOBAL_READ) también heredan el elem
                # type — sin esto, `para oplist en oplists` perdia el tipo del
                # for-target y oplist.append reventaba como receiver
                # desconocido (opcode.pseudo_op sobre oplist global).
                if args[1] in self._coll_elems:
                    self._coll_elems[args[0]] = self._coll_elems[args[1]]
                # NESTED_TUPLE_LIST_V1: un global promovido a static hereda la
                # estructura anidada de tuplas — sin esto, `table = (...)` en un
                # modulo pierde el shape al leerlo como global compartido.
                if args[1] in self._nested_tuple_lists:
                    self._nested_tuple_lists[args[0]] = self._nested_tuple_lists[args[1]]
                if args[1] in self._dict_val_types:
                    self._dict_val_types[args[0]] = self._dict_val_types[args[1]]
                return out
            out.append(f"    {_name(args[0])}={self._value(args[1])};")
            types[args[0]] = types.get(args[1], "int")
            # CALLITER_ELEM_V1: el nombre hereda el tipo de ELEMENTO del
            # iterador (`fiter = iter(f, sent)` -> `fiter` entrega el retorno
            # de f).
            if args[1] in self._calliter_elem:
                self._calliter_elem[args[0]] = self._calliter_elem[args[1]]
            if args[1] in self._builtin_bound:
                # BUILTIN_BOUND_METHOD_V1: `h_append = h.append` — el nombre
                # hereda el vinculo con la lista dueña.
                self._builtin_bound[args[0]] = self._builtin_bound[args[1]]
            # SYS_FDS_V1: el modulo `sys` lleva sus descriptores y su
            # version_info. stdout/stderr son los fd 1 y 2 (el backend no
            # tiene objetos de archivo: el descriptor es la representacion
            # fiel para `print(..., file=sys.stderr)`); version_info es la
            # version del host (el binario nativo targetiza su stdlib).
            if args[0] == "sys" and function.name == "<module>":
                out.append(
                    f'    piton_object_set((PitonObject*){_name(args[1])},"stdout",piton_slot(1,PK_INT));'
                )
                out.append(
                    f'    piton_object_set((PitonObject*){_name(args[1])},"stderr",piton_slot(2,PK_INT));'
                )
                _vi = getattr(sys, "version_info", None)
                if _vi is not None:
                    out.append(f"    {{PitonSeq*_vi=piton_seq_new(PK_TUPLE,{len(_vi)});")
                    for _v_i, _v in enumerate(_vi):
                        if isinstance(_v, int):
                            out.append(f"    piton_seq_put(_vi,{_v_i},piton_slot({_v},PK_INT));")
                        else:
                            out.append(
                                f'    piton_seq_put(_vi,{_v_i},piton_slot((long){json.dumps(str(_v))},PK_STR));'
                            )
                    out.append(
                        f'    piton_object_set((PitonObject*){_name(args[1])},"version_info",piton_slot((long)_vi,PK_TUPLE));}}'
                    )
            # CELL_TYPE_V1: al guardar una celda bajo el nombre de la
            # captura, el tipo del VALOR capturado queda asociado a ese
            # nombre (la carga de la celda lo consulta por nombre).
            if types.get(args[1]) == "cell":
                _cell_value_type = self._cell_types.get(args[1])
                if _cell_value_type:
                    self._cell_types[args[0]] = _cell_value_type
            # STORE_TARGETS_V1: que nombres guardaron este valor — el
            # elem-kind de una lista puede apesare despues del store (la
            # comprension se llena despues), y hay que propagarlo.
            self._store_targets.setdefault(args[1], set()).add(args[0])
            # SEED_KEEP_V1: un store cuyo valor NO tiene tipo conocido (el
            # unpack de una tupla de elems desconocidos deja el valor sin
            # tipo) no debe pisar el tipo deducido del param. El seed se
            # computa sobre TODOS los usos del param en la funcion, asi que
            # describe su tipo a lo largo de toda su vida. getopt:
            # `opts, args = do_longs(...)` y `has_arg, opt = long_has_args(..)`
            # degradaban los params a int y sus usos (args[0][1:],
            # opt.index('=')) fallaban cerrado.
            _seed_keep = (self._usage_param_types.get(function.name) or {}).get(args[0]) or (
                self._param_seeds.get(function.name) or {}
            ).get(args[0])
            if (
                _seed_keep
                and _seed_keep != "int"
                and types.get(args[1]) is None
            ):
                types[args[0]] = _seed_keep
            # DICT_VAL_TYPE_V1: a dict value stored into a name keeps the
            # value-type record, so `d = {...}; d.get(k)` resolves.
            if args[1] in self._dict_val_types:
                self._dict_val_types[args[0]] = self._dict_val_types[args[1]]
            if args[1] in self._dict_empty:
                self._dict_empty[args[0]] = True
            # DICT_KEY_TYPE_V1: the key type follows the value into the local,
            # so iterating `d` after `d = {1: 'a'}` still knows it yields ints.
            if args[1] in self._dict_key_types:
                self._dict_key_types[args[0]] = self._dict_key_types[args[1]]
            if args[1] in self._coll_elems:
                self._coll_elems[args[0]] = self._coll_elems[args[1]]
            if args[1] in self._nested_tuple_lists:
                self._nested_tuple_lists[args[0]] = self._nested_tuple_lists[args[1]]
            if args[1] in self._tuple_elems:
                self._tuple_elems[args[0]] = self._tuple_elems[args[1]]
            if args[1] in self._chr_results:
                self._chr_results.add(args[0])
            if args[1] in self._dict_val_types:
                self._dict_val_types[args[0]] = self._dict_val_types[args[1]]
            if args[1] in self._dict_empty:
                self._dict_empty[args[0]] = True
            if isinstance(args[0], str) and args[0].startswith("@boolh_"):
                # BOOL_SLOT_V1: CPython `y`/`o` return the OPERAND, so both
                # arms may be different types. Same-typed arms keep the plain
                # path (usable in arithmetic); mixed arms become a TAGGED SLOT
                # so the printed form matches whichever side won.
                seen = self._boolh_types.get(args[0])
                current = types.get(args[1], "int")
                _arm_type = types.get(args[1], "int")
                if _arm_type == "slot":
                    # BOOL_SLOT_V1: in a chained `y/o` the left arm is already
                    # a tagged slot (the holder of the previous op). Copying
                    # its pointer PRESERVES the winning kind; re-tagging would
                    # wrap the pointer itself as PK_INT and corrupt it.
                    types[args[0]] = "slot"
                    self._boolh_types[args[0]] = "slot"
                    return out
                
                _holder = args[0]
                _slot_expr = self._slot(args[1], types)
                _tag = f"/*boolh:{_holder}*/"
                if seen is None:
                    self._boolh_types[_holder] = current
                    self._boolh_line[_holder] = (self._value(args[1]), seen)
                    out.append(f"    {_name(_holder)}={self._value(args[1])};{_tag}")
                elif current != seen:
                    # mixed arms: BOTH become tagged slots so the printed
                    # form matches whichever side won (CPython returns the
                    # operand). The first arm was already emitted plain, so
                    # its line is rewritten by tag.
                    _first = self._boolh_line.pop(_holder, None)
                    _slot_id = abs(hash(_holder)) % 100000
                    if _first is not None:
                        _expr, _kind = _first
                        _pool = list(getattr(self, "_cur_lines", []) or []) + out
                        for _i, _line in enumerate(_pool):
                            if _tag in _line:
                                _repl = (
                                    f"    {{PitonSlot*_bha{_slot_id}=piton_alloc(sizeof(PitonSlot));"
                                    f"*_bha{_slot_id}=piton_slot({_expr},{self._kind(_kind)});"
                                    f" {_name(_holder)}=(long)_bha{_slot_id};}}"
                                )
                                if _i < len(out):
                                    out[_i] = _repl
                                else:
                                    self._cur_lines[_i] = _repl
                                break
                    out.append(
                        f"    {{PitonSlot*_bhb{_slot_id}=piton_alloc(sizeof(PitonSlot));"
                        f"*_bhb{_slot_id}={_slot_expr};"
                        f" {_name(_holder)}=(long)_bhb{_slot_id};}}"
                    )
                    types[_holder] = "slot"
                    types[_holder] = "slot"
            if args[1] in self._tuple_elems:
                self._tuple_elems[args[0]] = self._tuple_elems[args[1]]
            if args[1] in self._dict_elems:
                self._dict_elems[args[0]] = self._dict_elems[args[1]]
        elif op == "gettext_identity":
            # GETTEXT_IDENTITY_V1: gettext.gettext(msg) devuelve msg sin
            # cambios (identidad). El resultado conserva la plantilla
            # literal para que el operador `%` la pueda usar.
            value = args[0]
            out.append(f"    {_name(result)}={self._value(value)};")
            types[result] = types.get(value, "int")
            _gt_literal = self._fn_consts.get(value)
            if isinstance(_gt_literal, str):
                self._identity_templates[result] = _gt_literal
            return out
        elif op == "unary":
            operator = {"no": "!", "not": "!"}.get(args[0], args[0])
            if operator not in {"+", "-", "~", "!"}:
                raise NativeBuildError(f"Linux unary operator not supported: {operator}")
            if types.get(args[1]) == "none" and operator in {"+", "-", "~"}:
                # UNARY_NONE_V1: `-None`, `+None`, `~None` — CPython levanta
                # TypeError en runtime (mensaje exacto), no error de build.
                out.append(f'    piton_raise_set("TypeError","bad operand type for unary {operator}: \'NoneType\'");')
                self._emit_exc_check(out, function, None)
                return out
            if operator == "-" and types.get(args[1]) == "bigint":
                out.append(f"    {_name(result)}=(long)piton_bigint_negate((void*){_name(args[1])});")
                types[result] = "bigint"
                bigint_slots.append(result)
                return out
            if operator == "!":
                # TRUTHY_FIX_V1: `no X` is not !(raw value) — a non-null
                # pointer (empty str/list/dict/set) is always truthy that
                # way, so `no ''` and `no []` printed False instead of
                # True. Route through the same per-kind truthiness as
                # truth_test, negated (also opens float `no`, previously
                # rejected).
                operand = args[1]
                vtype = types.get(operand, "int")
                if vtype.startswith("class:"):
                    vtype = vtype.split(":", 1)[1]
                if vtype in {"int", "bool"}:
                    out.append(f"    {_name(result)}=({self._value(operand)}==0);")
                elif vtype == "float":
                    out.append(f"    {_name(result)}=(piton_bits_double({self._value(operand)})==0.0);")
                elif vtype == "none":
                    out.append(f"    {_name(result)}=1;")
                elif vtype == "str":
                    out.append(f"    {_name(result)}=(piton_strlen((const char*){self._value(operand)})==0);")
                elif vtype == "bytes":
                    out.append(f"    {_name(result)}=(((PitonBytes*){self._value(operand)})->length==0);")
                elif vtype == "bytearray":
                    out.append(f"    {_name(result)}=(((PitonByteArray*){self._value(operand)})->length==0);")
                elif vtype in {"list", "tuple"}:
                    out.append(f"    {_name(result)}=(((PitonSeq*){self._value(operand)})->length==0);")
                elif vtype == "dict":
                    out.append(f"    {_name(result)}=(((PitonDict*){self._value(operand)})->length==0);")
                elif vtype == "set":
                    out.append(f"    {_name(result)}=(((PitonSet*){self._value(operand)})->length==0);")
                elif vtype == "range":
                    # RANGE_VALUE_V1: `no rango(0)` is True.
                    out.append(f"    {_name(result)}=(piton_range_len((PitonRange*){self._value(operand)})==0);")
                elif vtype == "complex":
                    out.append(f"    {_name(result)}=(piton_cx_truthy_bits({self._value(operand)})==0);")
                elif vtype == "slot":
                    # SLOT_TRUTH_V1: `no X` sobre un slot ETIQUETADO (el
                    # resultado de un `o`/`y` de brazos de tipo distinto):
                    # la verdad se decide por la kind del slot.
                    out.append(
                        "    {PitonSlot _ts=*(PitonSlot*)"
                        f"{self._value(operand)};"
                        f"{_name(result)}=!(_ts.kind==PK_NONE?0:(_ts.kind==PK_STR?(_ts.bits&&((const char*)_ts.bits)[0]!=0):"
                        "(_ts.kind==PK_LIST||_ts.kind==PK_TUPLE?(((PitonSeq*)_ts.bits)->length>0):"
                        "(_ts.kind==PK_DICT?(((PitonDict*)_ts.bits)->length>0):(_ts.bits!=0)))));}"
                    )
                else:
                    raise NativeBuildError(f"Linux truth test does not support {vtype}")
                types[result] = "bool"
                return out
            _uowner = types.get(args[1], "")
            if _uowner.startswith("object:"):
                # UNARY_DUNDER_V1: -obj / +obj / ~obj despachan a __neg__/
                # __pos__/__invert__ por MRO. Antes -P() restaba sobre el
                # PUNTERO e imprimia una dirección.
                _ucls = _uowner.split(":", 1)[1]
                _dunder = {"-": "__neg__", "+": "__pos__", "~": "__invert__"}.get(operator)
                if _dunder is None:
                    raise NativeBuildError(f"Linux unary {operator} on an object is not supported")
                _uhit = None
                for _cand in self.class_mro.get(_ucls, [_ucls]):
                    if _dunder in self.classes.get(_cand, set()):
                        _uhit = _cand
                        break
                if _uhit is None:
                    self._runtime_type_error(out, f"bad operand type for unary {operator}: '{_ucls}'")
                    types[result] = "int"
                    return out
                _utarget = _name(_uhit + "____" + _dunder.lstrip("_"))
                if self.function_frame_abi.get(_uhit + "____" + _dunder.lstrip("_"), False):
                    out.append(f'    {{long _uu[]={{ {self._value(args[1])} }}; {_name(result)}=piton_frame_call((long)&{_utarget},1,_uu);}}')
                else:
                    out.append(f"    {_name(result)}={_utarget}({self._value(args[1])});")
                types[result] = "int"
                return out
            if types.get(args[1]) == "float":
                if operator == "-":
                    out.append(f"    {_name(result)}=piton_float_neg({self._value(args[1])});")
                    types[result] = "float"
                    operand_const = self._fn_consts.get(args[1]) if isinstance(args[1], str) else None
                    if isinstance(operand_const, float):
                        self._fn_consts[result] = -operand_const
                    return out
                if operator == "+":
                    out.append(f"    {_name(result)}={self._value(args[1])};")
                    types[result] = "float"
                    return out
                raise NativeBuildError(f"Linux float unary operator not supported: {operator}")
            if types.get(args[1]) == "complex":
                # COMPLEX_V1: -(1+2j) = (-1-2j); + es identidad (~ es TypeError).
                if operator == "-":
                    out.append(f"    {_name(result)}=piton_cx_neg({self._value(args[1])});")
                    types[result] = "complex"
                    operand_const = self._fn_consts.get(args[1]) if isinstance(args[1], str) else None
                    if isinstance(operand_const, complex):
                        self._fn_consts[result] = -operand_const
                    return out
                if operator == "+":
                    out.append(f"    {_name(result)}={self._value(args[1])};")
                    types[result] = "complex"
                    operand_const = self._fn_consts.get(args[1]) if isinstance(args[1], str) else None
                    if isinstance(operand_const, complex):
                        self._fn_consts[result] = operand_const
                    return out
                raise NativeBuildError(f"Linux complex unary operator not supported: {operator}")
            out.append(f"    {_name(result)}={operator}{self._value(args[1])};")
            types[result] = "bool" if operator == "!" else "int"
            # INTOVF_GUARD_V1 / FLOAT_POW_V1: record trivially-foldable
            # unary int/float results so downstream folds see through `-1`
            # (e.g. `2 ** -1`) and `-2.0` (e.g. `(-2.0) ** 0.5`).
            if operator in {"+", "-"}:
                operand_const = self._fn_consts.get(args[1]) if isinstance(args[1], str) else None
                if isinstance(operand_const, bool):
                    pass
                elif isinstance(operand_const, (int, float)):
                    self._fn_consts[result] = operand_const if operator == "+" else -operand_const
        elif op == "binary":
            operator, left, right = args[0], args[1], args[2]
            # ZDIV_GUARD_V1: mir attaches the innermost try handler as an
            # optional 4th argument on '//' and '%' so the raise below is
            # catchable by intentar/excepto.
            handler_label = args[3] if len(args) > 3 else None
            # IAUG_ASSIGN_V1: el MIR marca `p += 1` con un 5to arg 'aug'.
            is_aug = len(args) > 4 and args[4] == "aug"
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
            if operator == "+" and is_aug and left_type.startswith("object:"):
                # CPython prueba __iadd__ ANTES de __add__/__radd__ en
                # augmented assignment; sin __iadd__ cae al flujo normal.
                _ia_cls = left_type.split(":", 1)[1]
                _ia_hit = None
                for _cand in self.class_mro.get(_ia_cls, []):
                    if "__iadd__" in self.classes.get(_cand, set()):
                        _ia_hit = _cand
                        break
                if _ia_hit is None and "__iadd__" in self.classes.get(_ia_cls, set()):
                    _ia_hit = _ia_cls
                if _ia_hit is not None:
                    _ia_target = _name(_ia_hit + "__" + "__iadd__")
                    _ia_args = ",".join(self._value(v) for v in (left, right))
                    if self.function_frame_abi.get(_ia_hit + "__" + "__iadd__", False):
                        out.append(f'    {{long _ia[]={{ {_ia_args} }}; {_name(result)}=piton_frame_call((long)&{_ia_target},2,_ia);}}')
                    else:
                        out.append(f"    {_name(result)}={_ia_target}({_ia_args});")
                    types[result] = self.function_return_types.get(_ia_target, "int")
                    return out
            if operator == "in":
                # CONTAINS_V1: membership; mir lowers `a in b` here and
                # `a no en b` as a compare op that reuses the same helper.
                # Must run before the str/collection branches: they treat
                # 'in' as an unsupported str/collection operator.
                self._emit_contains(out, result, left, right, types, negate=False,
                                     function=function, handler_label=handler_label)
                return out
            _byteish = {"bytes", "bytearray"}
            if left_type in _byteish or right_type in _byteish:
                if operator == "+" and left_type in _byteish and right_type in _byteish:
                    if left_type == "bytes":
                        out.append(f"    {_name(result)}=(long)piton_bytes_concat((PitonBytes*){self._value(left)},(PitonBytes*){self._value(right)});")
                    else:
                        out.append(f"    {_name(result)}=(long)piton_bytearray_concat((PitonByteArray*){self._value(left)},(PitonByteArray*){self._value(right)});")
                    types[result] = left_type
                    return out
                if operator == "*" and (left_type in _byteish) != (right_type in _byteish):
                    seq, cnt = (left, right) if left_type in _byteish else (right, left)
                    seq_type = left_type if left_type in _byteish else right_type
                    if types.get(cnt, "int") not in {"int", "bool"}:
                        raise NativeBuildError(f"Linux {seq_type} repeat requires an int count")
                    if seq_type == "bytes":
                        out.append(f"    {_name(result)}=(long)piton_bytes_repeat((PitonBytes*){self._value(seq)},{self._value(cnt)});")
                    else:
                        out.append(f"    {_name(result)}=(long)piton_bytearray_repeat((PitonByteArray*){self._value(seq)},{self._value(cnt)});")
                    types[result] = seq_type
                    return out
                if operator in {"==", "!=", "<", "<=", ">", ">="} and left_type in _byteish and right_type in _byteish:
                    pass  # handled by the shared comparison path below
                elif operator not in {"==", "!="}:
                    # MIXED_BYTE_V1: byteish + desconocido (int por inferencia
                    # de tupla desestructurada) se resuelve en runtime; el
                    # helper levanta TypeError si realmente no es byteish.
                    if left_type == "int" and right_type in _byteish and operator == "+":
                        out.append(f"    {_name(result)}=(long)piton_bytes_concat_dyn((PitonSlot){{{self._value(left)},PK_INT}},(PitonBytes*){self._value(right)});")
                        types[result] = "bytes"
                        return out
                    if right_type == "int" and left_type in _byteish and operator == "+":
                        out.append(f"    {_name(result)}=(long)piton_bytes_concat_dyn((PitonBytes*){self._value(left)},(PitonSlot){{{self._value(right)},PK_INT}});")
                        types[result] = "bytes"
                        return out
                    raise NativeBuildError(
                        f"Linux unsupported operand type(s) for {operator}: '{left_type}' and '{right_type}'"
                    )
            if "str" in {left_type, right_type}:
                if operator == "+":
                    out.append(f"    {_name(result)}=(long)piton_str_concat((const char*){self._value(left)},(const char*){self._value(right)});")
                    types[result] = "str"
                    return out
                if operator == "*":
                    # STR_REPEAT_V1: 'ab' * 3 (either order); CPython yields
                    # '' for non-positive counts.
                    if left_type == "str" and right_type in {"int", "bool"}:
                        str_side, times_side = left, right
                    elif right_type == "str" and left_type in {"int", "bool"}:
                        str_side, times_side = right, left
                    else:
                        str_side, times_side = None, None
                    if str_side is not None:
                        out.append(f"    {_name(result)}=(long)piton_str_repeat((const char*){self._value(str_side)},{self._value(times_side)});")
                        types[result] = "str"
                        return out
                if operator == "%":
                    # PCT_FORMAT_V1 (+P14 width/precision/mapping/hex-octal):
                    # "%..." % args lowers through the {} engine after static
                    # translation. The template must be a literal; tuple
                    # arguments must have statically known elements (tuples
                    # are immutable); mapping arguments must be inline dict
                    # literals with literal str keys, resolved at build time
                    # (dicts built any other way fail closed). Anything else
                    # fails closed. Placeholder/argument count mismatches fail
                    # closed at build (CPython raises TypeError at runtime —
                    # the static subset rejects statically instead). Element
                    # types resolve at USE time: a variable can be reassigned
                    # after the tuple/dict is built, so SSA temps and aliases
                    # read the current static type while frozen literals keep
                    # their recorded type. Anything flowing from a parameter
                    # fails closed, like the single-value path below.
                    template = self._fn_consts.get(left)
                    if not isinstance(template, str):
                        # IDENTITY_FN_V1: el operando izquierdo puede ser el
                        # resultado de una funcion identidad (gettext stub)
                        # llamada con un literal — la plantilla es ese literal.
                        template = self._identity_templates.get(left)
                    if not isinstance(template, str):
                        # PCT_DYNAMIC_TEMPLATE_V1: plantilla no literal
                        # (variable, atributo, etc.). Usar piton_str_percent_dyn
                        # en runtime: solo valido si la plantilla tiene solo %s
                        # y el argumento (o primer elemento de tupla) es str;
                        # si no, TypeError claro en runtime.
                        _right_val = self._value(right)
                        _right_type = types.get(right, "")
                        if _right_type == "tuple":
                            # Tupla de un elemento: extraer el primero en runtime.
                            _right_val = f"piton_seq_get((PitonSeq*){_right_val},0).bits"
                            _right_type = "str"
                        if types.get(left) in {"str", ""} and _right_type in {"str", "", "int", "bool", "none"}:
                            out.append(f"    {{const char*_tmpl=(const char*){self._value(left)};")
                            out.append(f"    const char*_arg=(const char*)piton_str_of_value({_right_val});")
                            out.append(f"    if(!_tmpl||!_arg){{piton_raise_set(\"TypeError\",\"%s argument must be str\");piton_report_unhandled();piton_exit(1);}}")
                            out.append(f"    {_name(result)}=(long)piton_str_percent_dyn((long)_tmpl,(long)_arg);")
                            out.append("    }")
                            types[result] = "str"
                            return out
                        raise NativeBuildError("Linux str % formatting requires a literal template")
                    translated, fields = self._parse_percent_template(template)
                    has_map = any(field[0] is not None for field in fields)
                    has_pos = any(field[0] is None for field in fields)
                    if has_map and has_pos:
                        raise NativeBuildError("Linux str % formatting cannot mix positional and mapping conversions")

                    def _resolve_etype(item, recorded):
                        if isinstance(item, str) and item.startswith("%"):
                            source = aliases.get(item, item)
                            if source in function.params or item in function.params:
                                # PARAM_TYPE_V1: el tipo del param puede ser
                                # conocido (seed de uso o tipo del call-site)
                                # -> usarlo en vez de fallar cerrado.
                                # PCT_UNKNOWN_ARG_V1: si NO se conoce, ya no
                                # falla cerrado: la conversion %s acepta
                                # cualquier valor (str() de runtime), que es
                                # lo que CPython hace con `'%s' % x`.
                                _pt = types.get(source) or types.get(item)
                                if _pt and _pt not in {"int", "bool", "none", ""}:
                                    return _pt
                                return ""
                            return types.get(item, recorded)
                        return recorded

                    operands: list[str] = []
                    etypes: list[str] = []
                    if has_map:
                        if types.get(right) != "dict":
                            raise NativeBuildError("Linux str % mapping requires a dict argument")
                        record = self._dict_elems.get(right)
                        if record is None:
                            record = self._dict_elems.get(aliases.get(right, right))
                        if record is None:
                            raise NativeBuildError("Linux str % mapping requires a dict of statically known keys")
                        for name, _c, _f, _w, _pr in fields:
                            hit = record.get(name)
                            if hit is None:
                                raise NativeBuildError(f"Linux str % mapping: key {name!r} is not statically known")
                            operands.append(self._value(hit[0]))
                            etypes.append(_resolve_etype(hit[0], hit[1]))
                    elif types.get(right) == "tuple":
                        known = self._tuple_elems.get(right)
                        if known is None:
                            known = self._tuple_elems.get(aliases.get(right, right))
                        if known is None:
                            raise NativeBuildError("Linux str % formatting requires a tuple of statically known length")
                        for index, (item, etype0) in enumerate(known):
                            operands.append(
                                f"piton_seq_get((PitonSeq*){self._value(right)},{index}).bits"
                            )
                            etypes.append(_resolve_etype(item, etype0))
                    else:
                        # PCT_UNKNOWN_ARG_V1: un parametro como operando
                        # escalar ya no falla cerrado — con %s el str() de
                        # runtime (piton_str_of_value) cubre el tipo
                        # desconocido. La misma politica que _resolve_etype.
                        operands = [self._value(right)]
                        etypes = [types.get(right, "")]
                    if len(fields) != len(operands):
                        self._runtime_type_error(out, "not all arguments converted during string formatting")
                        types[result] = "str"
                        return out
                    bases = []
                    pads: list[tuple | None] = []
                    for index, (field, operand, arg_type) in enumerate(zip(fields, operands, etypes)):
                        _fname, spec, flags, width, prec = field
                        if spec == "s":
                            if arg_type in {"", "int", "none"}:
                                # PCT_UNKNOWN_ARG_V1: tipo desconocido (o
                                # escalar entero) -> str() de runtime. Para int
                                # es exacto; para un puntero a str el helper lo
                                # detecta; si el valor es otra cosa, el helper
                                # falla cerrado en vez de imprimir un puntero
                                # crudo (el fail-open historico).
                                # bool NO entra aqui: '%s' % True es "True",
                                # no "1" (CPython), y el helper no lo distingue
                                # de un entero.
                                base = f"piton_str_of_value({operand})"
                            elif arg_type == "complex":
                                base = f"piton_complex_str({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_float({operand})"
                            elif arg_type == "bool":
                                base = f'({operand}?"True":"False")'
                            elif arg_type == "none":
                                base = '"None"'
                            elif arg_type == "str":
                                base = f"((const char*){operand})"
                            elif arg_type in {"list", "tuple"}:
                                # STR_SEQ_V1: el str() de una secuencia es su
                                # repr ("(a, b)") como en CPython.
                                base = f"piton_str_from_seq((PitonSeq*){operand})"
                            elif arg_type.startswith(("object:", "class:")):
                                # STR_OBJECT_V1: el str() de un objeto es su
                                # repr ("<class 'X'>" para una clase).
                                _ocls = arg_type.split(":", 1)[1]
                                base = (
                                    f'(piton_object_repr_hook({operand},"{_ocls}")'
                                    f'?"":piton_str_quote("<object>"))'
                                )
                            else:
                                raise NativeBuildError(f"Linux str %s does not support {arg_type} arguments")
                        elif spec == "d":
                            if arg_type in {"int", "bool", ""}:
                                # PCT_UNKNOWN_ARG_V1: tipo desconocido ->
                                # piton_str_from_int asume que el valor es
                                # int-compatible en runtime (CPython lanza
                                # TypeError si no lo es).
                                base = f"piton_str_from_int({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_int((long)piton_bits_double({operand}))"
                            else:
                                raise NativeBuildError(f"Linux str %d requires a real number, not {arg_type}")
                        elif spec in {"f", "F", "e", "E", "g", "G"}:
                            precv = prec if prec >= 0 else 6
                            if arg_type not in {"float", "int", "bool", ""}:
                                raise NativeBuildError(f"Linux %{spec} requires int or float, not {arg_type}")
                            _foldv = f"({operand})" if arg_type == "float" else f"piton_double_bits((double){operand})"
                            if spec in {"f", "F"}:
                                base = f"piton_float_fmt_fixed({_foldv},{precv})"
                            elif spec in {"e", "E"}:
                                base = f"piton_float_fmt_exp({_foldv},{precv},{1 if spec in {'E', } else 0})"
                            else:
                                base = f"piton_float_fmt_g({_foldv},{1 if spec == 'G' else 0})"
                            isnum = 0
                        elif spec == "r":
                            if arg_type == "complex":
                                base = f"piton_complex_str({operand})"
                            elif arg_type == "str":
                                base = f"piton_str_quote((const char*){operand})"
                            elif arg_type == "bool":
                                base = f'({operand}?"True":"False")'
                            elif arg_type in {"int", ""}:
                                # PCT_REPR_UNKNOWN_V1: un entero, y sobre todo
                                # un parametro (que vale "int" por defecto pero
                                # en runtime puede no serlo), se resuelven con
                                # el repr de runtime: la misma politica que
                                # `%s`, que ya trata "int" como desconocido.
                                base = f"piton_repr_of_value({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_float({operand})"
                            elif arg_type == "none":
                                base = '"None"'
                            elif arg_type in {"list", "tuple"}:
                                # PCT_REPR_SEQ_V1: `%r` de una secuencia es su
                                # repr (mismo helper que `%s`, que ya imprime
                                # con las marcas de lista/tupla).
                                base = f"piton_str_from_seq((PitonSeq*){operand})"
                            elif arg_type.startswith("object:"):
                                # PCT_REPR_OBJECT_V1: el repr de una
                                # instancia usa el hook __repr__ del runtime
                                # (si la clase no lo define, "<object>").
                                _cls = arg_type.split(":", 1)[1]
                                base = (
                                    f'(piton_object_repr_hook({operand},"{_cls}")'
                                    f'?"":piton_str_quote("<object>"))'
                                )
                            else:
                                raise NativeBuildError(f"Linux str %r does not support {arg_type} arguments")
                        elif spec == "c":
                            if prec >= 0:
                                raise NativeBuildError("Linux str %c does not support precision")
                            if arg_type in {"int", "bool"}:
                                base = f"piton_percent_chr({operand})"
                            elif arg_type == "str":
                                base = f"piton_str_single_char((const char*){operand})"
                            else:
                                raise NativeBuildError(f"Linux str %c requires int or 1-character str, not {arg_type}")
                        elif spec in {"x", "X", "o"}:
                            if arg_type not in {"int", "bool"}:
                                raise NativeBuildError(f"Linux str %{spec} requires an int, not {arg_type}")
                            base_num = 16 if spec in {"x", "X"} else 8
                            base_inner = f"piton_str_from_int_base({operand},{base_num},{1 if spec == 'X' else 0})"
                            # PCT_ALT_FLAG_V1: bandera # agrega prefijo 0x/0X/0o
                            if flags & 4:
                                if spec == "o":
                                    prefix = '"0o"'
                                elif spec == "x":
                                    prefix = '"0x"'
                                else:
                                    prefix = '"0X"'
                                base = f"(const char*)piton_str_concat({prefix},(const char*){base_inner})"
                            else:
                                base = base_inner
                        else:
                            raise NativeBuildError(f"Linux str %{spec} is not supported")
                        bases.append(f"const char*_pb{index}=(const char*){base};")
                        if width >= 0 or prec >= 0:
                            isnum = 1 if spec in {"d", "x", "X", "o", "f", "F", "e", "E", "g", "G"} else 0
                            pads.append((width, prec if spec in {"d", "x", "X", "o", "s", "r", "c"} else -1, flags, isnum))
                        else:
                            pads.append(None)
                    names = ",".join(f"_pp{index}" for index in range(len(operands))) or "_pp0"
                    if not operands:
                        bases.append('const char*_pb0="";')
                        pads.append(None)
                    # a raising conversion (piton_chr out of range,
                    # single_char on a long string) yields NULL: route after
                    # EACH base so the pad/format engine never dereferences
                    # it (CPython evaluates left to right, first error wins
                    # — same order here).
                    out.append("    {")
                    for index, piece in enumerate(bases):
                        out.append(f"    {piece}")
                        out.append("    if(piton_exc_flag){")
                        if handler_label:
                            out.append(f"        goto {_name(function.name + '_' + handler_label)};")
                        else:
                            out.append("        piton_report_unhandled();piton_exit(1);")
                        out.append("    }")
                        pad = pads[index]
                        if pad is None:
                            out.append(f"    const char*_pp{index}=(const char*)_pb{index};")
                        else:
                            width, prec, flags, isnum = pad
                            out.append(f"    const char*_pp{index}=(const char*)piton_str_pad((const char*)_pb{index},{width},{prec},{flags},{isnum});")
                    out.append(f"    {{const char*_pa[]={{{names}}}; {_name(result)}=(long)piton_str_format({_str_c_literal(translated)},{len(operands)},_pa);}}")
                    self._emit_exc_check(out, function, handler_label)
                    out.append("    }")
                    types[result] = "str"
                    return out
                self._runtime_type_error(out, "unsupported operand type(s) for " + f"{operator}" + ": '" + f"{left_type}" + "' and '" + f"{right_type}" + "'")
                types[result] = "int"
                return out
            _setlike = {"set", "set:frozen"}
            if left_type in _setlike and right_type in _setlike and operator in {"|", "&", "-", "^"}:
                # SET_OPS_V1: | union, & interseccion, - diferencia,
                # ^ diferencia simetrica. Los helpers ya existían para los
                # metodos; los OPERADORES caían al TypeError genérico.
                helper = {"|": "piton_set_union", "&": "piton_set_intersection",
                          "-": "piton_set_difference", "^": "piton_set_sym_diff"}[operator]
                if left_type == "set:frozen" and right_type == "set:frozen":
                    out.append(f"    {{PitonSet* _fs={helper}((PitonSet*){self._value(left)},(PitonSet*){self._value(right)}); _fs->kind=PK_FROZENSET; {_name(result)}=(long)_fs;}}")
                    types[result] = "set:frozen"
                else:
                    out.append(f"    {_name(result)}={helper}((PitonSet*){self._value(left)},(PitonSet*){self._value(right)});")
                    types[result] = "set"
                return out
            if operator == "|" and left_type == "dict" and right_type == "dict":
                # DICT_MERGE_V1: {..} | {..} une dicts (CPython 3.9+).
                out.append(f"    {{PitonDict* _dm=(PitonDict*)piton_dict_copy((PitonDict*){self._value(left)}); if(_dm)piton_dict_update(_dm,(PitonDict*){self._value(right)}); {_name(result)}=(long)_dm;}}")
                types[result] = "dict"
                return out
            if {left_type, right_type} & {"list", "tuple", "dict", "set"}:
                # SEQ_CONCAT_V1: list+list / tuple+tuple concatenate; any other
                # collection arithmetic is a CPython TypeError — fail closed at
                # build time instead of doing C pointer arithmetic (previously
                # printed a raw pointer as an int).
                if operator == "+" and left_type == right_type and left_type in {"list", "tuple"}:
                    out.append(f"    {_name(result)}=piton_seq_concat((PitonSeq*){self._value(left)},(PitonSeq*){self._value(right)});")
                    types[result] = left_type
                    return out
                if operator == "*" and {left_type, right_type} == {"list", "int"}:
                    # COLL_REPEAT_V1: list * n (either order). CPython
                    # yields [] for non-positive counts.
                    seq_side, times = (left, right) if left_type == "list" else (right, left)
                    out.append(f"    {_name(result)}=(long)piton_seq_repeat_n((PitonSeq*){self._value(seq_side)},{self._value(times)});")
                    types[result] = "list"
                    return out
                if operator == "*" and {left_type, right_type} == {"tuple", "int"}:
                    seq_side, times = (left, right) if left_type == "tuple" else (right, left)
                    out.append(f"    {_name(result)}=(long)piton_seq_repeat_n((PitonSeq*){self._value(seq_side)},{self._value(times)});")
                    types[result] = "tuple"
                    # TUPLE_REPEAT_LEN_V1: la tupla repetida hereda longitud
                    # (n * len) y elementos del original. Sin esto
                    # `"%s() x %s()" % ((name,)*2)` fallaba en build con "tuple
                    # of statically known length": el formato necesita la
                    # aridad, y la repeticion la dejaba sin registrar.
                    _base = self._tuple_elems.get(seq_side)
                    if _base is None:
                        _base = self._tuple_elems.get(aliases.get(seq_side, seq_side))
                    _times = self._fn_consts.get(times)
                    if not isinstance(_times, int) or isinstance(_times, bool):
                        _times = None
                    if _base is not None and result:
                        _n = len(_base) * _times if (_times is not None and _times > 0) else (
                            0 if (_times is not None and _times <= 0) else None
                        )
                        if _n is not None:
                            self._tuple_elems[result] = tuple(_base) * _n
                    return out
                self._runtime_type_error(out, "unsupported operand type(s) for " + f"{operator}" + ": '" + f"{left_type}" + "' and '" + f"{right_type}" + "'")
                types[result] = "int"
                return out
            if "complex" in {left_type, right_type}:
                # COMPLEX_V1: aritmética de complejos contra int/float/complex.
                # CPython mezcla libremente real+complejo; // % ** y los turnos
                # bit a bit son TypeError y fallan cerrados en build.
                if operator == "**" and right_type in {"int", "bool"}:
                    left_cx2 = self._complex_operand(left, left_type)
                    out.append(f"    {_name(result)}=piton_cx_pow_int({self._value(left)},{self._value(right)});")
                    self._emit_exc_check(out, function, handler_label)
                    types[result] = "complex"
                    return out
                if operator not in {"+", "-", "*", "/"}:
                    raise NativeBuildError(f"Linux complex binary operator not supported: {operator}")
                left_cx = self._complex_operand(left, left_type)
                right_cx = self._complex_operand(right, right_type)
                if left_cx is None or right_cx is None:
                    raise NativeBuildError(f"Linux {operator} with a non-numeric operand is not supported")
                helper = {"+": "piton_cx_add", "-": "piton_cx_sub", "*": "piton_cx_mul", "/": "piton_cx_div"}[operator]
                out.append(f"    {_name(result)}={helper}({left_cx},{right_cx});")
                if operator == "/":
                    self._emit_exc_check(out, function, handler_label)
                types[result] = "complex"
                return out
            if "none" in {left_type, right_type}:
                # WRETURNTYPE_V1 (mirrors the Windows guard): None in
                # arithmetic is a CPython TypeError, not pointer math.
                raise NativeBuildError(
                    f"Linux {operator} with None is not supported (CPython raises TypeError)"
                )
            if "float" in {left_type, right_type}:
                # TRUEDIV_V1: float / and // join the supported set. CPython
                # raises ZeroDivisionError on float division by zero instead
                # of yielding IEEE infinities, so the helpers raise through
                # the native exception machinery (handler-routed by the
                # caller's exc check). Float % goes through an exact
                # software fmod (bigint-backed) with the CPython sign
                # adjustment — no libm under -nostdlib.
                if operator not in {"+", "-", "*", "/", "//", "%", "**"}:
                    raise NativeBuildError(f"Linux float binary operator not supported: {operator}")
                left_value = self._value(left)
                right_value = self._value(right)
                if left_type != "float":
                    left_value = f"piton_double_bits((double){left_value})"
                if right_type != "float":
                    right_value = f"piton_double_bits((double){right_value})"
                if operator == "**":
                    # FLOAT_POW_V1: exact for y in {-2,-1,-0.5,0,0.5,1,2}
                    # (see piton_float_pow_v1); constant operands fold exactly
                    # in Python (correctly-rounded pow IS the oracle).
                    left_const = self._fn_consts.get(left) if isinstance(left, str) else None
                    right_const = self._fn_consts.get(right) if isinstance(right, str) else None
                    if (
                        isinstance(left_const, (int, float)) and not isinstance(left_const, bool)
                        and isinstance(right_const, (int, float)) and not isinstance(right_const, bool)
                    ):
                        try:
                            folded = left_const ** right_const
                        except (OverflowError, ZeroDivisionError):
                            folded = None
                        if isinstance(folded, complex):
                            raise NativeBuildError("Linux float ** with negative base and fractional exponent yields complex (not supported)")
                        if isinstance(folded, float):
                            self._fn_consts[result] = folded
                            out.append(f"    {_name(result)}=piton_double_bits({_float_c_literal(folded)});")
                            types[result] = "float"
                            return out
                    out.append(f"    {_name(result)}=piton_float_pow_v1({left_value},{right_value});")
                    self._emit_exc_check(out, function, handler_label)
                    types[result] = "float"
                    return out
                helper = {"+": "piton_float_add", "-": "piton_float_sub", "*": "piton_float_mul",
                          "/": "piton_float_div", "//": "piton_float_floor_div", "%": "piton_float_mod"}[operator]
                out.append(f"    {_name(result)}={helper}({left_value},{right_value});")
                if operator in {"/", "//", "%"}:
                    self._emit_exc_check(out, function, handler_label)
                types[result] = "float"
                return out
            if left_type == "bigint" or right_type == "bigint":
                func = {"+": "piton_bigint_add", "-": "piton_bigint_sub", "*": "piton_bigint_mul",
                        "//": "piton_bigint_floor_div", "%": "piton_bigint_mod"}.get(operator)
                if not func:
                    raise NativeBuildError(f"Linux bigint binary operator not supported: {operator}")
                if left_type == "bigint" and right_type == "bigint":
                    out.append(f"    {_name(result)}=(long){func}((void*){_name(left)},(void*){_name(right)});")
                elif left_type == "bigint":
                    out.append(f"    {_name(result)}=(long){func}((void*){_name(left)},piton_bigint_from_i64({self._value(right)}));")
                else:
                    out.append(f"    {_name(result)}=(long){func}(piton_bigint_from_i64({self._value(left)}),(void*){_name(right)});")
                types[result] = "bigint"
                bigint_slots.append(result)
                return out
            if operator == "/" and "float" not in {left_type, right_type}:
                # TRUEDIV_V1: CPython int / int yields a double; both CPython
                # and the helper convert the i64 operands to double first, so
                # the results agree for the whole untagged subset. Division
                # by zero raises a catchable ZeroDivisionError.
                left_const = self._fn_consts.get(left) if isinstance(left, str) else None
                right_const = self._fn_consts.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                    and right_const != 0
                ):
                    folded = left_const / right_const  # Python true division IS the oracle
                    self._fn_consts[result] = folded
                    out.append(f"    {_name(result)}=piton_double_bits({_float_c_literal(folded)});")
                    types[result] = "float"
                    return out
                out.append(f"    {_name(result)}=piton_int_truediv({self._value(left)},{self._value(right)});")
                self._emit_exc_check(out, function, handler_label)
                types[result] = "float"
                return out
            if operator in {"//", "%"}:
                # ZDIV_GUARD_V1: CPython raises ZeroDivisionError; the bare C
                # division used to trap the process (SIGFPE/SIGILL). Raise the
                # native exception and route to the enclosing try handler.
                helper = "piton_floor_div" if operator == "//" else "piton_mod"
                out.append(
                    f'    if({self._value(right)}==0){{piton_raise_set("ZeroDivisionError","integer division or modulo by zero");}}'
                )
                out.append(f"    else{{{_name(result)}={helper}({self._value(left)},{self._value(right)});}}")
                self._emit_exc_check(out, function, handler_label)
                types[result] = "int"
                return out
            if operator == "**" and {left_type, right_type} <= {"int", "bool"}:
                # INT_POW_V1: CPython int ** int is exact arbitrary precision
                # for non-negative exponents. Constant operands fold exactly
                # (bounded: astronomic exponents stay on the runtime path);
                # runtime operands use square-and-multiply over the bigint
                # helpers. A runtime negative exponent cannot produce a
                # bigint in the untagged model, so it raises a catchable
                # ValueError instead of corrupting (folded literal
                # `2 ** -1` still yields the exact 0.5).
                left_const = self._fn_consts.get(left) if isinstance(left, str) else None
                right_const = self._fn_consts.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                    and 0 <= right_const <= 1000000
                ):
                    try:
                        folded = left_const ** right_const
                    except ZeroDivisionError:
                        raise NativeBuildError("Linux int ** with zero base and negative exponent is not supported")
                    if -(2 ** 63) <= folded < 2 ** 63:
                        # small results stay plain ints (better downstream:
                        # indexing, int arithmetic, no bigint prelude needed)
                        self._fn_consts[result] = folded
                        out.append(f"    {_name(result)}=(long)({folded});")
                        types[result] = "int"
                    else:
                        self._fn_consts.pop(result, None)
                        out.append(f"    {_name(result)}=(long)piton_bigint_from_str({json.dumps(str(folded))});")
                        types[result] = "bigint"
                        bigint_slots.append(result)
                    return out
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                    and right_const < 0
                ):
                    if left_const == 0:
                        raise NativeBuildError("Linux int ** with zero base and negative exponent is not supported")
                    folded = left_const ** right_const
                    self._fn_consts[result] = folded
                    out.append(f"    {_name(result)}=piton_double_bits({_float_c_literal(folded)});")
                    types[result] = "float"
                    return out
                # INT_POW_V1: pre-zero the slot — the raise path below
                # skips assignment, and the function-exit cleanup frees every
                # bigint_slots entry (an uninitialized slot freed a garbage
                # pointer: heap corruption, Windows-only crash).
                out.append(f"    {_name(result)}=0;")
                out.append(
                    f'    if({self._value(right)}<0){{piton_raise_set("ValueError","negative exponent requires a float result (out of the int subset)");}}'
                )
                out.append(f"    else{{{_name(result)}=(long)piton_bigint_pow_small({self._value(left)},{self._value(right)});}}")
                self._emit_exc_check(out, function, handler_label)
                types[result] = "bigint"
                bigint_slots.append(result)
                return out
            if operator in {"+", "-", "*"}:
                # DUNDER_ARITH_V1: `P() + 1` used to emit a raw integer add of
                # the object POINTER, printing an address. Like `==` dispatches
                # to `__eq__`, arithmetic dispatches to the dunder (MRO
                # resolved), with the reflected method as fallback.
                dunder, rdunder = {"+": ("__add__", "__radd__"), "-": ("__sub__", "__rsub__"), "*": ("__mul__", "__rmul__")}[operator]
                for _side, _cls, _meth, _swap in (
                    (left_type, left_type.split(":", 1)[1] if left_type.startswith("object:") else None, dunder, False),
                    (right_type, right_type.split(":", 1)[1] if right_type.startswith("object:") else None, rdunder, True),
                ):
                    if _cls is None:
                        continue
                    hit = None
                    for candidate in self.class_mro.get(_cls, []):
                        if _meth in self.classes.get(candidate, set()):
                            hit = candidate
                            break
                    if hit is None and _meth in self.classes.get(_cls, set()):
                        hit = _cls
                    if hit is None:
                        continue
                    target = _name(hit + "__" + _meth)
                    ordered = (right, left) if _swap else (left, right)
                    frame_args = ",".join(self._value(v) for v in ordered)
                    if self.function_frame_abi.get(hit + "__" + _meth, False):
                        out.append(f'    {{long _ee_args[]={{ {frame_args} }}; {_name(result)}=piton_frame_call((long)&{target},2,_ee_args);}}')
                    else:
                        out.append(f"    {_name(result)}={target}({frame_args});")
                    types[result] = self.function_return_types.get(target, "int")
                    return out
                if left_type.startswith("object:") or right_type.startswith("object:"):
                    raise NativeBuildError(
                        f"Linux '{operator}' between '{left_type}' and '{right_type}' is not supported "
                        f"(no {dunder}/{rdunder} found on the class)"
                    )
                # INTOVF_GUARD_V1: CPython promotes to arbitrary precision on
                # overflow; the untagged i64 subset used to wrap silently.
                # Constant operands fold exactly (Python bignum IS the oracle)
                # and promote to bigint literals when the result exceeds i64;
                # runtime operands use checked helpers that raise a catchable
                # OverflowError instead of corrupting the value.
                left_const = self._fn_consts.get(left) if isinstance(left, str) else None
                right_const = self._fn_consts.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                ):
                    folded = {"+": left_const + right_const, "-": left_const - right_const, "*": left_const * right_const}[operator]
                    # record the folded value so chained folds keep propagating
                    self._fn_consts[result] = folded
                    if -(2 ** 63) <= folded < 2 ** 63:
                        out.append(f"    {_name(result)}=(long)({folded});")
                        types[result] = "int"
                    else:
                        out.append(f"    {_name(result)}=(long)piton_bigint_from_str({json.dumps(str(folded))});")
                        types[result] = "bigint"
                        bigint_slots.append(result)
                    return out
                helper = {"+": "piton_int_add", "-": "piton_int_sub", "*": "piton_int_mul"}[operator]
                out.append(f"    {_name(result)}={helper}({self._value(left)},{self._value(right)});")
                self._emit_exc_check(out, function, handler_label)
                types[result] = "int"
                return out
            if operator in {"&", "|", "^", "<<", ">>"}:
                if operator in {"<<", ">>"}:
                    # SHIFT_NEG_V1: CPython raises ValueError for a negative
                    # shift count; the raw C shift used to answer 0 (or UB on
                    # some targets) because the count was consumed as unsigned.
                    out.append(
                        f'    if({self._value(right)}<0){{piton_raise_set("ValueError","negative shift count");}}'
                    )
                    self._emit_exc_check(out, function, handler_label)
                expression = f"({self._value(left)} {operator} {self._value(right)})"
            elif operator == "@":
                # MATMUL_V1: a @ b -> a.__matmul__(b) via dynamic dispatch.
                # Resuelve el metodo en runtime; si no existe, AttributeError.
                _matmul_expr = (
                    f'    {{PitonObject*_m_obj=(PitonObject*){self._value(left)};'
                    f'    long _m_meth=piton_object_get(_m_obj,"__matmul__").bits;'
                    f'    PitonSeq*_m_args=piton_seq_new(PK_TUPLE,1);'
                    f'    _m_args->items[0]=piton_slot({self._value(right)},PK_INT);'
                    f'    {_name(result)}=piton_call_dynamic(_m_meth,(long)_m_args,0,0);}}'
                )
                out.append(_matmul_expr)
                types[result] = "int"
                return out
            else:
                raise NativeBuildError(f"Linux binary operator not supported: {operator}")
            out.append(f"    {_name(result)}={expression};")
            types[result] = "int"
        elif op == "compare":
            operator, left, right = args[0], args[1], args[2]
            # IN_GUARD_V1: 4to arg opcional = handler del intentar activo
            # (solo lo traen las comparaciones de pertenencia).
            compare_handler = args[3] if len(args) > 3 else None
            if operator in {"es", "no es"}:
                # IS_IDENTITY_V1: `es` is IDENTITY, not equality. The raw C `==`
                # answered True for `1 es Verdadero` (both are 1) where CPython
                # says False. Compare raw bits: for the untagged model that is
                # identity for the value types it can represent, and the
                # differing static types are identity-different anyway.
                # NONE_SINGLETON_V2: contra None la identidad es la direccion
                # del singleton, sin kinds. Los kinds impedian el caso central:
                # un parametro sin tipo estatico daba PK_INT a la izquierda y
                # PK_NONE a la derecha, falso por construccion. Para cualquier
                # otro par se mantiene la regla (que distingue `1 es
                # Verdadero`).
                if self._is_none_side(left, types) or self._is_none_side(right, types):
                    identity = f"(({self._value(left)})==({self._value(right)}))"
                else:
                    identity = (
                        f"(({self._value(left)})==({self._value(right)}))"
                        f"&&{self._kind(types.get(left,'int'))}=={self._kind(types.get(right,'int'))}"
                    )
                # IS_IDENTITY_V1: `x no es y` is the negation of identity, and it
                # had no dispatch branch at all, so the operator name reached the
                # C source and the compile failed.
                out.append(f"    {_name(result)}={'!' if operator == 'no es' else ''}({identity});")
                types[result] = "bool"
                return out
            if operator in {"en", "no en"}:
                # CONTAINS_V1: `a en b` and `a no en b` share the membership
                # lowering. The POSITIVE form was missing here: `x en y` fell
                # through to the generic comparison emitter, which wrote the
                # operator name into the C source and produced invalid C
                # ("expected ')' before 'en'"). The parity gate only covered
                # `no en`, so the hole survived; the enumerative corpus found it.
                self._emit_contains(out, result, left, right, types,
                                    negate=(operator == "no en"), function=function,
                                    handler_label=compare_handler)
                return out
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
            if "range" in {left_type, right_type}:
                # RANGE_VALUE_V1: a range only ever equals another range (exact
                # len/start/step with the empty-range rule); against anything
                # else CPython answers False for `==` and True for `!=`.
                # Ordering a range raises TypeError, like any incomparable pair.
                if operator in {"==", "!="}:
                    if left_type == right_type == "range":
                        out.append(f"    {_name(result)}=(piton_range_eq((PitonRange*){self._value(left)},(PitonRange*){self._value(right)}) {'==' if operator == '==' else '!='} 1);")
                    else:
                        out.append(f"    {_name(result)}={1 if operator == '!=' else 0};")
                    types[result] = "bool"
                    return out
                self._runtime_type_error(out, f"'{operator}' not supported between instances of 'range' and '{right_type}'")
                types[result] = "bool"
                return out
            if "complex" in {left_type, right_type}:
                # COMPLEX_V1: solo == y != (CPython: ordenar complejos es
                # TypeError). Un real contra complejo compara numéricamente
                # (1+0j == 1 es Verdadero en CPython).
                if operator not in {"==", "!="}:
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of 'complex' and '{right_type}'")
                    types[result] = "bool"
                    return out
                left_cx = self._complex_operand(left, left_type)
                right_cx = self._complex_operand(right, right_type)
                if left_cx is None or right_cx is None:
                    raise NativeBuildError(f"Linux {operator} with a non-numeric operand is not supported")
                neg = "!" if operator == "!=" else ""
                out.append(f"    {_name(result)}={neg}(piton_cx_eq({left_cx},{right_cx}));")
                types[result] = "bool"
                return out
            if operator == "==" and (left_type.startswith("object:") or right_type.startswith("object:")):
                owner_side = left_type if left_type.startswith("object:") else right_type
                cls_name = owner_side.split(":", 1)[1]
                eq_cls = None
                for candidate in self.class_mro.get(cls_name, []):
                    if "__eq__" in self.classes.get(candidate, set()):
                        eq_cls = candidate
                        break
                if eq_cls is None and "__eq__" in self.classes.get(cls_name, set()):
                    eq_cls = cls_name
                if eq_cls is not None:
                    target = _name(eq_cls + "__" + "__eq__")
                    frame_args = ",".join(self._value(v) for v in (left, right))
                    if self.function_frame_abi.get(eq_cls + "____eq__", False):
                        out.append(f'    {{long _ee_args[]={{ {frame_args} }}; {_name(result)}=piton_frame_call((long)&{target},2,_ee_args);}}')
                    else:
                        out.append(f"    {_name(result)}={target}({frame_args});")
                    types[result] = "bool"
                    return out
            if left_type in {"bytes", "bytearray"} or right_type in {"bytes", "bytearray"}:
                # BYTES_CMP_V1: content comparison across bytes/bytearray;
                # against any other type only ==/!= are defined.
                if left_type in {"bytes", "bytearray"} and right_type in {"bytes", "bytearray"}:
                    out.append(f"    {_name(result)}=(piton_bytes_cmp((PitonBytes*){self._value(left)},(PitonBytes*){self._value(right)}) {operator} 0);")
                elif operator in {"==", "!="}:
                    out.append(f"    {_name(result)}={1 if operator == '!=' else 0};")
                else:
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of '{left_type}' and '{right_type}'")
                types[result] = "bool"
                return out
            if left_type == "bigint" or right_type == "bigint":
                # BIGINT_CMP_MIXED_V1: a plain int is NOT a PitonBigInt*, so
                # the both-sides-pointer cast dereferenced it as a struct and
                # `10 ** 20 > 5` died with SIGSEGV. Route the mixed case to the
                # int-aware helper and reverse the sign when the int is left.
                if left_type == "bigint" and right_type == "bigint":
                    out.append(f"    {_name(result)}=(piton_bigint_cmp((void*){_name(left)},(void*){_name(right)}) {operator} 0);")
                elif left_type == "bigint":
                    out.append(f"    {_name(result)}=(piton_bigint_cmp_int((void*){_name(left)},{self._value(right)}) {operator} 0);")
                else:
                    out.append(f"    {_name(result)}=(-piton_bigint_cmp_int((void*){_name(right)},{self._value(left)}) {operator} 0);")
                types[result] = "bool"
                return out
            if "float" in {left_type, right_type}:
                left_value = f"piton_bits_double({self._value(left)})" if left_type == "float" else f"(double){self._value(left)}"
                right_value = f"piton_bits_double({self._value(right)})" if right_type == "float" else f"(double){self._value(right)}"
                out.append(f"    {_name(result)}=({left_value} {operator} {right_value});")
                types[result] = "bool"
                return out
            elif types.get(left) == types.get(right) == "str":
                expression = f"(piton_strcmp((char*){self._value(left)},(char*){self._value(right)}) {operator} 0)"
            elif left_type in {"list", "tuple"} and right_type in {"list", "tuple"}:
                # SEQ_CMP_V1: ==/!= por contenido y orden lexicográfico
                # ([1,2] < [1,3] es True en CPython, no un TypeError).
                if operator in {"==", "!="}:
                    neg = "" if operator == "==" else "!"
                    out.append(f"    {_name(result)}={neg}piton_slot_eq(piton_slot({self._value(left)},{self._kind(left_type)}),piton_slot({self._value(right)},{self._kind(right_type)}));")
                    types[result] = "bool"
                    return out
                if operator in {"<", "<=", ">", ">="}:
                    cmp_fn = "piton_seq_cmp"
                    out.append(f"    {{long _c={cmp_fn}((PitonSeq*){self._value(left)},(PitonSeq*){self._value(right)}); {_name(result)}=(_c {operator} 0);}}")
                    types[result] = "bool"
                    return out
                self._runtime_type_error(out, f"'{operator}' not supported between instances of '{left_type}' and '{right_type}'")
                types[result] = "bool"
                return out
            elif left_type in {"dict", "dict:module"} and right_type in {"dict", "dict:module"}:
                # DICT_EQ_V1: dicts por contenido ({'a':1} == {'a':1} es True).
                if operator not in {"==", "!="}:
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of 'dict' and 'dict'")
                    types[result] = "bool"
                    return out
                neg = "" if operator == "==" else "!"
                out.append(f"    {_name(result)}={neg}piton_dict_eq((PitonDict*){self._value(left)},(PitonDict*){self._value(right)});")
                types[result] = "bool"
                return out
            elif left_type == "memoryview" and right_type == "memoryview":
                # MEMORYVIEW_EQ_V1: vistas comparan byte a byte (CPython).
                if operator not in {"==", "!="}:
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of 'memoryview' and 'memoryview'")
                    types[result] = "bool"
                    return out
                neg = "" if operator == "==" else "!"
                out.append(f"    {_name(result)}={neg}piton_memoryview_eq((PitonMemoryView*){self._value(left)},(PitonMemoryView*){self._value(right)});")
                types[result] = "bool"
                return out
            elif left_type == "slice" and right_type == "slice":
                # SLICE_EQ_V1: slices comparan por bounds, no por puntero.
                if operator not in {"==", "!="}:
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of 'slice' and 'slice'")
                    types[result] = "bool"
                    return out
                neg = "" if operator == "==" else "!"
                out.append(f"    {_name(result)}={neg}piton_slice_eq((PitonSlice*){self._value(left)},(PitonSlice*){self._value(right)});")
                types[result] = "bool"
                return out
            elif left_type in {"set", "set:frozen"} and right_type in {"set", "set:frozen"}:
                # SET_EQ_V1: conjuntos se comparan por CONTENIDO, no por
                # puntero ({1,2} == frozenset({2,1}) es True en CPython).
                if operator in {"==", "!="}:
                    neg = "" if operator == "==" else "!"
                    out.append(f"    {_name(result)}={neg}piton_slot_eq(piton_slot({self._value(left)},{self._kind(left_type)}),piton_slot({self._value(right)},{self._kind(right_type)}));")
                    types[result] = "bool"
                    return out
                if operator in {"<=", "<"}:
                    expr = f"piton_set_issubset((PitonSet*){self._value(left)},(PitonSet*){self._value(right)})"
                else:
                    expr = f"piton_set_issubset((PitonSet*){self._value(right)},(PitonSet*){self._value(left)})"
                if operator in {"<", ">"}:
                    same = f"piton_slot_eq(piton_slot({self._value(left)},{self._kind(left_type)}),piton_slot({self._value(right)},{self._kind(right_type)}))"
                    expr = f"({expr}&&!{same})"
                out.append(f"    {_name(result)}={expr};")
                types[result] = "bool"
                return out
            else:
                if operator in {"<", ">", "<=", ">="} and not self._ordering_pair_ok(left_type, right_type):
                    # ORDER_MIXED_TYPES_V1: refuse instead of comparing the raw
                    # representations of two incomparable operands.
                    self._runtime_type_error(out, f"'{operator}' not supported between instances of '{left_type}' and '{right_type}'")
                    types[result] = "bool"
                    return out
                expression = f"({self._value(left)} {operator} {self._value(right)})"
            out.append(f"    {_name(result)}={expression};")
            types[result] = "bool"
        elif op == "branch":
            # TRUTHY_BRANCH_V1: the branch condition is a CPython truth test,
            # not a raw pointer/integer test. `si ""`/`si []`/`si {}` used to
            # take the TRUE branch because a non-null pointer is always
            # truthy. Types the truth table does not model (objects,
            # iterators, functions) keep the historical raw test instead of
            # newly failing closed.
            out.append(
                f"    if({self._truth_expr(args[0], types.get(args[0], 'int'), strict=False)}) "
                f"goto {_name(function.name + '_' + args[1])}; else goto {_name(function.name + '_' + args[2])};"
            )
        elif op == "jump":
            out.append(f"    goto {_name(function.name + '_' + args[0])};")
        elif op == "call":
            function_name = aliases.get(args[0], args[0])
            values = list(args[1])
            # BUILTIN_BOUND_METHOD_V1: una llamada a un `xs.append` ligado
            # tipa el elemento de la lista igual que el append directo (sin
            # esto, heapq no podia desestructurar `value, order, next = h[0]`).
            _bound_owner = self._builtin_bound.get(args[0]) or self._builtin_bound.get(function_name)
            if _bound_owner and values:
                _bv_type = types.get(values[0])
                if _bv_type and "|" not in _bv_type:
                    _cur = self._coll_elems.get(_bound_owner)
                    if _cur is None:
                        self._coll_elems[_bound_owner] = _bv_type
                    elif "|" not in _cur and _cur != _bv_type:
                        self._coll_elems[_bound_owner] = _cur + "|" + _bv_type
            call_handler = args[2] if len(args) > 2 else None
            # PRINT_END_KW_V1: `print(..., end=X)` — el terminador en vez de
            # "\n" (solo para la impresion; el resto de llamadas lo ignora).
            print_end = args[3] if len(args) > 3 else None
            # SHADOW_BUILTIN_V1: a user definition of the same name wins over
            # the builtin table (`funcion suma(a, b)` is not `sum`).
            if function_name in self.function_names and function_name not in _BUILTINS:
                return self._emit_user_function_call(out, result, function_name, values, call_handler, function, types)
            if function_name in {"imprimir", "print"}:
                if values:
                    # PRINT_ARGS_V1: CPython print(a, b, ...) str()s every
                    # positional argument and joins them with single spaces.
                    # Each backend printer is the *_raw (no newline) variant;
                    # one newline is written after the last argument.
                    for index, value in enumerate(values):
                        # PRINT_TYPE_DEFAULT_V1: "" (desconocido) -> print_auto,
                        # que infiere por el valor. Antes el default "int"
                        # imprimía punteros como enteros tras PRINT_INT_V1.
                        value_type = types.get(value, "")
                        if index:
                            out.append('    piton_write(piton_print_fd," ",1);')
                        # PRINT_UNPRINTABLE_V1: iterators, generators, closures,
                        # module markers and builtin markers can never match
                        # CPython (addresses / uninitialized C values) — fail
                        # closed instead of printing garbage.
                        if value_type == "builtin":
                            # BUILTIN_TYPE_REPR_V1: los constructores de tipo
                            # imprimen su <class '...'> estable; el resto sigue
                            # fail-closed (direcciones / marcadores sin valor).
                            _bname = aliases.get(value, value)
                            _bkind = {
                                "lista": "PK_LIST", "list": "PK_LIST",
                                "tupla": "PK_TUPLE", "tuple": "PK_TUPLE",
                                "diccionario": "PK_DICT", "dict": "PK_DICT",
                                "conjunto": "PK_SET", "set": "PK_SET",
                                "rango": "PK_RANGE", "range": "PK_RANGE",
                                "texto": "PK_STR", "str": "PK_STR",
                                "entero": "PK_INT", "int": "PK_INT",
                                "decimal": "PK_FLOAT", "float": "PK_FLOAT",
                                "booleano": "PK_BOOL", "bool": "PK_BOOL",
                            }.get(_bname)
                            if _bkind is not None:
                                out.append(f'    piton_print_str_raw(piton_type_repr({_bkind}));')
                                continue
                        if (
                            str(value_type).startswith("iterator:")
                            or value_type in {"generator", "genexpr", "builtin", "closure", "module", "cell"}
                        ):
                            raise NativeBuildError(
                                f"Linux native print of a {value_type} value is not supported "
                                "(can never match CPython output)"
                            )
                        if str(value_type).startswith("object:"):
                            cls_name = value_type.split(":", 1)[1]
                            str_cls = None
                            repr_cls = None
                            for candidate in self.class_mro.get(cls_name, []):
                                if "__str__" in self.classes.get(candidate, set()):
                                    str_cls = candidate
                                    break
                                if repr_cls is None and "__repr__" in self.classes.get(candidate, set()):
                                    repr_cls = candidate
                            if str_cls is None and "__str__" in self.classes.get(cls_name, set()):
                                str_cls = cls_name
                            if repr_cls is None and "__repr__" in self.classes.get(cls_name, set()):
                                repr_cls = cls_name
                            if str_cls is not None:
                                out.append(f'    piton_print_str_raw((const char*){_name(str_cls+"__"+"__str__")}({self._value(value)}));')
                                continue
                            if repr_cls is not None:
                                # REPR_FALLBACK_V1: CPython usa __repr__ cuando
                                # no hay __str__; mismo contrato de retorno str.
                                out.append(f'    piton_print_str_raw((const char*){_name(repr_cls+"__"+"__repr__")}({self._value(value)}));')
                                continue
                            raise NativeBuildError(
                                f"Linux native print of a '{cls_name}' instance without __str__/__repr__ is not supported "
                                "(can never match CPython object repr)"
                            )
                        if value in self._chr_results:
                            # CHR_NUL_V1: written with its encoded length.
                            out.append(f'    piton_write(1,(const char*){self._value(value)},piton_chr_len((const char*){self._value(value)}));')
                        elif value_type == "str":
                            out.append(f'    piton_print_str_raw((char*){self._value(value)});')
                        elif value_type == "bool":
                            out.append(f'    piton_print_str_raw({self._value(value)}?"True":"False");')
                        elif value_type == "none":
                            out.append('    piton_print_str_raw("None");')
                        elif value_type == "bigint":
                            out.append(f'    piton_bigint_print_raw((void*){_name(value)});')
                        elif value_type == "float":
                            out.append(f'    piton_print_float_bits_raw({self._value(value)});')
                        elif value_type == "complex":
                            out.append(f'    piton_print_complex_raw({self._value(value)});')
                        elif value_type == "slice":
                            out.append(f'    piton_print_slice((PitonSlice*){self._value(value)});')
                        elif value_type == "memoryview":
                            out.append(f'    piton_print_memoryview((PitonMemoryView*){self._value(value)});')
                        elif value_type == "module-pkg":
                            out.append(f'    piton_print_dynamic({self._value(value)});')
                        elif value_type == "range":
                            # RANGE_VALUE_V1: CPython prints `range(0, 3)`, never
                            # the materialized elements.
                            out.append(f'    piton_range_print((PitonRange*){self._value(value)});')
                        elif value_type == "slot":
                            # PRINT-slot-marker need.s a dispatched print: a
                            # str slot prints raw like imprimir(str); the
                            # others stay repr-shaped (lists, None, etc.).
                            out.append(f'    {{PitonSlot _sl=*(PitonSlot*){self._value(value)};if(_sl.kind==PK_STR){{piton_print_str_raw((char*)_sl.bits);}}else{{piton_print_slot(_sl);}}}}')
                        elif value_type in {"list", "tuple", "dict", "set", "set:frozen", "bytes", "bytearray"}:
                            out.append(f'    piton_print_slot({self._slot(value, types)});')
                        elif value_type == "int":
                            # PRINT_INT_V1 (al final: los tipos difusos como
                            # module-pkg/none tiene su propia rama antes).
                            out.append(f'    piton_print_int_safe({self._value(value)});')
                        else:
                            out.append(f'    piton_print_auto((long){self._value(value)});')
                    out.append(self._print_end_expr(print_end, types))
                else:
                    out.append(self._print_end_expr(print_end, types))
                out.append(f"    {_name(result)}=0;")
            elif function_name in {"formato", "format"}:
                # FORMAT_V1: format(valor, spec) == '{spec}'.format(valor) — se
                # reutiliza el motor de str.format (mismo comportamiento).
                if len(values) != 2:
                    raise NativeBuildError("Linux format() requires exactly two arguments")
                if types.get(values[1], "") != "str":
                    raise NativeBuildError("Linux format() spec must be a str literal")
                spec = self._fn_consts.get(values[1])
                if not isinstance(spec, str):
                    raise NativeBuildError("Linux format() requires a literal spec")
                vt = types.get(values[0], "int")
                if vt == "int" or vt == "bool":
                    # el type char 'd' lo consume apply_spec como desconocido:
                    # se elimina antes de aplicar presentación.
                    _spec = spec[:-1] if spec.endswith("d") else spec
                    _conv = f"piton_str_from_int({self._value(values[0])})"
                elif vt == "float":
                    # conversiones de tipo real: se resuelven con los
                    # formateadores existentes y luego se aplica la
                    # presentación (fill/align/sign/width).
                    _t = spec.rstrip("fFeEgG%")
                    _prec = None
                    if "." in _t:
                        try:
                            _prec = int(_t.split(".")[1])
                        except ValueError:
                            _prec = None
                    # la parte de presentación se aplica al resultado COMPLETO
                    # del formateador (fill/align/sign/width), nunca antes.
                    _present = _t.split(".")[0]
                    if spec.endswith("f") or spec.endswith("F"):
                        _conv = f"piton_float_fmt_fixed({self._value(values[0])},{_prec if _prec is not None else 6})"
                    elif spec.endswith("e") or spec.endswith("E"):
                        _conv = f"piton_float_fmt_exp({self._value(values[0])},{_prec if _prec is not None else 6},{1 if spec.endswith('E') else 0})"
                    elif spec.endswith("g") or spec.endswith("G"):
                        _conv = f"piton_float_fmt_g({self._value(values[0])},{1 if spec.endswith('G') else 0})"
                    elif spec.endswith("%"):
                        _conv = f"piton_float_fmt_pct({self._value(values[0])},{_prec if _prec is not None else 6})"
                    else:
                        _conv = f"piton_str_from_float({self._value(values[0])})"
                    _spec = _present
                elif vt == "str":
                    _conv = f"((const char*){self._value(values[0])})"
                    _spec = spec
                else:
                    raise NativeBuildError(f"Linux format() does not support {vt} values")
                if not _spec:
                    out.append(f"    {_name(result)}=(long){_conv};")
                else:
                    out.append(f"    {_name(result)}=(long)piton_str_apply_spec((const char*){_conv},{json.dumps(_spec)});")
                types[result] = "str"
                return out
            elif function_name in {"longitud", "len"}:
                if values:
                    v0_type = types.get(values[0], "") or "int"
                    if v0_type == "memoryview":
                        out.append(f"    {_name(result)}=((PitonMemoryView*){self._value(values[0])})->length;")
                        types[result] = "int"
                        return out
                    if v0_type.startswith("object:"):
                        cls_name = v0_type.split(":", 1)[1]
                        len_cls = None
                        for candidate in self.class_mro.get(cls_name, []):
                            if "__len__" in self.classes.get(candidate, set()):
                                len_cls = candidate
                                break
                        if len_cls is None and "__len__" in self.classes.get(cls_name, set()):
                            len_cls = cls_name
                        if len_cls is not None:
                            out.append(f'    {_name(result)}={_name(len_cls+"__"+"__len__")}({self._value(values[0])});')
                            types[result] = "int"
                            return out
                if len(values) == 1 and values[0] in self._chr_results:
                    # CHR_NUL_V1: len() counts CHARACTERS; a chr() result is
                    # always exactly one (print uses the byte length).
                    out.append(f"    {_name(result)}=1;")
                    types[result] = "int"
                    return out
                if len(values) == 1 and types.get(values[0]) == "str":
                    # STR_LEN_V1: len('hola') is the C string length.
                    out.append(f"    {_name(result)}=(long)piton_strlen((const char*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                if len(values) == 1 and types.get(values[0]) == "range":
                    # RANGE_VALUE_V1: len() of a range is exact, never materialized.
                    out.append(f"    {_name(result)}=piton_range_len((PitonRange*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                if len(values) == 1 and types.get(values[0]) in {"bytes", "bytearray"}:
                    out.append(f"    {_name(result)}=((PitonBytes*){self._value(values[0])})->length;")
                    types[result] = "int"
                    return out
                if len(values) == 1 and types.get(values[0]) == "str":
                    # LEN_STR_V1: len() de un str es su longitud en BYTES
                    # (como en CPython, que cuenta caracteres, no bytes: el
                    # backend solo tiene bytes, asi que coincide con ASCII).
                    out.append(f"    {_name(result)}=(long)piton_strlen((const char*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                if len(values) != 1:
                    raise NativeBuildError("Linux len requires exactly one argument")
                if types.get(values[0]) not in {"list", "tuple", "dict", "set", "set:frozen"}:
                    # LEN_DYNAMIC_V1: un tipo que NO es coleccion conocida no
                    # es un fallo de build: en CPython `len()` de casi todo lo
                    # demas levanta TypeError en RUNTIME (protocols.py:
                    # `len(self._drain_waiter)`). Se despacha en runtime y el
                    # helper decide (o levanta), fail-closed.
                    out.append(f"    {_name(result)}=piton_len_dynamic({self._value(values[0])});")
                    # la excepcion del helper debe poder capturarse
                    # (intentar/excepto TypeError) y, sin handler, terminar el
                    # programa como CPython.
                    self._emit_exc_check(out, function, call_handler)
                    types[result] = "int"
                    return out
                value_type = types[values[0]]
                if value_type in {"list", "tuple"}:
                    expression = f"((PitonSeq*){self._value(values[0])})->length"
                elif value_type == "dict":
                    expression = f"((PitonDict*){self._value(values[0])})->length"
                else:
                    expression = f"((PitonSet*){self._value(values[0])})->length"
                out.append(f"    {_name(result)}={expression};")
                types[result] = "int"
            elif function_name in {"abrir", "open"}:
                # WITH_ABRIR_V1: the freestanding subset cannot open files,
                # so this is honest ONLY for provably-missing literal paths
                # (emits the FileNotFoundError CPython raises). An existing
                # file or a non-literal path fails closed: claiming
                # FileNotFoundError there would be a lie, and silently
                # "opening" it would be worse.
                if not values or len(values) > 2:
                    raise NativeBuildError(f"Linux {function_name} requires one or two positional arguments")
                # FILE_IO_V1: el modo es posicional (abrir(ruta[, modo])).
                # `encoding=`/`buffering=` por keyword siguen fail-closed: el
                # MIR todavia no transporta kwargs en llamadas a builtins.
                mode_expr = '"r"'
                if len(values) == 2:
                    _mv = self._fn_consts.get(values[1])
                    if isinstance(_mv, str):
                        if "x" in _mv:
                            # FILE_MODE_V1: 'x' (creacion exclusiva) sigue
                            # fail-closed; '+' ya es O_RDWR en el runtime.
                            raise NativeBuildError(f"Linux abrir(mode={_mv!r}) is not supported in the native subset")
                        mode_expr = json.dumps(_mv)
                    elif types.get(values[1], "int") in {"str", "int", "none", ""}:
                        # FILE_IO_DYNMODE_V1: el modo viaja como valor (un
                        # wrapper de stdlib_piton lo recibe como parametro y
                        # aqui llega sin ser constante). piton_file_open
                        # parsea el mode string EN RUNTIME, asi que el valor
                        # dinamico es tan correcto como el literal: los modos
                        # no soportados (x/+) siguen siendo ValueError en C.
                        # El None viaja como el singleton y piton_file_open
                        # lo rechazaria: no puede ocurrir con `abrir(f, m)`
                        # de un wrapper que ya valido su default.
                        mode_expr = f"(const char*)(long){self._value(values[1])}"
                    else:
                        raise NativeBuildError("Linux abrir(mode) requires a literal mode")
                path_type = types.get(values[0], "int")
                if path_type != "str":
                    if path_type not in {"", "int", "none"}:
                        raise NativeBuildError(f"Linux {function_name} requires a str filename, not {path_type}")
                    # FILE_IO_V1: ruta sin tipo estatico (netrc: la que devuelve
                    # expanduser). El runtime comprueba que sea str y levanta el
                    # TypeError de CPython si no lo es.
                    out.append(f"    {_name(result)}=(long)piton_file_open_dyn({self._value(values[0])},{mode_expr});")
                    types[result] = "file"
                    self._files[result] = True
                    return out
                # FILE_IO_V1: modo. 'r' (defecto) lee; 'w'/'a' escriben por
                # descriptor. 'x', '+' y los modos mixtos fallan cerrados en el
                # runtime: no hay syscalls para ellos y no se divergen.
                out.append(f"    {_name(result)}=(long)piton_file_open((const char*){self._value(values[0])},{mode_expr});")
                types[result] = "file"
                self._files[result] = True
                # Call handler not available here, but for `con abrir` there's
                # no try/except handler anyway.
                return out
            elif function_name in {"input", "entrada"}:
                # INPUT_V1: input([prompt]) nativo. Sin argumento o con un
                # prompt str; cualquier otro tipo de prompt se convierte con
                # str() en runtime (como CPython), nunca falla cerrado en build.
                if len(values) > 1:
                    raise NativeBuildError("Linux input() takes at most one argument")
                _prompt = '""'
                if values:
                    _pt = types.get(values[0], "int")
                    if _pt == "str":
                        _prompt = f"(const char*){self._value(values[0])}"
                    else:
                        # INPUT_DYNAMIC_PROMPT_V1: input(5) / input(Nada) —
                        # CPython aplica str() al prompt. piton_str_of_value
                        # cubre int, bool, none, etc. con el texto exacto.
                        _prompt = f"piton_str_of_value({self._value(values[0])})"
                out.append(f"    {_name(result)}=(long)piton_input_read({_prompt});")
                types[result] = "str"
            elif function_name == "abs":
                if len(values) != 1:
                    raise NativeBuildError("Linux abs requires one argument")
                arg_type = types.get(values[0], "int")
                # ABS_TYPE_V1: `abs` of a non-numeric operand is a TypeError in
                # CPython; the else branch treated any non-float as an integer
                # and read a string/collection POINTER as a long, so
                # `abs('a')` printed an address and `abs(Nada)` answered 0.
                if arg_type not in {"int", "bool", "float", "complex"}:
                    self._runtime_type_error(out, "bad operand type for abs(): '" + f"{arg_type}" + "'")
                    types[result] = "int"
                    return out
                if arg_type == "complex":
                    # COMPLEX_V1: abs(1+2j) es la magnitud (float), no un
                    # puntero leído como entero.
                    out.append(f"    {_name(result)}=piton_cx_abs({self._value(values[0])});")
                    types[result] = "float"
                    return out
                if arg_type == "float":
                    out.append(f"    {_name(result)}={self._value(values[0])}&0x7fffffffffffffffUL;")
                    types[result] = "float"
                else:
                    out.append(f"    {_name(result)}={self._value(values[0])}<0?-{self._value(values[0])}:{self._value(values[0])};")
                    types[result] = "int"
            elif function_name in {"memoryview", "vista_memoria"}:
                # MEMORYVIEW_V1: vista sobre bytes/bytearray (no copia).
                if len(values) != 1:
                    raise NativeBuildError("Linux memoryview() takes exactly one argument")
                vt = types.get(values[0], "int")
                if vt not in {"bytes", "bytearray"}:
                    raise NativeBuildError("Linux memoryview() requires bytes or bytearray")
                _ba = 1 if vt == "bytearray" else 0
                out.append(
                    f"    {_name(result)}=piton_memoryview_new({self._value(values[0])},{_ba},0,"
                    f"((PitonBytes*){self._value(values[0])})->length,1);"
                )
                types[result] = "memoryview"
                return out
            elif function_name in {"slice", "rebanada"}:
                # SLICE_VALUE_V1: slice(a, b, c) con bounds ausentes = None.
                if len(values) > 3:
                    raise NativeBuildError("Linux slice() takes at most 3 arguments")
                if len(values) == 1:
                    # CPython: slice(stop) — el único argumento es el STOP.
                    vals = [None, values[0], None]
                else:
                    vals = list(values) + [None] * (3 - len(values))
                exprs = []
                for _v in vals:
                    if _v is None:
                        exprs.append(("0", 0))
                        continue
                    _t = types.get(_v, "int")
                    if _t in {"int", "bool"}:
                        exprs.append((self._value(_v), 1))
                    elif _v == "None" or _t == "none":
                        # Nada baja como LOAD, no como const: valor 0/ausente.
                        exprs.append(("0", 0))
                    else:
                        raise NativeBuildError("Linux slice() bounds must be ints or None")
                out.append(
                    f"    {_name(result)}=piton_slice_new({exprs[0][0]},{exprs[1][0]},{exprs[2][0]},"
                    f"{exprs[0][1]},{exprs[1][1]},{exprs[2][1]});"
                )
                types[result] = "slice"
                return out
            elif function_name in {"frozenset", "conjunto_inmutable"}:
                if len(values) > 1:
                    raise NativeBuildError("Linux frozenset() takes at most one argument")
                if not values:
                    out.append(f"    {_name(result)}=piton_frozenset_new_empty();")
                else:
                    vtype = types.get(values[0], "int")
                    out.append(f"    {_name(result)}=piton_frozenset_from_seq({self._value(values[0])},{self._kind(vtype)});")
                types[result] = "set:frozen"
                return out
            elif function_name in {"complex", "complejo"}:
                # COMPLEX_V1: complejo() -> 0j; complejo(r); complejo(r, i).
                # Argumentos string (complejo('1+2j')) fallan cerrados.
                numeric = {"int", "float", "bool"}
                arg_types = [types.get(v, "int") for v in values]
                if len(values) == 0:
                    out.append(f"    {_name(result)}=(long)piton_complex_new(0.0,0.0);")
                elif len(values) == 1:
                    at = arg_types[0]
                    if at == "complex":
                        c = values[0]
                        out.append(f"    {_name(result)}=(long)piton_complex_new(piton_cx_re({self._value(c)}),piton_cx_im({self._value(c)}));")
                    elif at == "float":
                        v = values[0]
                        out.append(f"    {_name(result)}=(long)piton_complex_new(piton_bits_double({self._value(v)}),0.0);")
                    elif at in {"int", "bool"}:
                        v = values[0]
                        out.append(f"    {_name(result)}=(long)piton_complex_new((double){self._value(v)},0.0);")
                    elif at == "str":
                        # COMPLEX_STR_CTOR_V1: complejo('1+2j') / complejo('3') /
                        # complejo('2.5j'). Parser acotado: [signo] real [+-] imag j.
                        out.append(f"    {_name(result)}=(long)piton_complex_from_str((const char*){self._value(values[0])});")
                        out.append(f"    if(piton_exc_flag){{piton_report_unhandled();piton_exit(1);}}")
                    else:
                        raise NativeBuildError("Linux complex() from a string or non-numeric is not supported (fail closed)")
                elif len(values) == 2:
                    if arg_types[0] == "complex":
                        raise NativeBuildError("Linux complex(complex, imag) is a CPython TypeError (fail closed)")
                    if arg_types[0] not in numeric or arg_types[1] not in numeric:
                        raise NativeBuildError("Linux complex(re, im) requires two real numbers")
                    re_expr = (
                        f"piton_bits_double({self._value(values[0])})"
                        if arg_types[0] == "float" else f"(double){self._value(values[0])}"
                    )
                    im_expr = (
                        f"piton_bits_double({self._value(values[1])})"
                        if arg_types[1] == "float" else f"(double){self._value(values[1])}"
                    )
                    out.append(f"    {_name(result)}=(long)piton_complex_new({re_expr},{im_expr});")
                else:
                    self._runtime_type_error(out, "complex() takes at most 2 arguments")
                    types[result] = "int"
                    return out
                types[result] = "complex"
            elif function_name == "callable":
                if len(values) != 1:
                    raise NativeBuildError("Linux callable requires one argument")
                vtype = types.get(values[0], "int")
                name = aliases.get(values[0], values[0]) if isinstance(values[0], str) else ""
                if vtype == "builtin" or vtype.startswith("class:") or vtype == "closure":
                    out.append(f"    {_name(result)}=1;")
                elif vtype.startswith("object:"):
                    cls = vtype.split(":", 1)[1]
                    hit = any("__call__" in self.classes.get(c, set()) for c in self.class_mro.get(cls, [cls]))
                    out.append(f"    {_name(result)}={1 if hit else 0};")
                elif isinstance(name, str) and name in self.function_names:
                    out.append(f"    {_name(result)}=1;")
                else:
                    out.append(f"    {_name(result)}=0;")
                types[result] = "bool"
                return out
            elif function_name == "hash":
                if len(values) != 1:
                    raise NativeBuildError("Linux hash requires one argument")
                vtype = types.get(values[0], "int")
                if vtype.startswith("object:"):
                    # HASH_DUNDER_V1: hash(obj) despacha a __hash__ por MRO.
                    cls = vtype.split(":", 1)[1]
                    hit = None
                    for cand in self.class_mro.get(cls, [cls]):
                        if "__hash__" in self.classes.get(cand, set()):
                            hit = cand
                            break
                    if hit is not None:
                        target = _name(hit + "____hash__")
                        if self.function_frame_abi.get(hit + "____hash__", False):
                            out.append(f'    {{long _hh[]={{ {self._value(values[0])} }}; {_name(result)}=piton_frame_call((long)&{target},1,_hh);}}')
                        else:
                            out.append(f"    {_name(result)}={target}({self._value(values[0])});")
                        types[result] = "int"
                        return out
                if vtype in {"set", "set:frozen"}:
                    # HASH_FROZENSET_V1: frozenset es hashable; el contrato del
                    # corpus es que conjuntos iguales den hashes iguales (orden
                    # de inserción estable para enteros).
                    out.append(f"    {_name(result)}=piton_hash_set((PitonSet*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                if vtype in {"int", "bool", "float"}:
                    if vtype == "float":
                        # hash(float): byte-identico al hash del entero
                        # equivalente cuando el float es integral (contrato del
                        # corpus: solo igualdad entre valores iguales).
                        out.append(f"    {_name(result)}=piton_hash_str((const char*)0);")
                        out.append(f"    {_name(result)}=piton_hash_of_float({self._value(values[0])});")
                    else:
                        out.append(f"    {_name(result)}=piton_hash_long({self._value(values[0])});")
                elif vtype == "str":
                    out.append(f"    {_name(result)}=piton_hash_str((const char*){self._value(values[0])});")
                elif vtype == "none":
                    # CPython 3.12: hash(None) es constante estable del build.
                    out.append(f"    {_name(result)}=4238894112;")
                else:
                    self._runtime_type_error(out, "unhashable type: '" + vtype + "'")
                    types[result] = "int"
                    return out
                types[result] = "int"
                return out
            elif function_name == "ascii":
                if len(values) != 1:
                    raise NativeBuildError("Linux ascii requires one argument")
                vtype = types.get(values[0], "int")
                out.append(f"    {_name(result)}=piton_slot_repr_ascii({self._value(values[0])},{self._kind(vtype)});")
                types[result] = "str"
                return out
            elif function_name == "repr":
                if len(values) != 1:
                    raise NativeBuildError("Linux repr requires one argument")
                vtype = types.get(values[0], "int")
                out.append(f"    {_name(result)}=piton_slot_repr({self._value(values[0])},{self._kind(vtype)});")
                types[result] = "str"
                return out
            elif function_name in {"all", "any"}:
                if len(values) != 1:
                    raise NativeBuildError(f"Linux {function_name} requires one argument (M14 v1)")
                helper = "piton_all_seq" if function_name == "all" else "piton_any_seq"
                if types.get(values[0]) in {"genexpr", "iterator:genexpr"}:
                    # ANY_GENEXPR_V1: `any(f(x) for x in xs)` materializa el
                    # genexpr (warnings.py: `any(filename.startswith(t) ...)`).
                    out.append(
                        f"    {_name(result)}={helper}((PitonSeq*)piton_genexpr_to_list({self._value(values[0])}));"
                    )
                elif types.get(values[0]) in {"list", "tuple"}:
                    out.append(f"    {_name(result)}={helper}((PitonSeq*){self._value(values[0])});")
                else:
                    raise NativeBuildError(
                        f"Linux {function_name} requires one list, tuple or generator expression (M14 v1)"
                    )
                types[result] = "bool"
            elif function_name == "divmod":
                if len(values) != 2:
                    raise NativeBuildError("Linux divmod requires two arguments")
                t0, t1 = types.get(values[0], "int"), types.get(values[1], "int")
                if "float" in {t0, t1}:
                    # DIVMOD_FLOAT_V1: divmod(float, float) -> (q, r) floats.
                    lv = f"piton_bits_double({self._value(values[0])})" if t0 == "float" else f"(double){self._value(values[0])}"
                    rv = f"piton_bits_double({self._value(values[1])})" if t1 == "float" else f"(double){self._value(values[1])}"
                    out.append(f"    {_name(result)}=piton_divmod_float({lv},{rv});")
                    self._emit_exc_check(out, function, call_handler)
                    types[result] = "tuple"
                    self._coll_elems[result] = "float"
                    return out
                if t0 == "bigint" or t1 == "bigint":
                    # DIVMOD_BIGINT_V1: divmod(bigint, int|bigint) -> tupla
                    # (q, r) como CPython; el divisor int se promueve.
                    if t0 not in {"int", "bool", "bigint"} or t1 not in {"int", "bool", "bigint"}:
                        raise NativeBuildError("Linux divmod(bigint, x) requires int or bigint arguments")
                    _b0 = 1 if t0 == "bigint" else 0
                    _b1 = 1 if t1 == "bigint" else 0
                    out.append(
                        f"    {_name(result)}=piton_divmod_bigint({self._value(values[0])},{self._value(values[1])},{_b0},{_b1});"
                    )
                    self._emit_exc_check(out, function, call_handler)
                    types[result] = "tuple"
                    self._coll_elems[result] = "bigint"
                    return out
                if t0 not in {"int", "bool"} or t1 not in {"int", "bool"}:
                    raise NativeBuildError("Linux divmod requires int or float arguments")
                out.append(f"    {_name(result)}=piton_divmod_xy({self._value(values[0])},{self._value(values[1])});")
                types[result] = "tuple"
                self._coll_elems[result] = "int"
            elif function_name == "pow":
                if len(values) == 3:
                    # POW_MOD_V1: pow(base, exp, mod) — los tres deben ser int.
                    if any(types.get(v) not in {"int", "bool"} for v in values):
                        raise NativeBuildError("Linux pow(base, exp, mod) requires three int arguments")
                    out.append(f"    {_name(result)}=piton_pow_mod({self._value(values[0])},{self._value(values[1])},{self._value(values[2])});")
                    self._emit_exc_check(out, function, call_handler)
                    types[result] = "int"
                    return out
                if len(values) != 2:
                    self._runtime_type_error(out, "pow expected at least 2 arguments, got " + str(len(values)))
                    types[result] = "int"
                    return out
                base_type = types.get(values[0])
                if base_type == "float":
                    if types.get(values[1]) not in {"int", "bool"}:
                        raise NativeBuildError("Linux pow(float, e) requires an int exponent (M14 v1)")
                    out.append(f"    {_name(result)}=piton_pow_float({self._value(values[0])},{self._value(values[1])});")
                    types[result] = "float"
                elif base_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}=piton_pow_int({self._value(values[0])},{self._value(values[1])});")
                    types[result] = "int"
                else:
                    self._runtime_type_error(out, "unsupported operand type(s) for pow(): '" + f"{base_type}" + "'")
                    types[result] = "int"
                    return out
                if call_handler is not None:
                    out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            elif function_name in {"ord", "chr", "bin"}:
                if len(values) != 1:
                    raise NativeBuildError(f"Linux {function_name} requires exactly one argument")
                arg_type = types.get(values[0])
                if function_name in {"chr", "bin"} and arg_type not in {"int", "bool"}:
                    raise NativeBuildError(f"Linux {function_name} requires an int argument")
                if function_name == "ord" and arg_type != "str":
                    # ORD_DYNAMIC_V1: `ord(x)` con x de tipo desconocido se
                    # decide en runtime (CPython levanta TypeError si no es
                    # un caracter). curses/ascii.py: `ord(c)`.
                    out.append(f"    {_name(result)}=piton_ord_dynamic({self._value(values[0])});")
                    self._emit_exc_check(out, function, call_handler)
                    types[result] = "int"
                    return out
                helper = f"piton_{function_name}"
                if function_name == "ord":
                    out.append(f"    {_name(result)}={helper}((const char*){self._value(values[0])});")
                    types[result] = "int"
                else:
                    out.append(f"    {_name(result)}={helper}({self._value(values[0])});")
                    types[result] = "str"
                    if function_name == "chr":
                        self._chr_results.add(result)
                if call_handler is not None:
                    out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            elif function_name in {"globals", "locals"}:
                # GLOBALS_NS_V1: globals()/locals() -> namespace dict del
                # modulo (lazy). El tipo dict hace que el get/set y los
                # metodos (items/keys) tomen el camino dict existente.
                if values:
                    raise NativeBuildError(f"Linux {function_name} takes no arguments")
                if function_name == "globals":
                    out.append("    if(!piton_globals_ns){piton_globals_ns=(long)piton_dict_new(0);}")
                    out.append(f"    {_name(result)}=piton_globals_ns;")
                else:
                    out.append(f"    {_name(result)}=(long)piton_dict_new(0);")
                types[result] = "dict"
                self._dict_key_types[result] = "str"
            elif function_name in {"round", "redondear"}:
                if len(values) == 2:
                    _r0t = types.get(values[0])
                    if _r0t in ("", None):
                        # ROUND_DYN_V1: tipo desconocido -> despacha en runtime
                        # (int -> identidad, float -> redondeo; TypeError si
                        # no es numero). introduction.py: round(_, 2).
                        out.append(f"    {_name(result)}=piton_round_dyn({self._value(values[0])},1,{self._value(values[1])});")
                        self._emit_exc_check(out, function, call_handler)
                        types[result] = "int"
                        return out
                    if types.get(values[0]) in {"int", "bool"}:
                        # ROUND_INT_ID_V1: CPython round(int, n) devuelve el
                        # mismo int (sin conversion a float).
                        out.append(f"    {_name(result)}={self._value(values[0])};")
                        types[result] = "int"
                        return out
                    if types.get(values[0]) != "float":
                        raise NativeBuildError("Linux round(x, ndigits) requires a float first argument")
                    if types.get(values[1]) not in {"int", "bool"}:
                        raise NativeBuildError("Linux round(x, ndigits) requires an int ndigits")
                    out.append(f"    {_name(result)}=piton_round_float_nd({self._value(values[0])},{self._value(values[1])});")
                    types[result] = "float"
                    return out
                if len(values) != 1:
                    raise NativeBuildError("Linux round requires one or two arguments")
                arg_type = types.get(values[0])
                if arg_type == "float":
                    out.append(f"    {_name(result)}=piton_round_float({self._value(values[0])});")
                elif arg_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}={self._value(values[0])};")
                else:
                    self._runtime_type_error(out, "round() argument must be int or float, not '" + f"{arg_type}" + "'")
                    types[result] = "int"
                    return out
                types[result] = "int"
                if call_handler is not None:
                    out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            elif function_name in {"entero", "int"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux int requires exactly one argument")
                arg_type = types.get(values[0])
                operand = self._value(values[0])
                if arg_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}={operand};")
                elif arg_type == "float":
                    out.append(f"    {_name(result)}=(long)piton_bits_double({operand});")
                elif arg_type == "str":
                    out.append(f"    {_name(result)}=piton_int_from_str((const char*){operand});")
                    if call_handler is not None:
                        out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                    else:
                        out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
                else:
                    # INT_OF_VALUE_V1: tipo estatico desconocido — despacha en
                    # runtime (str -> parse, double -> truncado; el resto es
                    # TypeError con el texto de CPython).
                    out.append(f"    {_name(result)}=piton_int_of_value({operand});")
                    self._emit_exc_check(out, function, call_handler)
                types[result] = "int"
            elif function_name in {"decimal", "float"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux float requires exactly one argument")
                arg_type = types.get(values[0])
                operand = self._value(values[0])
                if arg_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}=piton_double_bits((double){operand});")
                elif arg_type == "float":
                    out.append(f"    {_name(result)}={operand};")
                elif arg_type == "str":
                    out.append(f"    {_name(result)}=piton_float_from_str((const char*){operand});")
                    if call_handler is not None:
                        out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                    else:
                        out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
                else:
                    raise NativeBuildError("Linux float() requires int, float or str (M14 v1)")
                types[result] = "float"
            elif function_name in {"texto", "str"}:
                if len(values) == 1:
                    arg_type = types.get(values[0])
                    operand = self._value(values[0])
                    if arg_type == "int":
                        out.append(f"    {_name(result)}=piton_str_from_int({operand});")
                    elif arg_type == "float":
                        out.append(f"    {_name(result)}=piton_str_from_float({operand});")
                    elif arg_type == "bool":
                        out.append(f"    {_name(result)}=(long)({operand}?\"True\":\"False\");")
                    elif arg_type == "none":
                        out.append(f"    {_name(result)}=(long)\"None\";")
                    elif arg_type == "str":
                        out.append(f"    {_name(result)}={operand};")
                    elif arg_type == "complex":
                        out.append(f"    {_name(result)}=(long)piton_complex_str({operand});")
                    elif arg_type in {"list", "tuple"}:
                        # STR_SEQ_V1 (mismo camino que '%s'): el str() de una
                        # secuencia es su repr, como en CPython.
                        out.append(f"    {_name(result)}=piton_str_from_seq((PitonSeq*){operand});")
                    elif arg_type is None or arg_type == "":
                        # STR_DYNAMIC_V1: tipo desconocido (parametro, bloque
                        # emitido fuera de orden) -> str() de runtime.
                        out.append(f"    {_name(result)}=(long)piton_str_of_value({operand});")
                    else:
                        raise NativeBuildError("Linux str() requires int/float/bool/None/str (M14 v1)")
                    types[result] = "str"
                elif len(values) in {2, 3}:
                    # STR_DECODE_V1: str(bytes, encoding[, errors]) -> decode
                    # bytes a str. Si el primer arg es str, se devuelve tal
                    # cual (CPython ignora encoding para str input).
                    src_type = types.get(values[0])
                    enc = self._value(values[1])
                    if src_type == "str":
                        out.append(f"    {_name(result)}={self._value(values[0])};")
                    elif src_type in {"bytes", "bytearray"}:
                        out.append(f"    {_name(result)}=(long)piton_bytes_decode((void*){self._value(values[0])},(const char*){enc});")
                    else:
                        raise NativeBuildError("Linux str(bytes, encoding) requires bytes or str")
                    types[result] = "str"
                else:
                    raise NativeBuildError("Linux str requires exactly one argument")
            elif function_name in {"booleano", "bool"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux bool requires exactly one argument")
                arg_type = types.get(values[0])
                operand = self._value(values[0])
                if arg_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}=({operand}!=0);")
                elif arg_type == "float":
                    out.append(f"    {_name(result)}=(({operand}&0x7FFFFFFFFFFFFFFFL)!=0);")
                elif arg_type == "str":
                    out.append(f"    {_name(result)}=piton_str_truthy((const char*){operand});")
                elif arg_type in {"list", "tuple"}:
                    out.append(f"    {_name(result)}=(((PitonSeq*){operand})->length>0);")
                elif arg_type == "dict":
                    out.append(f"    {_name(result)}=(((PitonDict*){operand})->length>0);")
                elif arg_type in {"set", "set:frozen"}:
                    out.append(f"    {_name(result)}=(((PitonSet*){operand})->length>0);")
                elif arg_type in {"bytes", "bytearray"}:
                    out.append(f"    {_name(result)}=(((PitonBytes*){operand})->length>0);")
                elif arg_type == "none":
                    out.append(f"    {_name(result)}=0;")
                elif arg_type == "complex":
                    out.append(f"    {_name(result)}=piton_cx_truthy_bits({operand});")
                elif str(arg_type).startswith("object:"):
                    # BOOL_OBJECT_V1: bool(obj) como CPython — __bool__ si
                    # existe, si no __len__ (!= 0), si no True. El metodo de
                    # usuario puede lanzar, asi que se routea al handler.
                    _bcls = arg_type.split(":", 1)[1]
                    _b_bool = _b_len = None
                    for _cand in self.class_mro.get(_bcls, []):
                        if _b_bool is None and "__bool__" in self.classes.get(_cand, set()):
                            _b_bool = _cand
                        if _b_len is None and "__len__" in self.classes.get(_cand, set()):
                            _b_len = _cand
                    if _b_bool is None and "__bool__" in self.classes.get(_bcls, set()):
                        _b_bool = _bcls
                    if _b_len is None and "__len__" in self.classes.get(_bcls, set()):
                        _b_len = _bcls
                    if _b_bool is not None:
                        out.append(f"    {_name(result)}=({_name(_b_bool + '__' + '__bool__')}({operand})!=0);")
                    elif _b_len is not None:
                        out.append(f"    {_name(result)}=({_name(_b_len + '__' + '__len__')}({operand})!=0);")
                    else:
                        out.append(f"    {_name(result)}=1;")
                    types[result] = "bool"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                else:
                    raise NativeBuildError("Linux bool() requires int/float/str/collection/None (M14 v1)")
                types[result] = "bool"
            elif function_name in {"min", "max"}:
                # MINMAX_VARIADIC_V1: min/max de N>=3 ESCALARES del mismo
                # tipo estatico (colorsys: max(r, g, b) floats) — se
                # distingue del key= porque alli values[0] es list/tuple.
                # Fold de comparaciones tipadas, misma regla que el caso
                # binario (mezclas fallan cerradas).
                # MINMAX_KWARGS_FIRST_V1: la forma (coleccion, default, clave,
                # reversa) de `min(x, default=..., key=...)` trae 4 valores y su
                # primero es una coleccion o un iterador. La rama variadica de
                # escalares se la comia antes (heapq: nsmallest con el atajo de
                # n==1). Un min/max de 4 escalares NO es coleccion, asi que
                # sigue yendo por la rama variadica.
                _mm_kwargs = len(values) == 4 and (
                    types.get(values[0]) in {"list", "tuple"}
                    or str(types.get(values[0], "")).startswith("iterator:")
                )
                if len(values) >= 3 and types.get(values[0]) not in {"list", "tuple"} and not _mm_kwargs:
                    _vts = [types.get(v, "int") for v in values]
                    if len(set(_vts)) != 1 or _vts[0] not in {"str", "float", "int", "bool"}:
                        raise NativeBuildError(
                            f"Linux min/max of {len(values)} values requires one homogeneous kind, not {sorted(set(_vts))}"
                        )
                    _cmp = "<" if function_name == "min" else ">"
                    _expr = self._value(values[0])
                    for _v in values[1:]:
                        _b = self._value(_v)
                        if _vts[0] == "str":
                            _expr = f"(piton_strcmp((const char*)({_expr}),(const char*){_b}){_cmp}0)?({_expr}):{_b}"
                        elif _vts[0] == "float":
                            _expr = f"(piton_bits_double({_expr}){_cmp}piton_bits_double({_b}))?({_expr}):{_b}"
                        else:
                            _expr = f"(({_expr}){_cmp}{_b})?({_expr}):{_b}"
                    out.append(f"    {_name(result)}={_expr};")
                    types[result] = _vts[0]
                    self._emit_exc_check(out, function, call_handler)
                    return out
                # MINMAX_KEY_V1: min/max con `key=` llegan como (coleccion,
                # clave, reversa). La clave SÍ se aplica (extremo Node sobre la
                # clave, devolviendo el elemento original).
                if len(values) == 4:
                    # MINMAX_DEFAULT_V1: (coleccion, default, clave, reversa).
                    # `default=` devuelve ese valor si la coleccion esta
                    # vacia; la clave (si la hay) se aplica como en key=.
                    _coll, _dflt, _key, _rev = values
                    _coll_type = types.get(_coll, "")
                    _want_min = 1 if function_name == "min" else 0
                    _key_expr = self._value(_key) if _key is not None else "0"
                    _dflt_expr = self._value(_dflt)
                    if _coll_type == "iterator:genexpr":
                        out.append(f"    {{PitonSeq*_mmc=(PitonSeq*)piton_genexpr_to_list({self._value(_coll)});")
                        out.append(f"    {_name(result)}=piton_extreme_default_new(_mmc,{_key_expr},{_want_min},{_dflt_expr});}}")
                    elif _coll_type.startswith("iterator:"):
                        out.append(f"    {{PitonSeq*_mmc=(PitonSeq*)piton_collect_any_iter({self._value(_coll)});")
                        out.append(f"    {_name(result)}=piton_extreme_default_new(_mmc,{_key_expr},{_want_min},{_dflt_expr});}}")
                    elif _coll_type in {"list", "tuple"}:
                        out.append(f"    {_name(result)}=piton_extreme_default_new((void*){self._value(_coll)},{_key_expr},{_want_min},{_dflt_expr});")
                    else:
                        raise NativeBuildError(
                            "Linux min/max default= requires one list, tuple or iterator"
                        )
                    self._emit_exc_check(out, function, call_handler)
                    _et = self._coll_elems.get(_coll, "int")
                    if _coll_type.startswith("iterator:"):
                        _src = self._iter_source.get(_coll)
                        _et = self._coll_elems.get(_src, "int") if _src else "int"
                    types[result] = _et if _et and "|" not in _et else "int"
                    return out
                if len(values) == 3:
                    if types.get(values[0]) not in {"list", "tuple"}:
                        raise NativeBuildError("Linux min/max key= requires one list or tuple")
                    out.append(f"    {_name(result)}=piton_extreme_key_new((void*){self._value(values[0])},{self._value(values[1])},{1 if function_name == 'min' else 0});")
                    self._emit_exc_check(out, function, call_handler)
                    _et = self._coll_elems.get(values[0], "int")
                    types[result] = _et if _et and "|" not in _et else "int"
                    return out
                if len(values) == 1:
                    _coll_type = types.get(values[0], "")
                    if _coll_type in {"list", "tuple", "str"} or _coll_type.startswith("iterator:"):
                        # MINMAX_ITER_V1: min()/max() of a single iterable. The
                        # element type rides along so the result prints correctly;
                        # heterogeneous or empty inputs raise through the helper.
                        # Iterators are collected to a list first.
                        want_min = 1 if function_name == "min" else 0
                        _coll = values[0]
                        _iter_tmp = False
                        if _coll_type.startswith("iterator:"):
                            out.append(f"    {{PitonSeq*_mmc=(PitonSeq*)piton_collect_any_iter({self._value(_coll)});")
                            _coll_ref = "_mmc"
                            _iter_tmp = True
                        elif types.get(values[0]) == "str":
                            _coll_ref = f"(const char*){self._value(values[0])}"
                        else:
                            _coll_ref = f"(PitonSeq*){self._value(values[0])}"
                        # Brace balance: the iterator path already opened a block
                        # above, so its PitonSlot line must NOT open another; the
                        # non-iterator path needs the opening brace here.
                        _open = "" if _iter_tmp else "{"
                        _helper = "piton_str_minmax" if types.get(values[0]) == "str" else "piton_seq_minmax"
                        out.append(f"    {_open}PitonSlot _mm={_helper}({_coll_ref},{want_min});")
                        elem_type = self._coll_elems.get(values[0], "int")
                        if types.get(values[0]) == "str":
                            elem_type = "str"
                        if "|" in elem_type:
                            # heterogeneous collection: the winner's kind is only
                            # known at runtime, so hand out a tagged slot.
                            out.append(f"    PitonSlot*_mmp=piton_alloc(sizeof(PitonSlot));*_mmp=_mm; {_name(result)}=(long)_mmp;}}")
                            types[result] = "slot"
                        else:
                            out.append(f"    {_name(result)}=_mm.bits;}}")
                            types[result] = elem_type
                        self._emit_exc_check(out, function, call_handler)
                        return out
                    # Unsupported single-arg types fall through to the
                    # historical "requires two arguments" error (unchanged
                    # behavior for min(5) etc.).
                if len(values) != 2:
                    raise NativeBuildError("Linux min/max requires two arguments")
                # MINMAX_TYPES_V1 (mirrors Windows): the winner keeps its
                # kind. Both str -> lexicographic; both float -> float;
                # both int/bool -> int compare ("bool" only when both are
                # bool); anything mixed fails closed (CPython raises
                # TypeError, or the winner's type is not statically
                # knowable for int/float mixes).
                t0, t1 = types.get(values[0], "int"), types.get(values[1], "int")
                comparison = "<" if function_name == "min" else ">"
                if t0 == t1 == "str":
                    out.append(f"    {_name(result)}=(piton_strcmp((const char*){self._value(values[0])},(const char*){self._value(values[1])}){comparison}0)?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "str"
                elif t0 == t1 == "float":
                    out.append(f"    {_name(result)}=piton_bits_double({self._value(values[0])}){comparison}piton_bits_double({self._value(values[1])})?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "float"
                elif t0 == t1 == "bool":
                    out.append(f"    {_name(result)}={self._value(values[0])}{comparison}{self._value(values[1])}?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "bool"
                elif t0 == t1 == "int":
                    out.append(f"    {_name(result)}={self._value(values[0])}{comparison}{self._value(values[1])}?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "int"
                elif {t0, t1} <= {"int", "bool"}:
                    # mixed bool/int: the winner's type depends on runtime
                    # values (min(True, 5) is True, max(True, 5) is 5), so
                    # the result type is not statically knowable.
                    raise NativeBuildError(
                        f"Linux min/max requires two values of the same kind, not {t0}/{t1}"
                    )
                else:
                    raise NativeBuildError(
                        f"Linux min/max requires two values of the same kind, not {t0}/{t1}"
                    )
            elif function_name in {"sum", "suma"}:
                if len(values) == 2:
                    # SUM_START_V1: sum(iterable, start) — el inicio se suma al
                    # total acumulado (CPython acepta cualquier start numérico).
                    st = types.get(values[1], "int")
                    if st not in {"int", "bool", "float"}:
                        raise NativeBuildError("Linux sum(x, start) requires a numeric start")
                    acc = f"piton_double_bits({self._value(values[1])})" if st == "float" else self._value(values[1])
                    et = self._coll_elems.get(values[0])
                    _mixed_float = et is not None and "float" in et.split("|")
                    if _mixed_float or st == "float":
                        _stv = self._value(values[1]) if st == "float" else f"piton_double_bits((double)({self._value(values[1])}))"
                        out.append(f"    {_name(result)}=piton_sum_seq_float_start({self._value(values[0])},{_stv});")
                        types[result] = "float"
                    else:
                        out.append(f"    {_name(result)}=piton_sum_seq_start({self._value(values[0])},{self._value(values[1])});")
                        types[result] = "int"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if len(values) != 1 or types.get(values[0]) not in {
                    "list", "tuple", "dict", "set", "set:frozen", "range", "generator"
                }:
                    raise NativeBuildError("Linux sum requires one collection")
                value_type = types.get(values[0])
                if value_type == "generator":
                    # GEN_SUM_V1: sum over a generator materializes it once.
                    out.append(f"    PitonSeq*_gsum=piton_collect_gen({self._value(values[0])});")
                    out.append(f"    {_name(result)}=piton_sum_seq(_gsum);")
                    types[result] = "int"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if value_type == "range":
                    # RANGE_VALUE_V1: exact, never materialized.
                    out.append(f"    {_name(result)}=piton_sum_range((PitonRange*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                struct_map = {"list": "PitonSeq", "tuple": "PitonSeq", "dict": "PitonDict", "set": "PitonSet", "set:frozen": "PitonSet"}
                helper_map = {"list": "piton_sum_seq", "tuple": "piton_sum_seq", "dict": "piton_sum_dict", "set": "piton_sum_set", "set:frozen": "piton_sum_set"}
                if value_type not in struct_map:
                    raise NativeBuildError("Linux sum requires one collection")
                # COLL_ELEM_TYPE_V1: the helper adds each element's raw bits, so
                # a bigint element contributed its POINTER and sum printed an
                # address (`sum([10 ** 20])` -> 4210752). Refuse anything whose
                # element type is not int instead of accumulating garbage.
                elem_type = self._coll_elems.get(values[0], "int")
                kinds = set(elem_type.split("|")) if elem_type else {"int"}
                if "float" in kinds and kinds <= {"int", "bool", "float"}:
                    if value_type not in {"list", "tuple"}:
                        self._runtime_type_error(out, "unsupported operand type(s) for +: '" + f"{elem_type}" + "'")
                        types[result] = "int"
                        return out
                    out.append(f"    {_name(result)}=piton_sum_seq_float((PitonSeq*){self._value(values[0])});")
                    types[result] = "float"
                    return out
                if "bigint" in kinds and kinds <= {"int", "bool", "bigint"}:
                    if value_type not in {"list", "tuple"}:
                        self._runtime_type_error(out, "unsupported operand type(s) for +: '" + f"{elem_type}" + "'")
                        types[result] = "int"
                        return out
                    out.append(f"    {_name(result)}=piton_sum_seq_bigint((PitonSeq*){self._value(values[0])});")
                    types[result] = "bigint"
                    bigint_slots.append(result)
                    return out
                if kinds != {"int"}:
                    elem_type = "|".join(sorted(kinds))
                    self._runtime_type_error(out, "unsupported operand type(s) for +: '" + f"{elem_type}" + "'")
                    types[result] = "int"
                    return out
                out.append(f"    {_name(result)}={helper_map[value_type]}(({struct_map[value_type]}*){self._value(values[0])});")
                types[result] = "int"
            elif function_name in {"range", "rango"}:
                # RANGE_VALUE_V1: `rango(...)` as a VALUE produces a lazy range
                # object (never materialized). The `for` header keeps its
                # counter-loop fast path, which consumes the call's arguments
                # directly, so this changes nothing there.
                if len(values) < 1 or len(values) > 3:
                    raise NativeBuildError("Linux rango() takes 1-3 arguments")
                for value in values:
                    if types.get(value, "int") not in {"int", "bool"}:
                        raise NativeBuildError("Linux rango() arguments must be ints")
                if len(values) == 1:
                    start, stop, step = "0", self._value(values[0]), "1"
                elif len(values) == 2:
                    start, stop = (self._value(value) for value in values)
                    step = "1"
                else:
                    start, stop, step = (self._value(value) for value in values)
                out.append(f"    {_name(result)}=(long)piton_range_new({start},{stop},{step});")
                types[result] = "range"
                # the elements of a range are always ints
                self._coll_elems[result] = "int"
                if call_handler is not None:
                    out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            elif function_name in {"bytes", "octetos", "bytearray", "arreglo_bytes"}:
                # BYTES_CTOR_V1: bytes()/bytearray() as values. str needs an
                # explicit encoding (CPython TypeError otherwise); int makes
                # zero-filled; list/tuple/range/bytes/bytearray copy.
                is_ba = function_name in {"bytearray", "arreglo_bytes"}
                tname = "bytearray" if is_ba else "bytes"
                ctor = "piton_bytearray" if is_ba else "piton_bytes"
                if len(values) > 2:
                    raise NativeBuildError(f"Linux {tname}() takes at most two arguments")
                if len(values) == 0:
                    out.append(f"    {_name(result)}=(long){ctor}_new(0,0);")
                elif types.get(values[0]) == "str":
                    if len(values) == 1:
                        raise NativeBuildError(f"Linux {tname}() string argument without an encoding")
                    out.append(f"    {_name(result)}=(long){ctor}_from_str((const char*){self._value(values[0])},(const char*){self._value(values[1])});")
                    out.append(f"    if(!{_name(result)}){{piton_report_unhandled();piton_exit(1);}}")
                else:
                    if len(values) == 2:
                        raise NativeBuildError(f"Linux {tname}() encoding requires a str source")
                    out.append(f"    {_name(result)}=(long){ctor}_from_any({self._slot(values[0], types)});")
                    out.append(f"    if(!{_name(result)}){{piton_report_unhandled();piton_exit(1);}}")
                types[result] = tname
            elif function_name in {"list", "lista", "tuple", "tupla", "set", "conjunto", "dict", "diccionario"}:
                # CONV_BUILTINS_V1: the conversion builtins as values.
                alias, seq_kind = {
                    "list": ("lista", "PK_LIST"), "lista": ("lista", "PK_LIST"),
                    "tuple": ("tupla", "PK_TUPLE"), "tupla": ("tupla", "PK_TUPLE"),
                    "set": ("conjunto", None), "conjunto": ("conjunto", None),
                    "dict": ("diccionario", None), "diccionario": ("diccionario", None),
                }[function_name]
                if len(values) == 0:
                    if alias == "diccionario":
                        out.append(f"    {_name(result)}=(long)piton_dict_new(0);")
                        types[result] = "dict"
                    elif alias == "conjunto":
                        out.append(f"    {_name(result)}=(long)piton_set_new(0);")
                        types[result] = "set"
                    else:
                        out.append(f"    {_name(result)}=(long)piton_seq_new({seq_kind},0);")
                        types[result] = "list" if alias == "lista" else "tuple"
                    return out
                if len(values) != 1:
                    raise NativeBuildError(f"Linux {alias}() takes at most one argument")
                source_type = types.get(values[0], "int")
                if source_type.startswith("object:") and seq_kind == "PK_LIST":
                    # CONV_OBJECT_LIST_V1: lista(obj) cuando obj tiene __iter__.
                    _cls = source_type.split(":", 1)[1]
                    try:
                        _iter_cls = self._resolve_method(_cls, "__iter__")
                    except NativeBuildError:
                        _iter_cls = None
                    if _iter_cls:
                        _has_next = any(
                            "__next__" in self.classes.get(c, set())
                            for c in self.class_mro.get(_cls, [])
                        )
                        if _has_next:
                            _next_cls = self._resolve_method(_cls, "__next__")
                            out.append(f'    {{long _ci={_iter_cls}____iter__({self._value(values[0])}); {_name(result)}=piton_collect_via_next_fn(_ci,(long(*)(long)){_next_cls}____next__);}}')
                        else:
                            out.append(f'    {{long _ci={_iter_cls}____iter__({self._value(values[0])}); {_name(result)}=piton_collect_any_iter(_ci);}}')
                        types[result] = "list"
                        self._coll_elems[result] = "int"
                        return out
                if source_type == "generator" and seq_kind in {"PK_LIST", "PK_TUPLE"}:
                    # CONV_GEN_V1: lista(generador) materializa en el runtime.
                    # TUPLE_OF_ITER_V1: tupla(g()) — se materializa igual y se
                    # convierte a la kind de destino (CPython: tuple(g())).
                    _gen_tmp = f"(long)piton_collect_gen({self._value(values[0])})"
                    if seq_kind == "PK_TUPLE":
                        out.append(f"    {_name(result)}=(long)piton_conv_seq(PK_TUPLE,(PitonSlot){{{_gen_tmp},PK_LIST}});")
                        types[result] = "tuple"
                    else:
                        out.append(f"    {_name(result)}={_gen_tmp};")
                        types[result] = "list"
                    self._coll_elems[result] = "int"
                    return out
                if source_type == "iterator:calliter" and seq_kind in {"PK_LIST", "PK_TUPLE"}:
                    # CALLITER_LIST_V1: lista(iter(f, centinela)) materializa
                    # vía piton_calliter_next — el centinela levanta
                    # StopIteration (fin normal) y cualquier otra excepción
                    # del callable propaga (CPython: el excepción sale de
                    # list()).
                    _ci_tmp = f"(long)piton_collect_via_next_fn({self._value(values[0])},piton_calliter_next)"
                    if seq_kind == "PK_TUPLE":
                        out.append(f"    {_name(result)}=(long)piton_conv_seq(PK_TUPLE,(PitonSlot){{{_ci_tmp},PK_LIST}});")
                        types[result] = "tuple"
                    else:
                        out.append(f"    {_name(result)}={_ci_tmp};")
                        types[result] = "list"
                    self._coll_elems[result] = "int"
                    return out
                if source_type in {"iterator:enumerate", "iterator:reversed", "iterator:zip", "iterator:map", "iterator:filter"} and seq_kind in {"PK_LIST", "PK_TUPLE"}:
                    _which = {"iterator:enumerate": 0, "iterator:reversed": 1, "iterator:map": 2, "iterator:filter": 2, "iterator:zip": 3}[source_type]
                    _elemkind = "PK_TUPLE" if source_type in {"iterator:enumerate", "iterator:zip"} else "PK_INT"
                    _it_tmp = f"(long)piton_collect({_which},{self._value(values[0])},{_elemkind})"
                    if seq_kind == "PK_TUPLE":
                        # TUPLE_OF_ITER_V1: tupla(map(...)) — 2 archivos del
                        # corpus recursivo (operator.py, util.py) caian aqui.
                        out.append(f"    {_name(result)}=(long)piton_conv_seq(PK_TUPLE,(PitonSlot){{{_it_tmp},PK_LIST}});")
                        types[result] = "tuple"
                    else:
                        out.append(f"    {_name(result)}={_it_tmp};")
                        types[result] = "list"
                    self._coll_elems[result] = "tuple" if source_type in {"iterator:enumerate", "iterator:zip"} else "int"
                    return out
                if source_type == "none" and alias in {"lista", "list", "tuple", "tupla"}:
                    # CONV_NONE_V1: lista(none) — si la llamada que produjo
                    # `none` ya levanto excepcion, se propaga (helper lo
                    # detecta via flag); si no, TypeError como CPython
                    # ('NoneType' object is not iterable). Sin esto
                    # `intentar: lista(g()) excepto ValueError` era fail-closed
                    # en build aunque CPython captura el error de g().
                    out.append(f"    {_name(result)}=piton_list_from_none();")
                    types[result] = "list"
                    self._coll_elems[result] = "int"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if source_type in {"genexpr", "iterator:genexpr"} and alias in {"diccionario", "dict"}:
                    # DICT_FROM_PAIRS_V1: dict(genexpr de pares) — el genexpr
                    # ya esta materializado como secuencia de tuplas.
                    out.append(f"    {_name(result)}=piton_conv_dict_from_genexpr({self._value(values[0])});")
                    types[result] = "dict"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if source_type in {"iterator:list", "iterator:any"} and alias in {"lista", "list"}:
                    # LIST_FROM_ITER_V1: lista(reversed(range(n))) — el
                    # iterador generico se materializa entero.
                    out.append(f"    {_name(result)}=piton_collect_any_iter({self._value(values[0])});")
                    types[result] = "list"
                    self._coll_elems[result] = "int"
                    return out
                if source_type in {"", "int", "bool", "float"} and alias in {"lista", "list"}:
                    # LIST_OF_DYNAMIC_V1: lista(x) con x sin tipo estatico. El
                    # runtime decide (list/tuple/str se copian conservando la
                    # kind de cada elemento; dict/set/range/bytes fallan
                    # cerrados con exit 2). Un no-iterable levanta el TypeError
                    # de CPython. Misma politica que len/ord/slice dinamicos.
                    out.append(f"    {_name(result)}=piton_list_of_dynamic({self._value(values[0])});")
                    types[result] = "list"
                    self._coll_elems[result] = "int"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if source_type in {"", "int", "bool", "float"} and alias in {"tupla", "tuple", "conjunto", "set"}:
                    # TUPLE/SET_OF_DYNAMIC_V1: `tupla(5)`, `conjunto(5)` —
                    # CPython levanta TypeError en runtime para no-iterables;
                    # se materializa a lista (el helper ya lanza el TypeError
                    # exacto) y se convierte al destino. Si el helper falla
                    # (piton_exc_flag), no se convierte (evita SIGSEGV).
                    _dyn = f"piton_list_of_dynamic({self._value(values[0])})"
                    _tmp = f"_{_name(result)}_tmp"
                    out.append(f"    long {_tmp}={_dyn};")
                    out.append(f"    if(!piton_exc_flag){{")
                    if alias in {"tupla", "tuple"}:
                        out.append(f"        {_name(result)}=(long)piton_conv_seq(PK_TUPLE,(PitonSlot){{{_tmp},PK_LIST}});")
                        types[result] = "tuple"
                    else:
                        out.append(f"        {_name(result)}=(long)piton_conv_set((PitonSlot){{{_tmp},PK_LIST}});")
                        types[result] = "set"
                    out.append("    }")
                    self._coll_elems[result] = "int"
                    self._emit_exc_check(out, function, call_handler)
                    return out
                if source_type not in {"list", "tuple", "str", "set", "dict", "range"}:
                    raise NativeBuildError(f"Linux {alias}() cannot convert a '{source_type}' value")
                # COLL_ELEM_TYPE_V1: a conversion preserves the element kind, so
                # `x = lista('abc'); x[1]` prints 'b' instead of a pointer.
                elem_of_result = "str" if source_type == "str" else self._coll_elems.get(values[0], "int")
                if "|" in elem_of_result:
                    elem_of_result = "int"
                slot = self._slot(values[0], types)
                if seq_kind:
                    out.append(f"    {_name(result)}=(long)piton_conv_seq({seq_kind},{slot});")
                    types[result] = "list" if alias == "lista" else "tuple"
                    self._coll_elems[result] = elem_of_result
                elif alias == "conjunto":
                    out.append(f"    {_name(result)}=(long)piton_conv_set({slot});")
                    types[result] = "set"
                    self._coll_elems[result] = elem_of_result
                else:
                    out.append(f"    {_name(result)}=(long)piton_conv_dict({slot});")
                    types[result] = "dict"
                # CONV_EXC_CHECK_V1: una conversion que lanza en runtime
                # (lista(None) -> TypeError, lista(g()) con g lanzando) debe
                # routear al handler del try; sin este check la excepcion se
                # ignoraba y el programa salia con rc 0.
                self._emit_exc_check(out, function, call_handler)
            elif function_name in {"type", "tipo"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux type requires one argument")
                value_type = types.get(values[0], "int")
                if value_type.startswith("object:"):
                    # TIPO_OBJ_V1: `tipo(instancia)` must name the class, not the
                    # generic object slot: CPython prints `<class '__main__.P'>`.
                    # The backend does not track which module a class came from,
                    # so the module part is fixed to `__main__` (correct for
                    # classes declared in the main script, which is what the
                    # declared native subset covers).
                    class_name = value_type.split(":", 1)[1]
                    literal = json.dumps(f"<class '__main__.{class_name}'>")
                    out.append(f"    {_name(result)}=(long){literal};")
                else:
                    if value_type == "ellipsis":
                        # ELLIPSIS_SINGLETON_V1: tipo(...) imprime
                        # `<class 'ellipsis'>` como CPython (antes salia
                        # 'object' por el default — divergencia silenciosa).
                        out.append(f"    {_name(result)}=(long)" + json.dumps("<class 'ellipsis'>") + ";")
                    else:
                        out.append(f"    {_name(result)}=(long)piton_type_repr({self._kind(value_type)});")
                types[result] = "str"
            elif function_name in {"sorted", "ordenar"}:
                # SORTED_STR_V1: a str sorts like its list of 1-char strings.
                if len(values) == 1 and types.get(values[0]) == "str":
                    out.append(f"    {_name(result)}=piton_sorted_new((void*)piton_str_explode((const char*){self._value(values[0])}));")
                    types[result] = "list"
                    self._coll_elems[result] = "str"
                    return out
                # SORT_KEY_V1: con `key=` llegan 3 valores (coleccion, clave,
                # reversa). La clave ahora SÍ se aplica (helper C que decora,
                # ordena por la clave y devuelve los originales).
                _sort_key = None
                if len(values) == 3:
                    _sort_key = values[1]
                    values = [values[0], values[2]]
                if len(values) not in {1, 2} or types.get(values[0]) in {"int", "str", "bool", "none", "float", "complex", "bytes", "bytearray"}:
                    # SORTED_ANY_ITERABLE_V1: sorted() acepta cualquier
                    # iterable en CPython, no solo lista/tupla/range. Rechazamos
                    # solo scalares evidentes; el runtime manejara la conversion
                    # o fallara con TypeError claro.
                    raise NativeBuildError("native sorted currently requires one list, tuple or range")
                if _sort_key is not None:
                    _src = f"piton_conv_seq(PK_LIST,{self._slot(values[0], types)})" if types.get(values[0]) == "range" else self._value(values[0])
                    out.append(f"    {_name(result)}=piton_sorted_key_new((void*){_src},{self._value(_sort_key)});")
                    self._emit_exc_check(out, function, call_handler)
                elif types.get(values[0]) == "range":
                    out.append(f"    {_name(result)}=piton_sorted_new((void*)piton_conv_seq(PK_LIST,{self._slot(values[0], types)}));")
                elif types.get(values[0]) == "set":
                    # SORTED_SET_V1: sorted(set) convierte el set a lista antes de ordenar.
                    _tmp = f"_{_name(result)}_set"
                    out.append(f"    long {_tmp}=piton_list_of_dynamic({self._value(values[0])});")
                    out.append(f"    if(!piton_exc_flag){{")
                    out.append(f"        {_name(result)}=(long)piton_sorted_new((void*){_tmp});")
                    out.append("    }")
                else:
                    out.append(f"    {_name(result)}=piton_sorted_new((void*){self._value(values[0])});")
                if len(values) == 2:
                    # SORT_KW_V1: reverse flag (`reversa=`/`reverse=`).
                    out.append(f"    if({self._value(values[1])})piton_seq_reverse((PitonSeq*){_name(result)});")
                types[result] = "list"
                ek = self._coll_elems.get(values[0]) or self._coll_elems.get(aliases.get(values[0], values[0]))
                if ek is not None:
                    self._coll_elems[result] = ek
                tx = self._tuple_list_elems.get(values[0]) or self._tuple_list_elems.get(aliases.get(values[0], values[0]))
                if tx is not None:
                    self._tuple_list_elems[result] = tx
            elif function_name in {"hasattr", "tiene_atr"}:
                if len(values) != 2:
                    raise NativeBuildError("hasattr requires 2 arguments (object, name)")
                obj_val = values[0]
                name_val = values[1]
                obj_type = types.get(obj_val, "")
                name_alias = aliases.get(name_val, name_val)
                name_str = None
                if isinstance(name_alias, str) and not name_alias.startswith("%"):
                    name_str = name_alias
                elif name_val in getattr(self, "_fn_consts", {}):
                    cv = self._fn_consts[name_val]
                    if isinstance(cv, str):
                        name_str = cv
                elif name_alias in getattr(self, "_fn_consts", {}):
                    cv = self._fn_consts[name_alias]
                    if isinstance(cv, str):
                        name_str = cv
                if obj_type.startswith("object:") and name_str:
                    cls_name = obj_type.split(":", 1)[1]
                    mro = self.class_mro.get(cls_name, [cls_name])
                    has_meth = any(name_str in self.classes.get(c, set()) for c in mro)
                    if has_meth:
                        out.append(f"    {_name(result)}=1;")
                        types[result] = "bool"
                        return out
                out.append(f"    {_name(result)}=piton_hasattr({self._value(obj_val)},(const char*){self._value(name_val)});")
                types[result] = "bool"
            elif function_name in {"getattr", "obtener_atr"}:
                if len(values) not in {2, 3}:
                    raise NativeBuildError("getattr requires 2 or 3 arguments (object, name [, default])")
                obj_val = values[0]
                name_val = values[1]
                has_def = 1 if len(values) == 3 else 0
                def_val = self._value(values[2]) if has_def else "0"
                out.append(f"    {_name(result)}=piton_getattr({self._value(obj_val)},(const char*){self._value(name_val)},{has_def},{def_val});")
                self._emit_exc_check(out, function, call_handler)
                # GETATTR_DEFAULT_TYPE_V1: con default, el resultado es el
                # default cuando el atributo no existe — el tipo del default
                # (abc: `getattr(scls, '__abstractmethods__', ())` -> tupla
                # vacia; sin esto el for sobre el resultado fallaba).
                types[result] = "int"
                if has_def:
                    _dft = types.get(values[2])
                    if _dft and _dft not in {"int", "bool"}:
                        types[result] = _dft
                        if _dft in {"list", "tuple"}:
                            self._coll_elems[result] = self._coll_elems.get(values[2], "int")
            elif function_name in {"setattr", "fijar_atr", "establecer_atr"}:
                if len(values) != 3:
                    raise NativeBuildError("setattr requires 3 arguments (object, name, value)")
                obj_val = values[0]
                name_val = values[1]
                val_arg = values[2]
                out.append(f"    piton_setattr({self._value(obj_val)},(const char*){self._value(name_val)},{self._slot(val_arg, types)});")
                self._emit_exc_check(out, function, call_handler)
                out.append(f"    {_name(result)}=0;")
                types[result] = "none"
            elif function_name in {"isinstance", "es_instancia"}:
                if len(values) != 2:
                    raise NativeBuildError("isinstance requires 2 arguments (object, class_or_tuple)")
                expr = self._build_isinstance_expr(values[0], values[1], types, aliases)
                out.append(f"    {_name(result)}={expr};")
                types[result] = "bool"
            elif function_name in {"issubclass", "es_subclase"}:
                if len(values) != 2:
                    raise NativeBuildError("issubclass requires 2 arguments (class, class_or_tuple)")
                expr = self._build_issubclass_expr(values[0], values[1], aliases)
                out.append(f"    {_name(result)}={expr};")
                types[result] = "bool"
            else:
                # BUILTIN_MARKER_V1: a builtin name that reached the generic
                # call path has no C value (its load is a marker). Calling it
                # here used to jump through an uninitialized variable — SIGSEGV.
                callee_name = aliases.get(args[0], args[0])
                if types.get(args[0]) == "builtin":
                    raise NativeBuildError(
                        f"Linux native call to builtin '{callee_name}' is not supported in this position"
                    )
                for value in values:
                    if types.get(value) == "builtin":
                        raise NativeBuildError(
                            f"Linux native call passes builtin '{aliases.get(value, value)}' as an argument (unsupported)"
                        )
                values = self._complete_call_args(function_name, list(values))
                if function_name in self.function_names:
                    return self._emit_user_function_call(out, result, function_name, values, call_handler, function, types)
                else:
                    # CALLABLE_PROTOCOL_V1: calling a statically-typed class
                    # instance routes to <Class>__call__ if defined.
                    caller_type = types.get(args[0], "")
                    call_owner = None
                    if caller_type.startswith("object:"):
                        cls = caller_type.split(":", 1)[1]
                        for candidate in self.class_mro.get(cls, []):
                            if "__call__" in self.classes.get(candidate, set()):
                                call_owner = candidate
                                break
                        if call_owner is None and "__call__" in self.classes.get(cls, set()):
                            call_owner = cls
                    if call_owner is not None:
                        target = _name(call_owner + "__" + "__call__")
                        call_values = [args[0], *values]
                        if self.function_frame_abi.get(f"{call_owner}__" + "__call__", False):
                            args_c = ",".join(self._value(v) for v in call_values)
                            out.append(f'    {{long _cv_args[]={{ {args_c} }}; {_name(result)}=piton_frame_call((long)&{target},{len(call_values)},_cv_args);}}')
                        else:
                            encoded_values = ",".join(self._value(v) for v in call_values)
                            out.append(f"    {_name(result)}={target}({encoded_values});")
                        # CALL_PROPAGATE_V1: route a raise from the callee.
                        self._emit_exc_check(out, function, call_handler)
                    else:
                        argc = len(values)
                        arg_values = ",".join(self._value(value) for value in values)
                        out.append(f"    {{long _frame_args[]={{ {arg_values} }};")
                        out.append(
                            f"    {_name(result)}=piton_closure_call_frame({self._value(args[0])},{argc},_frame_args);"
                        )
                        out.append("    }")
                        # CALL_PROPAGATE_V1: route a raise from the callee.
                        self._emit_exc_check(out, function, call_handler)
                        # CLOSURE_CALL_RETTYPE_V1: el resultado de una llamada
                        # por dispatcher llevaba siempre "int"; cuando el valor
                        # es una closure de una función conocida, el tipo
                        # inferido de retorno es más preciso (un decorador
                        # apilado devuelve str y se imprimía el puntero).
                        _rt = "int"
                        _callee = aliases.get(args[0], args[0])
                        if isinstance(_callee, str):
                            if _callee in self.function_return_types:
                                _rt = self.function_return_types[_callee]
                            else:
                                # el nombre global fue rebindado por un
                                # decorador a __decorated__<nombre>
                                _dec = "__decorated__" + _callee
                                if _dec in self.function_return_types:
                                    _rt = self.function_return_types[_dec]
                        types[result] = _rt
        elif op == "return":
            if getattr(function, "is_coroutine", False):
                if args[0] is not None and args[0] != "None":
                    out.append(f"    piton_gen->finished=1;")
                    out.append(f"    return {self._value(args[0])};")
                else:
                    out.append("    piton_gen->finished=1;")
                    out.append("    return 0;")
                return out
            if getattr(function, "is_generator", False):
                if args[0] is not None and args[0] != "None":
                    out.append(f"    piton_gen->slots[62]={self._value(args[0])};")
                out.append("    piton_gen->finished=1;")
                out.append("    return 0;")
                return out
            if isinstance(args[0], str) and types.get(args[0]) == "builtin":
                # BUILTIN_MARKER_V1: returning a builtin marker would hand the
                # caller an uninitialized C value.
                raise NativeBuildError(
                    f"Linux native return of builtin '{aliases.get(args[0], args[0])}' is not supported"
                )
            out.append(f"    return {self._value(args[0])};")
        elif op == "object_new":
            cls_name = args[0]
            parent = args[1] if len(args) > 1 else None
            parent_str = f'"{parent}"' if parent else "0"
            finalizer_class = None
            try:
                finalizer_class = self._resolve_method(cls_name, "__del__")
            except NativeBuildError:
                finalizer_class = None
            if finalizer_class:
                finalizer_name = f"{finalizer_class}____del__"
                params = self.function_params.get(finalizer_name) or []
                if len(params) != 1:
                    raise NativeBuildError("Linux __del__ must take exactly self (FINALIZERS_V1)")
                if self.function_frame_abi.get(finalizer_name, False):
                    raise NativeBuildError("Linux __del__ cannot use the frame ABI yet (FINALIZERS_V1)")
                out.append(f'    {_name(result)}=(long)piton_object_new_finalized("{cls_name}",{parent_str},(long)&{_name(finalizer_name)});')
            else:
                out.append(f'    {_name(result)}=(long)piton_object_new("{cls_name}",{parent_str});')
            # CLASS_ATTR_V1: instances of a class with class-level assignments
            # get those attributes set at creation (matching CPython default).
            if hasattr(self, "_class_attrs_map"):
                for attr_name, val_expr in self._class_attrs_map.get(cls_name, {}).items():
                    out.append(f'    piton_object_set((PitonObject*){_name(result)},"{attr_name}",{val_expr});')
            self._emit_class_bases_attribute(out, result, cls_name)
            types[result] = f"object:{cls_name}"
        elif op == "cell_new":
            value_arg = args[0]
            out.append("    {")
            out.append("        long*cell=(long*)piton_alloc(16);")
            if value_arg is None:
                out.append("        cell[0]=0;")
                self._cell_types[result] = "none"
            else:
                out.append(f"        cell[0]={self._value(value_arg)};")
                self._cell_types[result] = types.get(value_arg, "int")
            out.append("        cell[1]=0;")
            out.append(f"        {_name(result)}=(long)cell;")
            out.append("    }")
            types[result] = "cell"
        elif op == "cell_load":
            cell_ptr_name = args[0]
            out.append(f"    {_name(result)}=((long*){_name(cell_ptr_name)})[0];")
            types[result] = self._cell_types.get(cell_ptr_name, "int")
        elif op == "cell_store":
            cell_ptr_name, value_arg = args
            out.append(f"    ((long*){_name(cell_ptr_name)})[0]={self._value(value_arg)};")
            self._cell_types[cell_ptr_name] = types.get(value_arg, "int")
        elif op == "closure_new":
            lifted_name, n_args, capture_ops, has_vararg = args[:4]
            has_kwarg = args[4] if len(args) > 4 else 0
            cell_values = [self._value(cap) for cap in capture_ops]
            out.append(
                f"    {_name(result)}=piton_closure_new_frame((long)&{_name(lifted_name)},{n_args},"
                f"{len(capture_ops)},(long[]){{ {','.join(cell_values)} }},{int(has_vararg)},{int(has_kwarg)});"
            )
            types[result] = "closure"
        elif op == "dynamic_call":
            # DYNAMIC_CALL_V1: llamada a un callable desconocido en build time;
            # el runtime arma el frame con los args y el dict de keywords.
            callee, pos_seq, star_seq, kwargs_dict = args[0], args[1], args[2], args[3]
            # NONE_SINGLETON_V2: estos None del MIR significan "ausente" (sin
            # star args, sin kwargs), no el valor None del usuario. El runtime
            # usa 0 como centinela de ausencia; un singleton aqui seria
            # desreferenciado como seq/dict.
            _dyn = lambda v: "0" if v is None else self._value(v)
            out.append(
                f"    {_name(result)}=piton_call_dynamic({self._value(callee)},"
                f"{_dyn(pos_seq)},{_dyn(star_seq)},{_dyn(kwargs_dict)});"
            )
            types[result] = "int"
            self._emit_exc_check(out, function, args[4] if len(args) > 4 else None)
            return out
        elif op == "frame_call":
            callee, call_args = args
            # FRAME_CALL_DEFAULTS_V1: una funcion levantada (closure) con
            # defaults los recibe en el call-site (el default puede ser una
            # referencia: NON_CONST_DEFAULT_V1).
            _fname = aliases.get(callee, callee)
            if isinstance(_fname, str) and _fname in self.function_defaults:
                call_args = self._complete_call_args(_fname, list(call_args))
            self._reject_complex_args(call_args, types, "a lifted frame-ABI function")
            arg_values = ",".join(self._value(arg) for arg in call_args)
            out.append(f"    {{long _frame_args[]={{ {arg_values} }};")
            out.append(
                f"    {_name(result)}=piton_frame_call({self._value(callee)},{len(call_args)},_frame_args);"
            )
            out.append("    }")
            types[result] = "int"
        elif op == "closure_call":
            callee, packed = args
            argc, *call_args = packed
            self._reject_complex_args(call_args, types, "a closure")
            if len(call_args) > 4:
                raise NativeBuildError("Linux closure calls with more than four arguments are not supported yet")
            arg_values = [self._value(arg) for arg in call_args]
            arg_values += ["0"] * (4 - len(arg_values))
            out.append(
                f"    {_name(result)}=piton_closure_call6({self._value(callee)},{argc},{','.join(arg_values)});"
            )
            types[result] = "int"
        elif op == "slice_store":
            # BYTEARRAY_SLICE_ASSIGN_V1 / LIST_SLICE_ASSIGN_V1
            collection, lo, hi, step, value = args
            coll_type = types.get(collection, "")
            if coll_type == "bytearray":
                vtype = types.get(value, "")
                if vtype in {"bytes", "bytearray"}:
                    length_expr = f"piton_bytes_len_of((long){self._value(value)})"
                    data_expr = f"(const unsigned char*)piton_bytes_data_ptr_of((long){self._value(value)})"
                    out.append(
                        f"    piton_bytearray_slice_assign((PitonByteArray*){self._value(collection)},"
                        f"{self._value(lo)},{self._value(hi)},{self._value(step) if step is not None else 1},"
                        f"{data_expr},{length_expr});"
                    )
                    return out
                raise NativeBuildError("Linux bytearray slice assignment requires bytes/bytearray value")
            # SLICE_DYNAMIC_V1: destino sin tipo anotado (protocols.py).
            out.append(
                f"    piton_slice_assign_dynamic({self._value(collection)},"
                f"{self._value(lo)},{self._value(hi)},"
                f"{self._value(step) if step is not None else 1},"
                f"(long){self._value(value)});"
            )
            return out
        elif op == "subscript_store":
            collection, index, value = args
            coll_type = types.get(collection, "")
            if coll_type in {"list", "tuple"}:
                if types.get(index, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux subscript store requires an int index")
                out.append(f"    piton_seq_set((PitonSeq*){self._value(collection)},{self._value(index)},{self._slot(value, types)});")
            elif coll_type in {"dict", "dict:module"}:
                out.append(f"    piton_dict_set((PitonDict*){self._value(collection)},{self._slot(index, types)},{self._slot(value, types)});")
            elif coll_type == "bytearray":
                if types.get(index, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux bytearray subscript store requires an int index")
                if types.get(value, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux bytearray item assignment requires an int value")
                out.append(f"    piton_bytearray_set((PitonByteArray*){self._value(collection)},{self._value(index)},{self._value(value)});")
            elif coll_type == "memoryview":
                # MEMORYVIEW_SET_V1: m[i] = v escribe en el buffer subyacente
                # (si es bytearray); sobre bytes es TypeError en CPython.
                if types.get(index, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux memoryview subscript store requires an int index")
                if types.get(value, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux memoryview item assignment requires an int value")
                out.append(f"    piton_memoryview_set((PitonMemoryView*){self._value(collection)},{self._value(index)},{self._value(value)});")
            elif coll_type == "str":
                # SUBSCRIPT_STORE_STR_V1: CPython lanza TypeError en runtime
                # (tutorial/introduction.py: word[0] = 'J' sobre un str, que
                # el try/except del ejemplo atrapa). Mismo patron que
                # SUBSCRIPT_STORE_INT_V1: el runtime confirma que el puntero
                # NO es secuencia y levanta el TypeError exacto de CPython.
                out.append("    {")
                out.append(f"        long _ss_coll={self._value(collection)};")
                out.append("        int _ss_ok=0;")
                out.append("        if(_ss_coll&&piton_maybe_ptr(_ss_coll)){")
                out.append("            long*_ss_p=(long*)_ss_coll;long _ss_k=_ss_p[1];")
                out.append("            _ss_ok=(_ss_k==PK_LIST||_ss_k==PK_TUPLE);")
                out.append("        }")
                out.append("        if(_ss_ok){")
                out.append(f"            piton_seq_set((PitonSeq*)_ss_coll,{self._value(index)},{self._slot(value, types)});")
                out.append("        } else {")
                out.append('        piton_raise_set("TypeError","\'str\' object does not support item assignment");')
                out.append("        }")
                out.append("    }")
            elif coll_type == "int":
                # SUBSCRIPT_STORE_INT_V1: `x[0]=v` con x estaticamente int.
                # CPython levanta TypeError en runtime, no error de build.
                # Chequeamos en runtime: si es lista/tupla se asigna (el
                # sistema de tipos pudo equivocarse); si no, TypeError con el
                # mensaje exacto de CPython. Afecta _parseaddr.py y
                # colorchooser.py del corpus recursivo.
                out.append("    {")
                out.append(f"        long _ss_coll={self._value(collection)};")
                out.append("        int _ss_ok=0;")
                out.append("        if(_ss_coll&&piton_maybe_ptr(_ss_coll)){")
                out.append("            long*_ss_p=(long*)_ss_coll;long _ss_k=_ss_p[1];")
                out.append("            _ss_ok=(_ss_k==PK_LIST||_ss_k==PK_TUPLE);")
                out.append("        }")
                out.append("        if(_ss_ok){")
                out.append(f"            piton_seq_set((PitonSeq*)_ss_coll,{self._value(index)},{self._slot(value, types)});")
                self._emit_exc_check(out, function, None)
                out.append("        } else {")
                out.append('        piton_raise_set("TypeError","\'int\' object does not support item assignment");')
                self._emit_exc_check(out, function, None)
                out.append("        }")
                out.append("    }")
            elif coll_type in ("", "int", "slot", "builtin") or coll_type.startswith(("object:", "class:")):
                # SUBSCRIPT_STORE_UNKNOWN_V1: el tipo estatico del target no
                # se conoce (pipes.py: list[-1][-1] = x sobre elem de lista
                # heterogenea). Misma politica que SUBSCRIPT_STORE_INT_V1:
                # chequeo en runtime — si el puntero es lista/tupla se
                # asigna; si no, TypeError con el texto de CPython.
                out.append("    {")
                out.append(f"        long _ss_coll={self._value(collection)};")
                out.append("        int _ss_ok=0;")
                out.append("        if(_ss_coll&&piton_maybe_ptr(_ss_coll)){")
                out.append("            long*_ss_p=(long*)_ss_coll;long _ss_k=_ss_p[1];")
                out.append("            _ss_ok=(_ss_k==PK_LIST||_ss_k==PK_TUPLE);")
                out.append("        }")
                out.append("        if(_ss_ok){")
                out.append(f"            piton_seq_set((PitonSeq*)_ss_coll,{self._value(index)},{self._slot(value, types)});")
                self._emit_exc_check(out, function, None)
                out.append("        } else {")
                out.append('        piton_raise_set("TypeError","object does not support item assignment");')
                self._emit_exc_check(out, function, None)
                out.append("        }")
                out.append("    }")
            else:
                raise NativeBuildError(f"Linux subscript store not supported for {coll_type}")
        elif op == "set_attr":
            obj, attr, val = args
            owner_type = types.get(obj, "")
            prop_class = self._resolve_property_class(owner_type, attr)
            if prop_class is not None:
                setter = self.class_properties[prop_class][attr].get("setter")
                if not setter:
                    raise NativeBuildError(f"property '{attr}' of '{owner_type.split(':', 1)[1]}' object has no setter")
                out.append(f'    {_name(setter)}({self._value(obj)},{self._value(val)});')
            else:
                # INSTANCE_ATTR_TYPES_V1: `self.x = valor` anota el tipo del
                # atributo de la clase (para que un get_atrib posterior sepa
                # el tipo del valor).
                _cls = owner_type.split(":", 1)[1] if owner_type.startswith("object:") else ""
                if _cls in self.classes:
                    _vt = types.get(val, "")
                    if _vt and _vt not in {"int", "bool"}:
                        self._instance_attr_types.setdefault(_cls, {}).setdefault(attr, _vt)
                # MODULE_ATTR_TYPES_V1: track module attribute types for get_attr
                if owner_type == "object:module":
                    _vt = types.get(val, "")
                    if _vt and _vt != "none":
                        self.module_attr_types[attr] = _vt
                # ATTRIBUTE_LOOKUP_V2: __setattr__ hook routes normal stores;
                # the hook body itself stores raw (bypass, mirror of Win).
                hook = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.class_mro.get(class_name, []):
                        if "__setattr__" in self.classes.get(candidate, set()):
                            hook = candidate
                            break
                    if hook is None and "__setattr__" in self.classes.get(class_name, set()):
                        hook = class_name
                current_is_hook = function.name.endswith("__setattr__") and hook is not None
                if hook is not None and not current_is_hook:
                    out.append(f'    ((long(*)(long,long,long))(long)&{_name(hook+"__"+"__setattr__")})({self._value(obj)},(long){json.dumps(attr)},{self._value(val)});')
                else:
                    # SET_ATTR_DYN_V1: un nombre que llega como temporizador
                    # MIR (ej. `super().__setattr__(n, v)`) es un string en
                    # runtime, no un literal: emitirlo entre comillas escribia
                    # un atributo llamado "%t5".
                    attr_expr = (
                        f'(const char*){self._value(attr)}'
                        if isinstance(attr, str) and attr.startswith('%') else f'"{attr}"'
                    )
                    out.append(f'    piton_object_set((PitonObject*){self._value(obj)},{attr_expr},{self._slot(val, types)});')
        elif op == "get_attr":
            obj, attr = args
            owner_type = types.get(obj, "")
            if owner_type.startswith("class:") and attr == "__bases__":
                # CLASS_BASES_V1: `cls.__bases__` -> la tupla de las bases
                # DIRECTAS (abc: update_abstractmethods las recorre). Cada
                # base es un objeto de clase del runtime.
                _cls_name = owner_type.split(":", 1)[1]
                _bases = list(self.class_bases.get(_cls_name, ()))
                out.append(f"    {{PitonSeq*_bs=piton_seq_new(PK_TUPLE,{len(_bases)});")
                for _bi, _base in enumerate(_bases):
                    out.append(
                        f'    piton_seq_put(_bs,{_bi},piton_slot((long)piton_object_new("{_base}",0),PK_OBJECT));'
                    )
                out.append(f"    {_name(result)}=(long)_bs;}}")
                types[result] = "tuple"
                self._coll_elems[result] = "object"
                return out
            if owner_type.startswith("object:"):
                # GETATTRIBUTE_V1: __getattribute__ intercepta TODO acceso a
                # atributo (a diferencia de __getattr__, que solo corre cuando
                # la búsqueda normal falla). CPython: obj.x -> el hook.
                _gacls = owner_type.split(":", 1)[1]
                _gahit = None
                for _cand in self.class_mro.get(_gacls, [_gacls]):
                    if "__getattribute__" in self.classes.get(_cand, set()):
                        _gahit = _cand
                        break
                if _gahit is None and "__getattribute__" in self.classes.get(_gacls, set()):
                    _gahit = _gacls
                if _gahit is not None:
                    _gatarget = _name(_gahit + "____getattribute__")
                    _gaframe = self.function_frame_abi.get(_gahit + "____getattribute__", False)
                    if _gaframe:
                        out.append(f'    {{long _ga_args[]={{ {self._value(obj)},(long){json.dumps(attr)} }}; {_name(result)}=piton_frame_call((long)&{_gatarget},2,_ga_args);}}')
                    else:
                        out.append(f"    {_name(result)}={_gatarget}({self._value(obj)},(long){json.dumps(attr)});")
                    types[result] = "int"
                    return out
            if owner_type == "memoryview" and attr == "readonly":
                # MEMORYVIEW_READONLY_V1: True sobre bytes, False sobre bytearray.
                out.append(f"    {_name(result)}=(((PitonMemoryView*){self._value(obj)})->is_bytearray?0:1);")
                types[result] = "bool"
                return out
            if owner_type == "memoryview" and attr == "nbytes":
                # MEMORYVIEW_NBYTES_V1: total de bytes de la vista.
                out.append(f"    {_name(result)}=(((PitonMemoryView*){self._value(obj)})->length);")
                types[result] = "int"
                return out
            if owner_type == "memoryview" and attr == "itemsize":
                # MEMORYVIEW_ITEMSIZE_V1: vistas 'B' -> 1 byte por elemento.
                out.append(f"    {_name(result)}=1;")
                types[result] = "int"
                return out
            if attr == "__next__" and owner_type.startswith("iterator"):
                # ITER_NEXT_ATTR_V1: `it.__next__` como bound method (heapq:
                # `next = it.__next__` + `next()` posterior). Los helpers de
                # iter_next son unary; el bound method con n_args=0 los invoca
                # con self en el primer registro (mismo ABI que los métodos
                # resueltos arriba).
                _iter_next_helper = {
                    "iterator:str": "piton_str_iterator_next",
                    "iterator:bytes": "piton_bytes_iterator_next",
                    "iterator:memoryview": "piton_memoryview_iter_next",
                    "iterator:any": "piton_iterator_next_any",
                    "iterator:enumerate": "piton_enumerate_next",
                    "iterator:reversed": "piton_reversed_next",
                    "iterator:zip": "piton_zip_next",
                    "iterator:map": "piton_callback_iterator_next",
                    "iterator:filter": "piton_callback_iterator_next",
                    "iterator:calliter": "piton_calliter_next",
                }.get(owner_type, "piton_iterator_next_any")
                out.append(
                    f"    {_name(result)}=piton_bound_method_new((long)&{_iter_next_helper},0,{self._value(obj)});"
                )
                types[result] = "closure"
                return out
            if owner_type == "slice" and attr in {"start", "stop", "step"}:
                # SLICE_ATTR_V1: s.start / s.stop / s.step — None si el bound
                # está ausente. CPython los expone como enteros o None.
                _fld = {"start": "has_start", "stop": "has_stop", "step": "has_step"}[attr]
                _val = attr
                # SLICE_ATTR_V1: bounds presentes se devuelven como int. Un
                # bound AUSENTE (None) no es representable en el modelo sin tag:
                # falla cerrado en build en vez de imprimir 0 (que sería una
                # divergencia silenciosa contra CPython, que imprime None).
                out.append(
                    f"    if(!((PitonSlice*){self._value(obj)})->{_fld}){{"
                    f'piton_write(2,"TypeError: slice.{attr} of a slice with an absent bound is not supported in the native subset\\n",110);piton_exit(2);}}'
                )
                out.append(f"    {_name(result)}=((PitonSlice*){self._value(obj)})->{_val};")
                types[result] = "int"
                return out
            if owner_type == "complex" and attr in {"real", "imag"}:
                # COMPLEX_V1: (2+3j).real / .imag son floats (2.0 / 3.0). El slot
                # de un float son los BITS del double, no el double truncado.
                helper = "piton_cx_re" if attr == "real" else "piton_cx_im"
                out.append(f"    {_name(result)}=piton_double_bits({helper}({self._value(obj)}));")
                types[result] = "float"
                return out
            if owner_type in {"float", "int"} and attr in {"real", "imag"}:
                # NUMERIC_REAL_IMAG_V1: float/int .real/.imag como CPython —
                # .real es el propio valor; .imag es 0.0 (float) / 0 (int).
                # Sin esto, 1.5.real caia al guard scalar (AttributeError).
                if attr == "real":
                    out.append(f"    {_name(result)}={self._value(obj)};")
                elif owner_type == "float":
                    out.append(f"    {_name(result)}=piton_double_bits(0.0);")
                else:
                    out.append(f"    {_name(result)}=0;")
                types[result] = owner_type
                return out
            if owner_type in {"list", "tuple"} and attr == "append":
                # BUILTIN_BOUND_METHOD_V1: `m = xs.append` como valor. El
                # wrapper recibe (self, value) por el ABI de bound method y la
                # kind del valor se deriva en runtime (piton_slot_guess).
                if owner_type == "tuple":
                    raise NativeBuildError("tuple has no attribute 'append'")
                out.append(
                    f"    {_name(result)}=piton_bound_method_new((long)&piton_list_append_bound,1,{self._value(obj)});"
                )
                types[result] = "closure"
                # siembra estatica: el nombre dueño se anota para tipar su
                # elemento cuando se llame (heapq: `h_append = h.append`).
                _owner_name = aliases.get(obj, obj)
                if isinstance(_owner_name, str):
                    self._builtin_bound[result] = _owner_name
                return out
            prop_class = self._resolve_property_class(owner_type, attr)
            if prop_class is not None:
                getter = self.class_properties[prop_class][attr]["getter"]
                out.append(f'    {_name(result)}={_name(getter)}({self._value(obj)});')
                types[result] = "int"
            else:
                if owner_type == "closure" and attr == "__self__":
                    out.append(f'    {_name(result)}=piton_bound_method_self({self._value(obj)});')
                    types[result] = "int"
                    return out
                resolved_method = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.class_mro.get(class_name, []):
                        if attr in self.classes.get(candidate, set()):
                            resolved_method = candidate
                            break
                    if resolved_method is None and attr in self.classes.get(class_name, set()):
                        resolved_method = class_name
                if resolved_method is not None:
                    params = self.function_params.get(f"{resolved_method}__{attr}") or []
                    if not params:
                        raise NativeBuildError(f"bound method '{attr}' has no native signature")
                    target = f"{resolved_method}__{attr}"
                    if target in self.static_methods:
                        out.append(f'    {_name(result)}=(long)&{_name(target)};')
                        types[result] = "closure"
                        return out
                    elif target in self.class_methods:
                        cls_val = f"(long)&{_name(resolved_method)}" if resolved_method in self.function_names else f"(long){_name(resolved_method)}"
                        out.append(f'    {_name(result)}=piton_bound_method_new((long)&{_name(target)},{len(params)-1},{cls_val});')
                        types[result] = "closure"
                        return out
                    elif self.function_frame_abi.get(target, False):
                        out.append(f'    {_name(result)}=piton_closure_new_frame((long)&{_name(target)},{len(params)-1},1,(long[]){{(long){self._value(obj)}}},0,0);')
                    else:
                        out.append(f'    {_name(result)}=piton_bound_method_new((long)&{_name(target)},{len(params)-1},(long){self._value(obj)});')
                    # CALLITER_ELEM_V1: el closure sabe de que funcion viene.
                    self._closure_origin[result] = target
                    types[result] = "closure"
                    return out
                # ATTRIBUTE_LOOKUP_V2: __getattr__ hook after method resolution
                getattr_class = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.class_mro.get(class_name, []):
                        if "__getattr__" in self.classes.get(candidate, set()):
                            getattr_class = candidate
                            break
                    if getattr_class is None and "__getattr__" in self.classes.get(class_name, set()):
                        getattr_class = class_name
                if getattr_class is not None:
                    out.append(f'    {_name(result)}=piton_object_lookup((PitonObject*){self._value(obj)},{json.dumps(attr)},(long)&{_name(getattr_class+"__"+"__getattr__")});')
                    types[result] = "int"
                    return out
                # GETATTR_SCALAR_GUARD_V1: leer un atributo sobre un valor que
                # NO es objeto (str/int/float/...) caia al fallback y leia un
                # char*/entero como PitonObject* -> SIGSEGV. CPython lanza
                # AttributeError: se emite lo mismo.
                # GETATTR_SCALAR_GUARD_V1: 'int'/'bool' NO están en la lista:
                # son el tipo POR DEFECTO del modelo, y un valor sin tipo
                # estático resuelto puede ser perfectamente un objeto (los
                # tests lo ejercen). Solo se rechazan los tipos que jamás
                # pueden ser instancias.
                _scalar_kinds = {"str", "float", "complex", "none",
                                 "bytes", "bytearray", "bigint", "range", "list",
                                 "tuple", "dict", "set", "set:frozen"}
                if owner_type in _scalar_kinds and not (
                    owner_type == "complex" and attr in {"real", "imag"}
                ):
                    attr_name = attr if isinstance(attr, str) else "?"
                    self._runtime_type_error(
                        out, f"'{owner_type}' object has no attribute '{attr_name}'"
                    )
                    types[result] = "int"
                    return out
                if attr in {"__bases__", "__mro__"}:
                    # CLASS_BASES_V1: el atributo lo lleva la clase (creada con
                    # su tupla de bases y su MRO) — el receptor puede ser
                    # dinamico (un parametro que es una clase).
                    out.append(
                        f'    {_name(result)}=piton_object_get((PitonObject*){self._value(obj)},"{attr}").bits;'
                    )
                    types[result] = "tuple"
                    self._coll_elems[result] = "object"
                    return out
                attr_expr_g = (
                    f'(const char*){self._value(attr)}'
                    if isinstance(attr, str) and attr.startswith('%') else f'"{attr}"'
                )
                out.append(f'    {_name(result)}=piton_object_get((PitonObject*){self._value(obj)},{attr_expr_g}).bits;')
                if owner_type == "object:module":
                    types[result] = self.module_attr_types.get(attr, "int")
                    # Track module name for nested module method calls
                    parent_name = aliases.get(obj, "")
                    if parent_name and isinstance(parent_name, str) and "." not in parent_name:
                        # Parent is a top-level module, child is parent.attr
                        aliases[result] = f"{parent_name}.{attr}"
                    elif parent_name and isinstance(parent_name, str) and "." in parent_name:
                        # Parent is already a nested module
                        aliases[result] = f"{parent_name}.{attr}"
                else:
                    types[result] = "int"
                # INSTANCE_ATTR_TYPES_V1: el tipo del atributo de instancia
                # (deducido de `self.x = valor`).
                _icls = owner_type.split(":", 1)[1] if owner_type.startswith("object:") else ""
                if _icls in self.classes:
                    _it = self._instance_attr_types.get(_icls, {}).get(attr)
                    if _it:
                        types[result] = _it
        elif op == "del_attr":
            obj, attr = args
            owner_type = types.get(obj, "")
            prop_class = self._resolve_property_class(owner_type, attr)
            if prop_class is not None:
                deleter = self.class_properties[prop_class][attr].get("deleter")
                if not deleter:
                    raise NativeBuildError(f"property '{attr}' of '{owner_type.split(':', 1)[1]}' object has no deleter")
                out.append(f'    {_name(deleter)}({self._value(obj)});')
            else:
                hook = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.class_mro.get(class_name, []):
                        if "__delattr__" in self.classes.get(candidate, set()):
                            hook = candidate
                            break
                    if hook is None and "__delattr__" in self.classes.get(class_name, set()):
                        hook = class_name
                current_is_hook = function.name.endswith("__delattr__") and hook is not None
                if hook is not None and not current_is_hook:
                    out.append(f'    ((long(*)(long,long))(long)&{_name(hook+"__"+"__delattr__")})({self._value(obj)},(long){json.dumps(attr)});')
                elif owner_type not in {"str", "float", "complex", "none", "bytes", "bytearray", "bigint", "range", "list", "tuple", "dict", "set", "set:frozen"}:
                    # DEL_ATTR_DEFAULT_V1: `del obj.attr` sobre un objeto cuyo
                    # tipo es desconocido o es un objeto nativo (object:Clase)
                    # sin __delattr__ definido. CPython elimina de __dict__
                    # (object.__delattr__). En PITON, piton_object_del verifica
                    # en runtime que sea realmente un PK_OBJECT; si no, lanza
                    # TypeError como CPython.
                    out.append(f'    piton_object_del((PitonObject*){self._value(obj)},{json.dumps(attr)});')
                    self._emit_exc_check(out, function, None)
                else:
                    raise NativeBuildError(
                        f"native del on '{attr}' is not a property of a natively-typed object"
                    )
        elif op == "del_slice":
            # DEL_SLICE_V1: `borrar l[a:b]` — borrado de rango real. Los bounds
            # ausentes (None) son 0 / la longitud en runtime (datastructures:
            # del a[:]); sobre tupla es TypeError (imutabilidad).
            obj, lower, upper = args
            owner_type = types.get(obj, "")
            if owner_type == "tuple":
                out.append('    piton_raise_set("TypeError","\'tuple\' object does not support item deletion");')
                self._emit_exc_check(out, function, None)
                return out
            if owner_type not in {"list", ""}:
                raise NativeBuildError(f"native del slice target of type '{owner_type}' is not supported")
            _lo = self._value(lower) if lower is not None else "0"
            _hi = (
                self._value(upper)
                if upper is not None
                else f"((PitonSeq*){self._value(obj)})->length"
            )
            out.append(f"    piton_seq_del_range((PitonSeq*){self._value(obj)},{_lo},{_hi});")
        elif op == "del_item":
            # DEL_ITEM_V1: build-time check on the container type.
            obj, index = args
            owner_type = types.get(obj, "")
            if owner_type == "tuple":
                raise NativeBuildError("'tuple' object does not support item deletion")
            if owner_type in {"list"}:
                out.append(f"    piton_seq_pop((PitonSeq*){self._value(obj)},{self._value(index)});")
            elif owner_type == "dict":
                out.append(f"    piton_dict_del((PitonDict*){self._value(obj)},{self._slot(index, types)});")
            elif owner_type == "set":
                out.append(f"    piton_set_discard((PitonSet*){self._value(obj)},{self._slot(index, types)});")
            elif owner_type == "int":
                # DEL_ITEM_INT_V1: `borrar x[0]` con x estaticamente int.
                # CPython SIEMPRE lanza TypeError: 'int' object is not
                # subscriptable (un int no es subscriptable, nunca se borra).
                out.append('    piton_raise_set("TypeError","\'int\' object is not subscriptable");')
                self._emit_exc_check(out, function, None)
            else:
                raise NativeBuildError(f"native del item target of type '{owner_type}' is not supported")
            return out
        elif op == "del_name":
            # DEL_NAME_V1: the local slot is removed. Any later load of the
            # same name sees no type and fails closed with a PITON-shaped
            # error instead of a raw C symbol error. This is a build-time
            # model of CPython's NameError: branches that only conditionally
            # delete a variable are conservatively handled by the static type
            # map (scope documented in the PITON contract).
            # DEL_FN_SCOPE_V1: un `borrar` de modulo (opcode.py: `del def_op`
            # al final) NO mata los loads dentro de funciones — la funcion
            # pudo ejecutarse antes del del (los calls de opcode.py ocurren
            # antes); el NameError estatico solo aplica al flujo lineal de
            # <module> y a deletes anteriores EN LA MISMA funcion. El delete
            # runtime no es observable en funciones (edge documentado).
            deleted_here = self._deleted_by_function.setdefault(function.name, set())
            if isinstance(args[0], str):
                types.pop(args[0], None)
                self._deleted_names.add(args[0])
                deleted_here.add(args[0])
                self._deleted_at[(function.name, args[0])] = (self._cur_block, self._instr_seq)
            else:
                for name in args:
                    types.pop(name, None)
                    self._deleted_names.add(name)
                    deleted_here.add(name)
                    self._deleted_at[(function.name, name)] = (self._cur_block, self._instr_seq)
            return out
        elif op == "method_call":
            cls_name, method, obj = args[0], args[1], args[2]
            call_args = args[3] if len(args) > 3 else ()
            kw_args = args[4] if len(args) > 4 else ()
            if kw_args:
                # DICT_UPDATE_KW_V1: `d.update(b=2)` -> un piton_dict_append
                # por keyword (equivalente exacto de CPython d[k] = v).
                # El MIR solo baja kwargs para `update`; aqui se exige ademas
                # receiver dict. Cualquier otro caso es build error ANTES de
                # cualquier dispatch de metodo.
                owner_type = types.get(obj, "")
                # FMT_NAMED_V1: str.format con keywords — el receptor es un
                # str (literal o de tipo str) y los keywords se colocan en
                # los campos con nombre de la plantilla.
                if (
                    cls_name is None
                    and method == "format"
                    and owner_type in {"str", "class:str"}
                ):
                    self._emit_str_method(
                        out, result, method, obj, list(call_args), types,
                        tuple(kw_args),
                    )
                    return out
                # SORT_KEY_KW_V1: `lista.sort(key=f[, reverse=r])` — el metodo
                # sort con kw baja por aqui (mir ya no lo expande a posicional
                # salvo reverse solo). Se resuelven key (callback de closure)
                # y reverse (flag) de los kw y se ordena con
                # piton_seq_sort_key / _rev.
                if cls_name is None and method == "sort":
                    _sk_type = types.get(obj, "")
                    if _sk_type not in {"list", "tuple"}:
                        raise NativeBuildError(f"Linux sort() requires a list or tuple, not {_sk_type}")
                    if call_args:
                        raise NativeBuildError("Linux sort() takes no positional arguments")
                    _sk_key = None
                    _sk_rev = None
                    for _kn, _kv in kw_args:
                        if _kn == "key":
                            _sk_key = _kv
                        elif _kn in {"reverse", "reversa"}:
                            _sk_rev = _kv
                        else:
                            raise NativeBuildError(f"Linux sort() got an unexpected keyword argument '{_kn}'")
                    if _sk_key is None:
                        raise NativeBuildError("Linux sort() requires a key= keyword")
                    _sk_expr = self._value(_sk_key)
                    if _sk_rev is not None:
                        _rv = types.get(_sk_rev, "int")
                        if _rv not in {"int", "bool"}:
                            raise NativeBuildError("Linux sort(reverse=) requires a bool")
                        out.append(
                            f"    if({self._value(_sk_rev)})"
                            f"{{piton_seq_sort_key_rev((PitonSeq*){self._value(obj)},{_sk_expr});}}"
                            f"else{{piton_seq_sort_key((PitonSeq*){self._value(obj)},{_sk_expr});}}"
                        )
                    else:
                        out.append(f"    piton_seq_sort_key((PitonSeq*){self._value(obj)},{_sk_expr});")
                    types[result] = "none"
                    return out
                # KWRG_METHOD_CLASS_V1: metodo de clase con kwargs sobre una
                # instancia de tipo conocido (string.Template.substitute(...,
                # **kw)): se resuelve el simbolo Class__method y se empaquetan
                # los kwargs en un dict (**kwargs) + los posicionales extra en
                # una tupla (*args), igual que el ABI variadico de mir.
                # CONSTRUCTOR_KW_V1: el mismo camino se abre cuando el callee
                # trae cls_name estatico — es el __init__ de un constructor
                # (object_new + method_call con kwargs), cuyo receiver ya es la
                # instancia nueva y cuya clase conoce el MIR.
                if (cls_name is None or cls_name in self.classes) and not (
                    method == "update" and owner_type in {"dict", "class:dict", "dict:module"}
                ):
                    _kcn = None
                    if cls_name is not None:
                        _kcn = cls_name
                    elif owner_type.startswith(("object:", "class:")):
                        _kcn = owner_type.split(":", 1)[1]
                    _khit = None
                    if _kcn is not None:
                        for _cand in self.class_mro.get(_kcn, [_kcn]):
                            if method in self.classes.get(_cand, set()):
                                _khit = _cand
                                break
                    _ksym = f"{_khit}__{method}" if _khit else None
                    _kkw = self.function_kwargs.get(_ksym) if _ksym else None
                    if _ksym and _kkw is not None and _ksym in self.function_names:
                        _kopnd = self._value(obj)
                        _parts = [f"    {{PitonSeq*_kva=piton_seq_new(PK_TUPLE,{len(call_args)});"]
                        for _ki, _ka in enumerate(call_args):
                            _parts.append(
                                f"piton_seq_put(_kva,{_ki},{self._slot(_ka, types)});"
                            )
                        _parts.append(
                            # DICT_NEW_APPEND_V1: piton_dict_new(n) deja
                            # d->length=n (rellena n huecos, ver el literal de
                            # dict que si rellena por indice). Con append encima
                            # el dict salia con n huecos None:None de mas —
                            # len(**kw) daba el doble que en CPython.
                            f"PitonDict*_kvd=piton_dict_new(0);"
                        )
                        for _kname, _kval in kw_args:
                            if _kname is None:
                                raise NativeBuildError(
                                    f"Linux {method}() does not accept **kwargs unpacking"
                                )
                            _parts.append(
                                f"piton_dict_append(_kvd,piton_slot((long){json.dumps(_kname)},PK_STR),{self._slot(_kval, types)});"
                            )
                        _parts.append(
                            f"{_name(result)}={_name(_ksym)}({_kopnd},(long)_kva,(long)_kvd);}}"
                        )
                        out.append("".join(_parts))
                        types[result] = "int"
                        return out
                if cls_name is not None or method != "update" or owner_type not in {"dict", "class:dict", "dict:module"}:
                    raise NativeBuildError(
                        "native method calls with keyword arguments are supported only for "
                        "dict.update() and str.format()"
                    )
                _opnd = self._value(obj)
                if call_args:
                    if len(call_args) != 1 or types.get(call_args[0], "") != "dict":
                        raise NativeBuildError(
                            "Linux dict.update() with keyword arguments takes no positional dict argument"
                        )
                    out.append(f"    piton_dict_update((PitonDict*){_opnd},(PitonDict*){self._value(call_args[0])});")
                for _kname, _kval in kw_args:
                    if _kname is None:
                        raise NativeBuildError("Linux dict.update() does not accept **kwargs unpacking")
                    out.append(
                        f"    piton_dict_append((PitonDict*){_opnd},"
                        f"piton_slot((long){json.dumps(_kname)},PK_STR),{self._slot(_kval, types)});"
                    )
                types[result] = "none"
                return out
            if cls_name is None:
                # RECEIVER_TYPE_V1: si el receiver no esta tipado, intentar
                # inferirlo por uso en tiempo de uso antes del dispatch
                # (token: globals().items() -> dict; getopt: args.startswith
                # -> str). Cachea el tipo en types[obj].
                owner_type = self._infer_receiver_type(obj, method, types, function)
                # MODULE_METHOD_CALL_V1: si el receiver es un modulo (tipo
                # "module" / "object:module"), resolver la funcion compilada
                # "mod__method" (el camino que from_imports ya usa). nturl2path:
                # `urllib.parse.unquote(..)` (import LOCAL a la funcion, por
                # eso el chequeo de imports de modulo no lo alcanza) -> llama a
                # "urllib__parse__unquote". sitecustomize: apport_python_hook
                # (modulo externo no compilado) -> falla cerrado.
                _mod_target: str | None = None
                if owner_type in {"module", "object:module"}:
                    _obj_name = aliases.get(obj, obj)
                    if isinstance(_obj_name, str) and _obj_name:
                        # Try nested module pattern first (e.g., pkg2__subpkg__method)
                        _cand = f"{_obj_name.replace('.', '__')}__{method}"
                        if _cand in self.function_names:
                            _mod_target = _cand
                        else:
                            # Try original name with dots (e.g., pkg.sub__method)
                            _cand = f"{_obj_name}__{method}"
                            if _cand in self.function_names:
                                _mod_target = _cand
                            else:
                                # Fallback: try top-level module only
                                _top = _obj_name.split(".")[0]
                                _cand2 = f"{_top}__{method}"
                                if _cand2 in self.function_names:
                                    _mod_target = _cand2
                    if _mod_target is None:
                        raise NativeBuildError(
                            f"Linux module method {method}() is not statically known "
                            f"(module '{aliases.get(obj, obj)}' has no compiled function '{method}')"
                        )
                    # emitir la llamada a la funcion de modulo resuelta (como
                    # call directo: los params de modulo NO llevan self).
                    _all_values = self._complete_call_args(_mod_target, list(call_args))
                    if self.function_frame_abi.get(_mod_target, False):
                        _vals_c = ",".join(self._value(v) for v in _all_values)
                        out.append(f"    long _mc_args[]={{{_vals_c}}}; {_name(result)}=piton_frame_call((long)&{_name(_mod_target)},{len(_all_values)},_mc_args);")
                    else:
                        _vals_c = ",".join(self._value(v) for v in _all_values)
                        out.append(f"    {_name(result)}={_name(_mod_target)}({_vals_c});")
                    types[result] = "int"
                    return out
                if owner_type == "file":
                    # FILE_IO_V1: metodos del handle de fichero. Lo que CPython
                    # tiene y aqui no existe (seek/tell/flush, lectura por
                    # lineas con limite, truncate) falla cerrado, nunca diverge.
                    self._emit_file_method(out, result, method, obj, list(call_args), types)
                    return out
                if owner_type == "str":
                    # STR_METHODS_V1: builtin str methods bind statically here;
                    # anything not in the table fails closed.
                    self._emit_str_method(
                        out, result, method, obj, list(call_args), types,
                        tuple(kw_args) if kw_args else (),
                    )
                    return out
                if (
                    method == "maketrans"
                    and (
                        owner_type == "class:str"
                        # La forma real hoy: `load str` pasa por
                        # BUILTIN_MARKER_V1 (CLASS_LOAD_V1 solo cubre dict y
                        # slice), asi que el receiver llega tipado "builtin" y
                        # el nombre de la clase vive en aliases.
                        or (
                            owner_type == "builtin"
                            and (aliases.get(obj) if aliases else None) in {"str", "texto"}
                        )
                    )
                ):
                    # STR_MAKETRANS_V1 (forma de clase): en CPython
                    # str.maketrans es un staticmethod, asi que la llamada
                    # str.maketrans(dict) lleva la CLASE de receptor y no pasa
                    # por la tabla de metodos de instancia. Sin esta rama cae
                    # al piton_call_dynamic generico, que lee un temp sin
                    # codigo C emitido (SIGSEGV en runtime) y deja el
                    # resultado sin tipo: el siguiente uso revienta en BUILD.
                    # Mismo molde que dict.fromkeys sobre la clase.
                    if len(call_args) != 1:
                        raise NativeBuildError("Linux str.maketrans() requires exactly one argument")
                    if types.get(call_args[0], "") != "dict":
                        raise NativeBuildError("Linux str.maketrans() requires a dict argument")
                    out.append(
                        f"    {_name(result)}=(long)piton_str_maketrans((PitonDict*){self._value(call_args[0])});"
                    )
                    # Sin _dict_val_types: la tabla mezcla str y None segun la
                    # entrada, igual que en la forma de instancia — declarar un
                    # tipo fijo haria que las dos formas divergieran.
                    types[result] = "dict"
                    return out
                # BUILTIN_CLASS_METHODS_V1: classmethods de builtins que
                # llegan como load('int')/'float'/'bytes' -> builtin + alias.
                # Sin rama caen a piton_call_dynamic sobre temp sin codigo -> SIGSEGV.
                # Mismo patron que dict.fromkeys / str.maketrans clase.
                if method == "from_bytes" and (
                    owner_type == "class:int"
                    or (
                        owner_type == "builtin"
                        and (aliases.get(obj) if aliases else None) in {"int", "entero"}
                    )
                ):
                    # INT_FROM_BYTES_V1: int.from_bytes(bytes, byteorder='big', signed=False)
                    if len(call_args) < 1 or len(call_args) > 3:
                        raise NativeBuildError("Linux int.from_bytes() takes 1-3 arguments")
                    if types.get(call_args[0], "") not in {"bytes", "octetos", "bytearray", "arreglo_bytes"}:
                        raise NativeBuildError("Linux int.from_bytes() requires bytes or bytearray")
                    _ifb_order = "big"
                    _ifb_signed = 0
                    if len(call_args) >= 2:
                        if types.get(call_args[1], "") not in {"str", "texto"}:
                            raise NativeBuildError("Linux int.from_bytes() byteorder must be str")
                        _ifb_order = f"(const char*){self._value(call_args[1])}"
                    if len(call_args) == 3:
                        if types.get(call_args[2], "") not in {"bool", "int"}:
                            raise NativeBuildError("Linux int.from_bytes() signed must be bool")
                        _ifb_signed = self._value(call_args[2])
                    out.append(
                        f"    {_name(result)}=(long)piton_int_from_bytes("
                        f"(PitonSeq*){self._value(call_args[0])},{_ifb_order},{_ifb_signed});"
                    )
                    types[result] = "int"
                    return out
                if method == "maketrans" and (
                    owner_type == "class:bytes"
                    or (
                        owner_type == "builtin"
                        and (aliases.get(obj) if aliases else None) in {"bytes", "octetos"}
                    )
                ):
                    # BYTES_MAKETRANS_V1: bytes.maketrans(frm, to) -> 256-byte table
                    if len(call_args) != 2:
                        raise NativeBuildError("Linux bytes.maketrans() takes exactly 2 arguments")
                    if types.get(call_args[0], "") not in {"bytes", "octetos"} or types.get(call_args[1], "") not in {"bytes", "octetos"}:
                        raise NativeBuildError("Linux bytes.maketrans() requires two bytes arguments")
                    out.append(
                        f"    {_name(result)}=(long)piton_bytes_maketrans("
                        f"(PitonSeq*){self._value(call_args[0])},(PitonSeq*){self._value(call_args[1])});"
                    )
                    types[result] = "bytes"
                    return out
                if method == "fromhex" and (
                    owner_type == "class:float"
                    or (
                        owner_type == "builtin"
                        and (aliases.get(obj) if aliases else None) in {"float"}
                    )
                ):
                    # FLOAT_FROMHEX_V1: float.fromhex(str)
                    if len(call_args) != 1:
                        raise NativeBuildError("Linux float.fromhex() takes exactly one argument")
                    if types.get(call_args[0], "") not in {"str", "texto"}:
                        raise NativeBuildError("Linux float.fromhex() requires a string argument")
                    out.append(
                        f"    {_name(result)}=(long)piton_float_fromhex((const char*){self._value(call_args[0])});"
                    )
                    types[result] = "float"
                    return out
                if owner_type in {"bytes", "bytearray"}:
                    self._emit_bytes_method(out, result, method, obj, list(call_args),
                                            owner_type == "bytearray", types)
                    return out
                if owner_type == "class:dict" and method == "fromkeys":
                    # DICT_FROMKEYS_V1: dict.fromkeys(iterable, value) — las
                    # claves son los elementos; el valor es el mismo para todas.
                    if len(call_args) not in (1, 2):
                        raise NativeBuildError("Linux dict.fromkeys() requires one or two arguments")
                    _fk_kind0 = types.get(call_args[0], "")
                    if _fk_kind0 not in {"list", "tuple", "str"}:
                        raise NativeBuildError("Linux dict.fromkeys() requires a list, tuple or str of keys")
                    _has_val = 1 if len(call_args) == 2 else 0
                    _vkind = self._kind(types.get(call_args[1], "none")) if _has_val else "PK_NONE"
                    _vexpr = self._value(call_args[1]) if _has_val else "0"
                    # DICT_FROMKEYS_STR_V1: el str no es un PitonSeq y tiene
                    # helper propio (shlex: dict.fromkeys(punctuation_chars)).
                    _fk_cast = "(const char*)" if _fk_kind0 == "str" else "(PitonSeq*)"
                    _fk_fn = "piton_dict_fromkeys_str" if _fk_kind0 == "str" else "piton_dict_fromkeys"
                    out.append(
                        f"    {_name(result)}=(long){_fk_fn}({_fk_cast}{self._value(call_args[0])},"
                        f"{_has_val},{_vkind},{_vexpr});"
                    )
                    types[result] = "dict"
                    self._dict_val_types[result] = types.get(call_args[1], "none") if _has_val else "none"
                    return out
                if owner_type in {"slice", "class:slice"} and method == "indices":
                    # SLICE_INDICES_V1: s.indices(n) -> (start, stop, step)
                    # normalizados a la longitud n (CPython).
                    if len(call_args) != 1 or types.get(call_args[0], "") not in {"int", "bool"}:
                        raise NativeBuildError("Linux slice.indices() requires one int argument")
                    _slv = values[0] if owner_type == "class:slice" else obj
                    out.append(
                        f"    {_name(result)}=piton_slice_indices((PitonSlice*){self._value(_slv)},{self._value(call_args[0])});"
                    )
                    types[result] = "tuple"
                    self._tuple_elems[result] = (("%i", "int"), ("%j", "int"), ("%k", "int"))
                    return out
                if owner_type in {"class:bytes", "class:bytearray"} and method == "fromhex":
                    if len(call_args) != 1 or types.get(call_args[0]) != "str":
                        raise NativeBuildError("Linux fromhex() requires one str argument")
                    is_ba = owner_type == "class:bytearray"
                    out.append(f"    {_name(result)}=(long)piton_bytes_fromhex((const char*){self._value(call_args[0])},{1 if is_ba else 0});")
                    out.append(f"    if(piton_exc_flag){{piton_report_unhandled();piton_exit(1);}}")
                    types[result] = "bytearray" if is_ba else "bytes"
                    return out
                coll_type = types.get(obj, "")
                if coll_type == "memoryview" and method in {
                    "tobytes", "tolist", "hex", "cast", "release", "readonly", "itemsize", "nbytes"
                }:
                    # MEMORYVIEW_METHODS_V1: tobytes/tolist/readonly/itemsize/
                    # nbytes soportados; hex/cast/release quedan fuera.
                    _mv = f"(PitonMemoryView*){self._value(obj)}"
                    if method == "tobytes":
                        if call_args:
                            raise NativeBuildError(f"Linux memoryview.{method}() takes no arguments")
                        out.append(f"    {_name(result)}=piton_memoryview_tobytes({_mv});")
                        types[result] = "bytes"
                        return out
                    if method == "tolist":
                        if call_args:
                            raise NativeBuildError(f"Linux memoryview.{method}() takes no arguments")
                        out.append(f"    {_name(result)}=piton_memoryview_tolist({_mv});")
                        types[result] = "list"
                        self._coll_elems[result] = "int"
                        return out
                    if method == "readonly":
                        if call_args:
                            raise NativeBuildError(f"Linux memoryview.{method}() takes no arguments")
                        out.append(f"    {_name(result)}=({_mv})->is_bytearray?0:1;")
                        types[result] = "bool"
                        return out
                    if method == "itemsize":
                        if call_args:
                            raise NativeBuildError(f"Linux memoryview.{method}() takes no arguments")
                        # V1: vistas de bytes ('B') -> 1 byte por elemento.
                        out.append(f"    {_name(result)}=1;")
                        types[result] = "int"
                        return out
                    if method == "nbytes":
                        if call_args:
                            raise NativeBuildError(f"Linux memoryview.{method}() takes no arguments")
                        out.append(f"    {_name(result)}=({_mv})->length;")
                        types[result] = "int"
                        return out
                    if method == "cast":
                        # MEMORYVIEW_CAST_V1: cast('B') devuelve una vista con
                        # el mismo buffer (alias, como CPython). Requiere vista
                        # contigua: un receiver marcado como no contiguo es un
                        # build error (CPython lanza TypeError en runtime).
                        if len(call_args) != 1:
                            raise NativeBuildError("Linux memoryview.cast() takes exactly one argument")
                        _fmt = call_args[0]
                        _fmt_val = _fmt if isinstance(_fmt, str) and not _fmt.startswith(("%", "@")) else self._fn_consts.get(_fmt)
                        if _fmt_val not in {"B", "b", "c"}:
                            raise NativeBuildError("Linux memoryview.cast() supports only the 1-byte formats ('B','b','c')")
                        if obj in self._mv_noncontig:
                            raise NativeBuildError("memoryview.cast() requires a C-contiguous view (non-contiguous receiver)")
                        out.append(
                            f"    {_name(result)}=piton_memoryview_new(({_mv})->buf,({_mv})->is_bytearray,({_mv})->offset,({_mv})->length,1);"
                        )
                        types[result] = "memoryview"
                        return out
                    raise NativeBuildError(f"Linux memoryview.{method}() is not supported")
                if coll_type == "complex" and method == "conjugate":
                    # COMPLEX_CONJUGATE_V1: z.conjugate() -> z con la parte
                    # imaginaria negada.
                    if call_args:
                        raise NativeBuildError("Linux complex.conjugate() takes no arguments")
                    out.append(f"    {{PitonComplexVal _z=piton_cx_of({self._value(obj)}); _z.im=-_z.im; {_name(result)}=piton_cx_wrap(_z);}}")
                    types[result] = "complex"
                    return out
                if coll_type in {"list", "tuple", "dict", "set", "set:frozen"}:
                    # COLL_METHODS_V1: builtin collection methods bind
                    # statically here, mirroring the str table.
                    # SET_FROZEN_METHODS_V1: "set:frozen" usa la misma tabla de
                    # set (los mutadores fallan cerrado dentro de la tabla).
                    self._emit_collection_method(out, result, method, obj, list(call_args), coll_type, types, aliases)
                    return out
                unbound_class = None
                if not owner_type.startswith("object:"):
                    _obj_name = aliases.get(obj, obj) if aliases else obj
                    if (
                        isinstance(_obj_name, str)
                        and method == "__init__"
                        and _obj_name in _BUILTIN_BASE_INIT
                    ):
                        # BUILTIN_BASE_INIT_V1: `Exception.__init__(self, msg)`
                        # (xml/sax/_exceptions.py) llama al BUILTIN, no a la
                        # clase del usuario. El fallback por candidatos emitia
                        # una llamada a SAXException__init__ DESDE SU PROPIO
                        # cuerpo: recursion y aridad erronea -> gcc.
                        # object.__init__ es no-op; BaseException guarda args.
                        out.append("    {")
                        if (
                            _obj_name in {"Exception", "BaseException"}
                            and call_args
                        ):
                            _argc_extra = len(call_args) - 1
                            out.append(
                                f"        PitonSeq*_exc_args=piton_seq_new(PK_TUPLE,{_argc_extra});"
                            )
                            for _i, _v in enumerate(call_args[1:]):
                                out.append(
                                    f"        _exc_args->items[{_i}]={self._slot(_v, types)};"
                                )
                            out.append(
                                f"        piton_object_set((PitonObject*){self._value(call_args[0])},"
                                f"\"args\",piton_slot((long)_exc_args,PK_TUPLE));"
                            )
                        out.append("    }")
                        if result:
                            types[result] = "none"
                        return out
                    if isinstance(_obj_name, str) and _obj_name in self.classes:
                        # UNBOUND_CLASS_METHOD_V1: `P.f(P())` llama a la
                        # funcion de clase con la instancia como primer arg.
                        unbound_class = _obj_name
                    else:
                        candidates = [c for c in self.classes if method in self.classes[c]]
                        if candidates:
                            # UNBOUND_BASE_CALL_V1: si el receiver es una
                            # CLASE (no una instancia), la llamada es
                            # desbound: el self ya viene en los argumentos
                            # (`Exception.__init__(self, msg, opt)`), asi que
                            # NO se antepone el receiver — sin esto la
                            # llamada llevaba self dos veces y gcc rechazaba
                            # elabi con "too many arguments".
                            _is_class_receiver = owner_type.startswith("class:")
                            _recv_args = [] if _is_class_receiver else [obj]
                            out.append(f"    {{PitonObject* _d_obj=(PitonObject*){self._value(obj)};")
                            for idx, c in enumerate(candidates):
                                target = f"{c}__{method}"
                                all_vals = self._complete_call_args(target, [*_recv_args, *call_args])
                                vals_c = ",".join(self._value(v) for v in all_vals)
                                branch = "if" if idx == 0 else "else if"
                                out.append(f'    {branch}(_d_obj&&_d_obj->class_name&&piton_strcmp(_d_obj->class_name,"{c}")==0){{')
                                if self.function_frame_abi.get(target, False):
                                    out.append(f'        long _d_args[]={{{vals_c}}}; {_name(result)}=piton_frame_call((long)&{_name(target)},{len(all_vals)},_d_args);')
                                else:
                                    out.append(f"        {_name(result)}={_name(target)}({vals_c});")
                                out.append("    }")
                            out.append("    }")
                            types[result] = "int"
                            return out
                        else:
                            # DYNAMIC_METHOD_CALL_V1: el receiver no tiene
                            # clase conocida -> el callee es el VALOR del
                            # atributo, resuelto en runtime, y el frame lo
                            # arma el runtime (DYNAMIC_CALL_V1). Funciona
                            # cuando el atributo es un callable de runtime
                            # (un closure, un metodo ligado, la funcion de
                            # un modulo); una clase de PITON no expone sus
                            # metodos como atributo en runtime (limite
                            # documentado: la llamada falla en runtime).
                            _attr_expr = (
                                f'(const char*){self._value(method)}'
                                if isinstance(method, str) and method.startswith('%') else f'"{method}"'
                            )
                            _dargs = []
                            if not isinstance(obj, str) or not obj.startswith("%"):
                                # un receiver que NO es una clase: la llamada
                                # es ligada (el receiver va como primer arg).
                                _dargs.append(obj)
                            _dargs.extend(call_args)
                            _dparts = [f"{{PitonSeq*_dpa=piton_seq_new(PK_TUPLE,{len(_dargs)});"]
                            for _di, _dv in enumerate(_dargs):
                                _dparts.append(
                                    f"    _dpa->items[{_di}]=piton_slot({self._value(_dv)},PK_INT);"
                                )
                            _dparts.append("_dpa;}")
                            _dtuple = f"(long)({''.join(_dparts)})"
                            if kw_args:
                                _kparts = [f"{{PitonDict*_dpk=piton_dict_new(0);"]
                                for _kn, _kv in kw_args:
                                    _kparts.append(
                                        f'    piton_dict_append(_dpk,piton_slot((long){json.dumps(_kn)},PK_STR),{self._slot(_kv, types)});'
                                    )
                                _kparts.append("    }")
                                _kdict = f"(long)({''.join(_kparts)})"
                            else:
                                _kdict = "0"
                            out.append(
                                f"    {_name(result)}=piton_call_dynamic("
                                f"piton_object_get((PitonObject*){self._value(obj)},{_attr_expr}).bits,"
                                f"{_dtuple},0,{_kdict});"
                            )
                            types[result] = "int"
                            return out
                if unbound_class is not None:
                    cls_name = unbound_class
                    target = f"{cls_name}__{method}"
                    if target in self.static_methods:
                        all_values = self._complete_call_args(target, list(call_args))
                    elif target in self.class_methods:
                        all_values = self._complete_call_args(target, [unbound_class, *call_args])
                    else:
                        all_values = self._complete_call_args(target, list(call_args))
                    if self.function_frame_abi.get(target, False):
                        args_c = ",".join(self._value(v) for v in all_values)
                        out.append(f'    {{long _method_args[]={{ {args_c} }}; {_name(result)}=piton_frame_call((long)&{_name(target)},{len(all_values)},_method_args);}}')
                    else:
                        args_c = ",".join(self._value(v) for v in all_values)
                        out.append(f"    {_name(result)}={_name(target)}({args_c});")
                    types[result] = self.function_return_types.get(target, "int")
                    return out
                cls_name = owner_type.split(":", 1)[1]
                if self._resolve_property_class(owner_type, method) is not None:
                    raise NativeBuildError(
                        f"native property '{method}' of '{cls_name}' object is not a method (calling a property is unsupported)"
                    )
            try:
                cls_name = self._resolve_method(cls_name, method)
            except NativeBuildError:
                # METHOD_NOT_FOUND_V1: el metodo no esta definido estaticamente
                # en ninguna clase conocida (p.ej. heredado de una clase de
                # CPython real no empaquetada). Fallback a llamada dinamica en
                # runtime: resuelve el atributo con getattr y lo llama.
                # Si el atributo no existe o no es callable, el runtime
                # lanza AttributeError/TypeError como CPython.
                _attr_expr = (
                    f'(const char*){self._value(method)}'
                    if isinstance(method, str) and method.startswith('%')
                    else f'"{method}"'
                )
                _dargs = []
                if not isinstance(obj, str) or not obj.startswith("%"):
                    _dargs.append(obj)
                _dargs.extend(call_args)
                _dparts = [f"{{PitonSeq*_dpa=piton_seq_new(PK_TUPLE,{len(_dargs)});"]
                for _di, _dv in enumerate(_dargs):
                    _dparts.append(
                        f"    _dpa->items[{_di}]=piton_slot({self._value(_dv)},PK_INT);"
                    )
                _dparts.append("_dpa;}")
                _dtuple = f"(long)({''.join(_dparts)})"
                if kw_args:
                    _kparts = [f"{{PitonDict*_dpk=piton_dict_new(0);"]
                    for _kn, _kv in kw_args:
                        _kparts.append(
                            f'    piton_dict_append(_dpk,piton_slot((long){json.dumps(_kn)},PK_STR),{self._slot(_kv, types)});'
                        )
                    _kparts.append("    }")
                    _kdict = f"(long)({''.join(_kparts)})"
                else:
                    _kdict = "0"
                out.append(
                    f"    {_name(result)}=piton_call_dynamic("
                    f"piton_object_get((PitonObject*){self._value(obj)},{_attr_expr}).bits,"
                    f"{_dtuple},0,{_kdict});"
                )
                types[result] = "int"
                return out
            target = f"{cls_name}__{method}"
            if target in self.static_methods:
                all_values = self._complete_call_args(target, list(call_args))
            elif target in self.class_methods:
                # CLASSMETHOD_CLS_V1: el `cls` es el OPERANDO (objeto de
                # clase real), no el string del nombre — `P__f(P)` no
                # compila porque P no es un identificador C.
                all_values = self._complete_call_args(target, [obj, *call_args])
            else:
                all_values = self._complete_call_args(target, [obj, *call_args])
                # CALL_ARG_TYPE_V1: el call-site de un metodo informa el tipo
                # de sus params (el constructor de una clase: `P([1,2])` deja
                # el tipo de la lista en el param del __init__).
                _mparams = self.function_params.get(target) or []
                for _mi, _mv in enumerate(all_values[1:], start=1):
                    if _mi >= len(_mparams):
                        break
                    _mvt = types.get(_mv)
                    if _mvt and _mvt != "int":
                        self._call_param_types.setdefault(target, {})[_mparams[_mi]] = _mvt
            values = ",".join(self._value(v) for v in all_values)
            if self.function_frame_abi.get(target, False):
                args_c = ",".join(self._value(v) for v in all_values)
                out.append(f'    {{long _method_args[]={{ {args_c} }}; {_name(result)}=piton_frame_call((long)&{_name(target)},{len(all_values)},_method_args);}}')
            else:
                out.append(f"    {_name(result)}={_name(target)}({values});")
            # RETURNTYPE_V1: method results propagate the inferred return type
            # (covers `devolver self` -> __str__ dispatch on print, etc).
            types[result] = self.function_return_types.get(target, "int")
        elif op == "raise_chain":
            exc_type, payload, cause_type, cause_payload, handler_label = args
            message = f"(const char*){self._value(payload)}" if payload is not None else '""'
            cause_msg = f"(const char*){self._value(cause_payload)}" if cause_payload is not None else '""'
            out.append(f'    piton_raise_chain_set("{exc_type}",{message},"{cause_type}",{cause_msg});')
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                _raise_or_propagate(out, function)
        elif op == "builtin_class":
            # OS_PATHLIKE_V1: una clase builtin (os.PathLike) como objeto.
            out.append(f'    {_name(result)}=(long)piton_object_new("{args[0]}",0);')
            types[result] = f"class:{args[0]}"
            return out
        elif op == "os_fspath":
            # OS_FSPATH_V1: os.fspath(x) — un str pasa; un bytes tambien
            # (CPython acepta str|bytes|PathLike); otro valor es un
            # TypeError en runtime con el MISMO texto que CPython:
            #   expected str, bytes or os.PathLike object, not int
            _fp = self._value(args[0])
            _fpt = types.get(args[0], "")
            _fph = args[1] if len(args) > 1 else None
            if _fpt == "str":
                out.append(f"    {_name(result)}=(long)(const char*){_fp};")
            elif _fpt == "bytes":
                out.append(f"    {_name(result)}={_fp};")
            else:
                # Chequeo en runtime: el tipo estatico puede ser "int" por
                # defecto (hueco de inferencia) mientras el valor real es un
                # str, y al reves. La politica de puntero es la misma que
                # piton_isinstance_dynamic (imprimible -> str).
                out.append(
                    f"    {{long _fpv={_fp};"
                    f"if(_fpv!=PITON_NONE&&piton_maybe_ptr(_fpv)&&piton_is_printable_str((const char*)_fpv))"
                    f"{{{_name(result)}=_fpv;}}"
                    f"else{{piton_raise_fspath_type(_fpv);}}"
                    f"}}"
                )
                self._emit_exc_check(out, function, _fph)
            types[result] = "str"
            return out
        elif op == "os_fsencode":
            # OS_FSENCODE_V1: os.fsencode(x) — str -> bytes, bytes pasa.
            _fp = self._value(args[0])
            _fpt = types.get(args[0], "")
            _fph = args[1] if len(args) > 1 else None
            if _fpt == "str":
                out.append(f"    {_name(result)}=(long)piton_str_to_bytes((const char*){_fp});")
            elif _fpt == "bytes":
                out.append(f"    {_name(result)}={_fp};")
            else:
                out.append(
                    f"    {{long _fpv={_fp};"
                    f"if(_fpv!=PITON_NONE&&piton_maybe_ptr(_fpv)&&piton_is_printable_str((const char*)_fpv))"
                    f"{{{_name(result)}=(long)piton_str_to_bytes((const char*)_fpv);}}"
                    f"else{{piton_raise_set(\"TypeError\",\"expected str, bytes or os.PathLike object, not int\");{_name(result)}=0;}}"
                    f"}}"
                )
                self._emit_exc_check(out, function, _fph)
            types[result] = "bytes"
            return out
        elif op == "print_set_fd":
            # PRINT_FD_V1: print(..., file=X) — el descriptor de salida activo.
            out.append(f"    piton_print_fd={self._value(args[0])};")
            return out
        elif op == "print_restore_fd":
            out.append("    piton_print_fd=1;")
            return out
        elif op == "raise_value":
            # RAISE_VALUE_V1: `raise <instancia>` — el runtime deriva el tipo
            # del objeto y lleva el valor.
            value = args[0]
            out.append(f"    piton_raise_set_value({self._value(value)});")
            handler_label = args[1] if len(args) > 1 else None
            if handler_label:
                # Emit exception check to route to the correct handler based on exception type
                self._emit_exc_check(out, function, handler_label)
            else:
                _raise_or_propagate(out, function)
            return out
        elif op == "raise_typed":
            exc_type, payload, handler_label = args[0], args[1], args[2]
            args_tuple = args[3] if len(args) > 3 else None
            message = f"(const char*){self._value(payload)}" if payload is not None else '""'
            if args_tuple is not None:
                # EXC_ARGS_MULTI_V1: raise con 2+ args -> .args completo.
                out.append(f'    piton_raise_set_args("{exc_type}",{message},{self._value(args_tuple)});')
            else:
                out.append(f'    piton_raise_set("{exc_type}",{message});')
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                if exc_type == "StopIteration":
                    out.append(f"    goto {_name(function.name + '___exit')};")
                else:
                    _raise_or_propagate(out, function)
        elif op == "try_push":
            pass  # no-op in static flag-based model
        elif op == "try_pop":
            pass  # no-op in static flag-based model
        elif op == "catch_flag":
            out.append(f'    {_name(result)}=piton_exc_flag;')
            types[result] = "bool"
        elif op == "catch_clear":
            out.append('    piton_catch_clear();')
        elif op == "load_exc_args":
            # EXC_ARGS_MULTI_V1: `e.args` — si el raise guardo la tupla
            # multi-arg, devolverla; si no, (mensaje,) como siempre.
            msg_operand = args[0]
            out.append(
                f"    {_name(result)}=piton_exc_args?piton_exc_args:(long)piton_tuple1((const char*){self._value(msg_operand)});"
            )
            types[result] = "tuple"
            return out
        elif op == "catch_bind":
            out.append(f"    {_name(result)}=(long)(piton_exc_message?piton_exc_message:\"\");")
            types[result] = "str"
        elif op == "catch_type":
            out.append(f"    {_name(result)}=(long)(piton_exc_type?piton_exc_type:\"\");")
            types[result] = "str"
        elif op == "catch_message":
            out.append(f"    {_name(result)}=(long)(piton_exc_message?piton_exc_message:\"\");")
            types[result] = "str"
        elif op == "reraise_save":
            out.append('    piton_reraise_save();')
        elif op == "raise_active":
            exc_type, handler_label = args
            out.append(f'    piton_reraise_set("{exc_type}");')
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                _raise_or_propagate(out, function)
        elif op == "raise_active_dynamic":
            # Dynamic re-raise: read the reraise slots (the handler may have
            # already cleared the live exception state via catch_clear). With
            # a multi-except enclosing try, dispatch by the live reraise type.
            (handler_label,) = args
            out.append("    piton_reraise_set(piton_reraise_type);")
            if isinstance(handler_label, list):
                emitted = False
                for accepted, label in handler_label:
                    if accepted is None or accepted in {"Exception", "BaseException"}:
                        out.append(f"    goto {_name(function.name + '_' + label)};")
                        emitted = True
                        break
                    prefix = (
                        "if(!piton_strcmp(piton_exc_type, "
                        if not emitted else
                        "else if(!piton_strcmp(piton_exc_type, "
                    )
                    head = "if(" if not emitted else "else if("
                    out.append(
                        f"    {head}!piton_strcmp(piton_exc_type, {json.dumps(accepted)})) "
                        f"goto {_name(function.name + '_' + label)};"
                    )
                    emitted = True
                if not emitted:
                    _raise_or_propagate(out, function)
            elif handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                _raise_or_propagate(out, function)
        elif op == "sys_exit":
            # STDLIB_TIER1_V1: sys.exit([code]) terminates the process.
            # None -> 0, int/bool -> that code. A string argument is CPython's
            # "print to stderr and exit 1"; that is not implemented natively, so
            # it fails closed instead of silently exiting with a pointer.
            (code,) = args
            if code is None:
                value = "0"
            elif types.get(code) == "str":
                raise NativeBuildError("native sys.exit(str) is not supported yet")
            else:
                value = self._value(code)
            if result:
                out.append(f"    {_name(result)}=0;")
            out.append(f"    piton_exit((long)({value}));")
        elif op == "sys_argv":
            # SYS_ARGV_V1: sys.argv as a list of process argument strings.
            out.append(f"    {_name(result)}=piton_argv_new();")
            types[result] = "list"
        elif op == "math_sqrt":
            operand = self._value(args[0])
            if types.get(args[0]) != "float":
                operand = f"piton_double_bits((double){operand})"
            out.append(f'    {_name(result)}=piton_float_sqrt({operand});')
            types[result] = "float"
        elif op in {"math_sin", "math_cos", "math_log"}:
            operand = self._value(args[0])
            if types.get(args[0]) != "float":
                operand = f"piton_double_bits((double){operand})"
            helper = f"piton_float_{op[5:]}"
            out.append(f'    {_name(result)}={helper}({operand});')
            types[result] = "float"
        elif op == "math_log2":
            # MATH_LOG2_V1: math.log(x, base) = log(x)/log(base) (CPython).
            left = self._value(args[0])
            right = self._value(args[1])
            if types.get(args[0]) != "float":
                left = f"piton_double_bits((double){left})"
            if types.get(args[1]) != "float":
                right = f"piton_double_bits((double){right})"
            out.append(f'    {_name(result)}=piton_double_bits(piton_log(piton_bits_double({left}))/piton_log(piton_bits_double({right})));')
            types[result] = "float"
        elif op in {"math_floor", "math_ceil", "math_trunc", "math_fabs"}:
            operand = self._value(args[0])
            if types.get(args[0]) != "float":
                operand = f"piton_double_bits((double){operand})"
            helper = f"piton_{op}_bits"
            out.append(f"    {_name(result)}={helper}({operand});")
            types[result] = "float" if op == "math_fabs" else "int"
        elif op == "math_gcd":
            if types.get(args[0]) not in {"int", "bool"} or types.get(args[1]) not in {"int", "bool"}:
                raise NativeBuildError("Linux math.gcd requires int arguments")
            out.append(f"    {_name(result)}=piton_math_gcd({self._value(args[0])},{self._value(args[1])});")
            types[result] = "int"
        elif op == "terminal_init":
            # TERMINAL_V1: initialize terminal (raw mode, no echo)
            out.append(f"    {_name(result)}=piton_terminal_init();")
            types[result] = "int"
        elif op == "terminal_cleanup":
            # TERMINAL_V1: restore terminal to normal mode
            out.append(f"    {_name(result)}=piton_terminal_cleanup();")
            types[result] = "int"
        elif op == "terminal_write":
            # TERMINAL_V1: write string to terminal
            out.append(f"    {_name(result)}=piton_terminal_write({self._value(args[0])});")
            types[result] = "int"
        elif op == "terminal_read":
            # TERMINAL_V1: read from terminal (non-blocking)
            out.append(f"    {_name(result)}=piton_terminal_read({self._value(args[0])});")
            types[result] = "str"
        elif op == "terminal_size":
            # TERMINAL_V1: get terminal size as tuple (rows, cols)
            out.append(f"    {_name(result)}=piton_terminal_size();")
            types[result] = "tuple"
        elif op == "build_collection":
            kind, items = args[0], args[1]
            if kind == "tuple" and result:
                # PCT_FORMAT_V1: record tuple ELEMENT TYPES so %-formatting
                # can convert each element by static type. Types (unlike
                # temps, which are SSA per-function) stay valid when the
                # tuple crosses into another function via a global.
                elems = []
                for item in items:
                    if isinstance(item, str) and item.startswith("%"):
                        elems.append((item, types.get(item, "int")))
                    else:
                        elems.append((item, self._percent_literal_type(item)))
                self._tuple_elems[result] = tuple(elems)
            if result and items:
                # COLL_ELEM_TYPE_V1: a single element kind is recorded; mixed or
                # empty collections stay untyped so sum() refuses them.
                elem_kinds = set()
                for item in items:
                    if isinstance(item, str) and item.startswith("%"):
                        elem_kinds.add(types.get(item, "int"))
                    elif isinstance(item, str):
                        # a bare string operand is a literal in this position
                        elem_kinds.add("str")
                    else:
                        elem_kinds.add("int")
                if len(elem_kinds) == 1:
                    self._coll_elems[result] = next(iter(elem_kinds))
                elif elem_kinds:
                    # COLL_ELEM_TYPE_V1: a MIXED collection is recorded as the
                    # joined set, so `sum([10 ** 20, 1])` is recognized as
                    # containing a bigint instead of silently defaulting to int
                    # and adding pointers.
                    self._coll_elems[result] = "|".join(sorted(elem_kinds))
                # NESTED_TUPLE_LIST_V1: una coleccion cuyos items son TODOS
                # temporales de tipo tuple anota la estructura de la PRIMERA
                # tupla elemental. En stat: `_filemode_table` es una TUPLA de
                # tablas de aridades heterogeneas, pero `table` (cada fila) es
                # una TUPLA de tuplas (bit, char) HOMOGENEA. El `for table en
                # _filemode_table` rinde cada table como tupla; `para bit, char
                # en table` desempaqueta su item. La anotacion usa el shape de
                # la PRIMERA tupla (CPython desempaqueta contra la primera
                # coincidencia); aridades distintas entre items NO se anotan
                # (fail-closed, unpack_check rechaza si la aridad no calza).
                if kind in {"list", "tuple"} and items and all(
                    isinstance(i, str) and i.startswith("%") and types.get(i) == "tuple"
                    for i in items
                ):
                    _shapes = [self._tuple_elems.get(i) for i in items]
                    _first = _shapes[0]
                    if _first is not None and all(
                        s is not None and len(s) == len(_first) for s in _shapes
                    ):
                        self._nested_tuple_lists[result] = tuple(t for _, t in _first)
            if kind in {"list", "tuple"}:
                out.append(f'    {_name(result)}=(long)piton_seq_new({self._kind(kind)},{len(items)});')
                for index, value in enumerate(items):
                    out.append(f'    piton_seq_put((PitonSeq*){_name(result)},{index},{self._slot(value, types)});')
            elif kind == "dict":
                if result:
                    # P14 mapping: resolve %(name)s statically for inline
                    # literals with literal str keys (anything else is not
                    # recorded, so mapping use fails closed).
                    _drec = {}
                    for _k, _v in items:
                        _ks = _k if (isinstance(_k, str) and not _k.startswith("%")) else self._fn_consts.get(_k)
                        if not isinstance(_ks, str):
                            _drec = None
                            break
                        if isinstance(_v, str) and _v.startswith("%"):
                            _drec[_ks] = (_v, types.get(_v, "int"))
                        else:
                            try:
                                _drec[_ks] = (_v, self._percent_literal_type(_v))
                            except NativeBuildError:
                                _drec = None
                                break
                    if _drec is not None:
                        self._dict_elems[result] = _drec
                if result and items:
                    # DICT_KEY_TYPE_V1: every key must share one static kind for
                    # the iterator to be typed; a mixed-key dict stays untyped
                    # and its iteration fails closed instead of printing a
                    # pointer as if it were a string.
                    kinds = set()
                    for key, _value in items:
                        if isinstance(key, str) and not key.startswith("%"):
                            kinds.add("str")
                        else:
                            kinds.add(types.get(key, "int"))
                    if len(kinds) == 1:
                        self._dict_key_types[result] = next(iter(kinds))
                    # DICT_VAL_TYPE_V1: record the value kind so `d[k]` and
                    # `d.get(k)` return a correctly typed result.
                    if result and items:
                        v_kinds = set()
                        for _key, value in items:
                            if isinstance(value, str) and value.startswith("%"):
                                v_kinds.add(types.get(value, "int"))
                            elif isinstance(value, str):
                                v_kinds.add("str")
                            else:
                                v_kinds.add("int")
                        if len(v_kinds) == 1:
                            self._dict_val_types[result] = next(iter(v_kinds))
                    if result and items:
                        v_kinds = set()
                        for _key, value in items:
                            if isinstance(value, str) and value.startswith("%"):
                                v_kinds.add(types.get(value, "int"))
                            elif isinstance(value, str):
                                v_kinds.add("str")
                            else:
                                v_kinds.add("int")
                        if len(v_kinds) == 1:
                            self._dict_val_types[result] = next(iter(v_kinds))
                if result and not items:
                    # DICT_VAL_TYPE_V1: an empty literal records no value
                    # kind; the flag lets dict.get fallback to the default.
                    self._dict_empty[result] = True
                out.append(f'    {_name(result)}=(long)piton_dict_new({len(items)});')
                for index, (key, value) in enumerate(items):
                    key_is_str = isinstance(key, str) and not key.startswith("%")
                    key_kind = "PK_STR" if key_is_str else self._kind(types.get(key, "int"))
                    out.append(f'    piton_dict_put((PitonDict*){_name(result)},{index},piton_slot({self._value(key)},{key_kind}),{self._slot(value, types)});')
            elif kind == "dict:module":
                # dict:module is a dict whose values are module objects
                if result and items:
                    # All values are module objects
                    self._dict_val_types[result] = "object:module"
                    # Keys are strings
                    self._dict_key_types[result] = "str"
                out.append(f'    {_name(result)}=(long)piton_dict_new({len(items)});')
                for index, (key, value) in enumerate(items):
                    key_is_str = isinstance(key, str) and not key.startswith("%")
                    key_kind = "PK_STR" if key_is_str else self._kind(types.get(key, "int"))
                    out.append(f'    piton_dict_put((PitonDict*){_name(result)},{index},piton_slot({self._value(key)},{key_kind}),{self._slot(value, types)});')
            elif kind == "set":
                out.append(f'    {_name(result)}=(long)piton_set_new({len(items)});')
                for value in items:
                    out.append(f'    piton_set_add((PitonSet*){_name(result)},{self._slot(value, types)});')
            else:
                raise NativeBuildError(f"Linux collection kind not supported: {kind}")
            types[result] = kind
        elif op == "collection_extend":
            # COLLECTION_EXTEND_V1: extender coleccion con iterable (starred en
            # LIST/TUPLE/SET: `[*a, b]`). dst es un PitonSeq*, src es cualquier
            # iterable.
            dst, src = args[0], args[1]
            out.append(f"    piton_seq_extend_any((PitonSeq*){_name(dst)},{self._value(src)});")
        elif op == "truth_test":
            # BOOL_SHORT_V1 / TRUTHY_FIX_V1: full truthiness per static kind
            # (CPython bool()): int/bool != 0, float bits != 0.0, str
            # length > 0, collections length > 0, None always false. The
            # raw `!value` used to misjudge empty strings/collections
            # (non-null pointers are always truthy).
            value = args[0]
            vtype = types.get(value, "int")
            out.append(f"    {_name(result)}=({self._truth_expr(value, vtype, strict=True)});")
            types[result] = "bool"
        elif op == "unpack_check":
            # UNPACK_ARITY_V1: CPython verifies the element count on
            # unpacking (ValueError: too many / not enough values).
            # list/tuple read their length; str counts bytes (str get_item
            # is byte-based, so units agree); anything else fails closed
            # at build (dict/set unpacking needs key iteration, a
            # separate item — today it dies at get_item with KeyError).
            value, expected = args[0], args[1]
            handler_label = args[2] if len(args) > 2 else None
            vtype = types.get(value)
            if vtype not in {"list", "tuple", "str"}:
                # UNPACK_DYNAMIC_V1: cualquier valor que no sea una secuencia
                # estatica decide en runtime. Es seguro tambien para un int
                # REAL: CPython levanta TypeError al desestructurarlo y el
                # helper levanta el mismo TypeError.
                out.append("    {")
                out.append(f"    long _ul=piton_unpack_len_dynamic({self._value(value)});")
                out.append(f"    if(_ul>=0&&_ul!={expected}){{piton_raise_set(\"ValueError\",\"unpack length mismatch\");}}")
                self._emit_exc_check(out, function, handler_label)
                out.append("    }")
                return out
            if vtype in {"list", "tuple"}:
                # NESTED_TUPLE_LIST_V1: unpack anidado en FOR-TARGET es
                # fail-closed — el backend no tiene runtime de unpack anidado
                # sobre iteradores (el build pasa pero crashea en runtime).
                # SOLO rechaza si el value viene de iter_next (for-target,
                # _iter_source seteado) Y el shape tiene un campo "tuple".
                # La asignacion directa `a, (b, c) = ...` (value de
                # build_collection, sin _iter_source) sigue soportada
                # (UNPACK_NESTED_V1).
                if vtype == "tuple" and self._iter_source.get(value) is not None:
                    _te = self._tuple_elems.get(value)
                    if _te is not None and any(t == "tuple" for _, t in _te):
                        raise NativeBuildError("Linux unpack of nested tuple targets is not supported")
                length = f"((PitonSeq*){self._value(value)})->length"
            elif vtype == "str":
                length = f"(long)piton_strlen((const char*){self._value(value)})"
            else:
                # NESTED_TUPLE_LIST_V1: el valor rendido por iter_next sobre una
                # coleccion de tuplas. Si es una tupla (elem_kind == "tuple"),
                # la aridad se resuelve desde _tuple_elems del SOURCE (cada
                # sub-item del outer heterogeneo tiene su shape en el build).
                # Si es "int" (homogeneo), la fuente debe ser lista/tupla de
                # tuplas anotada (_nested_tuple_lists) y la aridad compara
                # contra la longitud de la lista fuente. Sin anotacion -> fail.
                _src = self._iter_source.get(value)
                if vtype == "tuple" and _src is not None:
                    _te = self._tuple_elems.get(_src)
                    if _te is not None and len(_te) == expected:
                        length = f"((PitonSeq*){self._value(_src)})->length"
                    else:
                        raise NativeBuildError(f"Linux unpack requires a list, tuple or str, not {vtype}")
                else:
                    _nt = self._nested_tuple_lists.get(_src) if _src else None
                    if _nt is None or len(_nt) != expected:
                        raise NativeBuildError(f"Linux unpack requires a list, tuple or str, not {vtype}")
                    length = f"((PitonSeq*){self._value(_src)})->length"
            out.append("    {")
            out.append(f"    long _ul={length};")
            out.append(f"    if(_ul!={expected}){{")
            out.append(f'    if(_ul>{expected}){{piton_raise_set("ValueError","too many values to unpack (expected {expected})");}}')
            out.append("    else{const char*_ue[]={(const char*)piton_str_from_int(" + str(expected) + "),(const char*)piton_str_from_int(_ul)};")
            out.append('    piton_raise_set("ValueError",(const char*)piton_str_format("not enough values to unpack (expected {0}, got {1})",(long)2,_ue));}')
            out.append("    }")
            self._emit_exc_check(out, function, handler_label)
            out.append("    }")
        elif op == "get_item":
            coll, idx = args[0], args[1]
            _gi_handler = args[2] if len(args) > 2 else None
            collection_type = types.get(coll)
            if types.get(idx, "") == "slice" and collection_type in {"list", "tuple"}:
                # SLICE_APPLY_V1: sec[slice(...)] -> nueva secuencia.
                out.append(f"    {{PitonSeq* _sr=piton_seq_apply_slice((PitonSeq*){self._value(coll)},(PitonSlice*){self._value(idx)});")
                out.append(f"    {_name(result)}=(long)_sr;}}")
                if _gi_handler:
                    self._emit_exc_check(out, function, _gi_handler)
                types[result] = collection_type
                ek = self._coll_elems.get(coll)
                if ek and "|" not in ek:
                    self._coll_elems[result] = ek
                return out
            if collection_type == "memoryview":
                # MEMORYVIEW_V1: m[i] -> int del byte (vivo: refleja el buffer).
                if types.get(idx, "") == "slice":
                    out.append(
                        f"    {_name(result)}=piton_memoryview_slice((PitonMemoryView*){self._value(coll)},"
                        f"{self._value(idx) if True else 0});"
                    )
                    # el slice sobre memoryview usa los bounds del objeto slice
                    out.pop()
                    out.append(
                        f"    {{PitonSlice* _sl=(PitonSlice*){self._value(idx)};long _n=((PitonMemoryView*){self._value(coll)})->length;"
                        f"long _lo=_sl->has_start?(_sl->start<0?_sl->start+_n:_sl->start):0;"
                        f"long _hi=_sl->has_stop?(_sl->stop<0?_sl->stop+_n:_sl->stop):_n;"
                        f"if(_lo<0)_lo=0;if(_hi>_n)_hi=_n;if(_hi<_lo)_hi=_lo;"
                        f"{_name(result)}=piton_memoryview_new(((PitonMemoryView*){self._value(coll)})->buf,((PitonMemoryView*){self._value(coll)})->is_bytearray,((PitonMemoryView*){self._value(coll)})->offset+_lo*((PitonMemoryView*){self._value(coll)})->stride,_hi-_lo,((PitonMemoryView*){self._value(coll)})->stride);}}"
                    )
                    types[result] = "memoryview"
                    if coll in self._mv_noncontig:
                        self._mv_noncontig.add(result)
                    if _gi_handler:
                        self._emit_exc_check(out, function, _gi_handler)
                    return out
                out.append(f"    {_name(result)}=piton_memoryview_get((PitonMemoryView*){self._value(coll)},{self._value(idx)});")
                types[result] = "int"
                if _gi_handler:
                    self._emit_exc_check(out, function, _gi_handler)
                return out
            if collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_get((PitonSeq*){self._value(coll)},{self._value(idx)}).bits;')
                # COLL_ELEM_TYPE_V1: an index carries the element kind. Without it
                # `lista('abc')[1]` returned a str pointer in an int slot and
                # printed an address, and `[10 ** 20][0]` printed `<object>`.
                elem_kind = self._coll_elems.get(coll)
                # NESTED_TUPLE_LIST_V1: si el elem es tuple, delegar a la rama
                # de tuple para resolver el TIPO DEL CAMPO por indice — la rama
                # de elem_kind setea types[result]="tuple" (el puntero a la
                # tupla), no el tipo del campo indexado. Sin esto, `a = item[0]`
                # de una lista de tuplas heredaba "tuple" y `imprimir(a)` trataba
                # un int como puntero a seq -> SIGSEGV.
                if elem_kind == "tuple" and (self._tuple_elems.get(coll) or self._tuple_elems.get(aliases.get(coll, coll))):
                    pass  # fallthrough a la rama de tuple de abajo
                elif elem_kind and "|" not in elem_kind:
                    types[result] = elem_kind
                    if elem_kind == "tuple":
                        # NESTED_TUPLE_LIST_V1: el item de una lista de
                        # tuplas hereda la ESTRUCTURA de la tupla (para que
                        # el desempaquet de sus campos sepa el tipo de cada
                        # uno: `para k ,v en [('a' ,1 )]` -> k es str).
                        _nt_item = self._nested_tuple_lists.get(coll)
                        if _nt_item is None:
                            _nt_item = self._nested_tuple_lists.get(aliases.get(coll, coll))
                        if _nt_item is not None:
                            self._tuple_elems[result] = tuple(
                                (f"%{i}", t) for i, t in enumerate(_nt_item)
                            )
                    _tx = self._tuple_list_elems.get(coll) or self._tuple_list_elems.get(aliases.get(coll, coll))
                    if _tx is not None and elem_kind == "tuple":
                        self._tuple_elems[result] = (("%k", _tx[0]), ("%v", _tx[1]))
                    self._emit_exc_check(out, function, _gi_handler)
                    return out
                te = self._tuple_elems.get(coll) or self._tuple_elems.get(aliases.get(coll, coll))
                raw_idx = self._fn_consts.get(idx)
                if raw_idx is None:
                    raw_idx = self._fn_consts.get(aliases.get(idx, idx))
                if te is not None and isinstance(raw_idx, int):
                    # GET_ITEM_ELEM_NEG_V1: `t[-1]` (el resto tras `*` en el
                    # star-unpack usa indice negativo) normaliza como en
                    # CPython; sin esto la anotacion no aplicaba y el slot
                    # se imprimia como int crudo.
                    if raw_idx < 0:
                        raw_idx += len(te)
                    if 0 <= raw_idx < len(te):
                        types[result] = te[raw_idx][1]
                        # GET_ITEM_ELEM_PROP_V1: si el item es una tupla con
                        # estructura anotada, el resultado la hereda (cadena de
                        # niveles de `a,(b,(c,d))=...`) y el TIPO tambien: sin la
                        # anotacion el slot se imprimia como int (puntero crudo
                        # en `(*a, rest)=t` con rest de tipo tupla).
                        _sub = te[raw_idx][0]
                        if te[raw_idx][1] == "tuple" and isinstance(_sub, str) and _sub.startswith("%") \
                                and _sub in self._tuple_elems:
                            self._tuple_elems[result] = self._tuple_elems[_sub]
                            types[result] = "tuple"
                        self._emit_exc_check(out, function, _gi_handler)
                        return out
            elif collection_type in {"bytes", "bytearray"}:
                # BYTES_V1: b[i] is an int 0..255 (negative indices wrap).
                out.append(f'    {_name(result)}=piton_bytes_get((PitonBytes*){self._value(coll)},{self._value(idx)});')
                types[result] = "int"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif collection_type == "str" and types.get(idx, "") == "slice":
                # SLICE_APPLY_STR_V1: cad[slice(...)] -> nueva cadena.
                out.append(f"    {{long _sr=piton_str_apply_slice((const char*){self._value(coll)},(PitonSlice*){self._value(idx)});")
                out.append(f"    {_name(result)}=_sr;}}")
                if _gi_handler:
                    self._emit_exc_check(out, function, _gi_handler)
                types[result] = "str"
                return out
            elif collection_type == "str":
                # PARITY_P2_V1: str[s] yields the one-character string.
                out.append(f'    {_name(result)}=(long)piton_str_index((const char*){self._value(coll)},{self._value(idx)});')
                types[result] = "str"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif (
                isinstance(idx, str)
                and aliases.get(idx, idx).startswith("@comp_index")
                and collection_type in {"dict", "dict:module", "set"}
            ):
                # COMP_DICT_ITER_V1: a comprehension lowers to an index loop, and
                # the index temporaries are named `@comp_index_N` by convention
                # (like the `@boolh_` holders). Indexing a dict by an integer
                # raised KeyError, so `[k for k in {'a': 1}]` produced nothing,
                # while a `para` loop over the same dict worked. CPython
                # iteration over a dict yields KEYS, so that is what the
                # comprehension step must fetch.
                if collection_type in {"dict", "dict:module"}:
                    out.append(f'    {_name(result)}=piton_dict_nth_key((PitonDict*){self._value(coll)},{self._value(idx)}).bits;')
                    key_type = self._dict_key_types.get(coll, "str")
                    types[result] = key_type
                    self._emit_exc_check(out, function, _gi_handler)
                    return out
                out.append(f'    {_name(result)}=piton_set_nth((PitonSet*){self._value(coll)},{self._value(idx)}).bits;')
                types[result] = self._coll_elems.get(coll, "int")
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif collection_type == "range":
                # RANGE_VALUE_V1: indexing is exact, never materialized.
                out.append(f'    {_name(result)}=piton_range_get((PitonRange*){self._value(coll)},{self._value(idx)});')
                types[result] = "int"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif collection_type == "str":
                # STR_GET_ITEM_V1: s[i] -> str de un caracter (CPython);
                # s[slice(...)] -> str recortada. Antes caia al fallback
                # generico (PitonSeq*) -> SIGSEGV.
                if types.get(idx, "") == "slice":
                    out.append(
                        f'    {_name(result)}=piton_str_apply_slice((const char*){self._value(coll)},'
                        f'(PitonSlice*){self._value(idx)});'
                    )
                else:
                    out.append(
                        f'    {_name(result)}=piton_str_get((const char*){self._value(coll)},{self._value(idx)});'
                    )
                types[result] = "str"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif collection_type in {"dict", "dict:module"}:
                out.append(f'    {_name(result)}=piton_dict_get((PitonDict*){self._value(coll)},{self._slot(idx, types)}).bits;')
                # DICT_VAL_TYPE_V1: `d[k]` must carry the dict's value type, or
                # a str/bigint/float value is printed as a raw int slot (an
                # address). Unknown value type keeps the historical int.
                val_type = self._dict_val_types.get(coll)
                if val_type is not None:
                    types[result] = val_type
                else:
                    # GETITEM_MODULE_TYPE_V1: sin tipo de valor conocido, el
                    # fall-through histórico tipaba dict:module como
                    # "object:module" (sys.modules["x"].__package__ lo needs).
                    types[result] = "object:module" if collection_type == "dict:module" else "int"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            elif collection_type is not None and collection_type.startswith("object:"):
                # GETITEM_DUNDER_V1: obj[i] despacha a __getitem__ por MRO.
                # Antes caía al fallback genérico y leía un PitonObject* como
                # PitonSeq* -> SIGSEGV.
                cls_name = collection_type.split(":", 1)[1]
                hit = None
                for candidate in self.class_mro.get(cls_name, [cls_name]):
                    if "__getitem__" in self.classes.get(candidate, set()):
                        hit = candidate
                        break
                if hit is None and "__getitem__" in self.classes.get(cls_name, set()):
                    hit = cls_name
                if hit is None:
                    raise NativeBuildError(
                        f"Linux native subscript on a '{cls_name}' instance requires __getitem__"
                    )
                target = _name(hit + "____getitem__")
                if self.function_frame_abi.get(hit + "____getitem__", False):
                    out.append(f'    {{long _gi_args[]={{ {self._value(coll)},{self._value(idx)} }}; {_name(result)}=piton_frame_call((long)&{target},2,_gi_args);}}')
                else:
                    out.append(f"    {_name(result)}={target}({self._value(coll)},{self._value(idx)});")
                types[result] = "int"
                self._emit_exc_check(out, function, _gi_handler)
                return out
            else:
                if types.get(idx) == "str":
                    out.append(f'    {_name(result)}=piton_dict_get((PitonDict*){self._value(coll)},{self._slot(idx, types)}).bits;')
                else:
                    out.append(f'    {_name(result)}=piton_seq_get((PitonSeq*){self._value(coll)},{self._value(idx)}).bits;')
                types[result] = "int"
                self._emit_exc_check(out, function, _gi_handler)
                return out
        elif op == "get_slice":
            # PARITY_P2_V1: [a:b] slices. Missing bounds arrive as None; the
            # emitter substitutes 0 / INT64_MAX and the C helpers normalize
            # negative indices and clamp, matching CPython semantics.
            # SLICE_STEP_V1: with a step operand (possibly runtime), missing
            # bounds become INT64_MIN / INT64_MAX sentinels and the step
            # helpers apply direction-aware defaults.
            coll, lower, upper = args[0], args[1], args[2]
            step = args[3] if len(args) > 3 else None
            # STARRED_SLICE_LIST_V1: `as_list` llega como 5to arg (MIR starred
            # unpack); step=None via args[3].
            as_list = len(args) > 4 and args[4] == "as_list"
            collection_type = types.get(coll)
            if step is None:
                lower_value = self._value(lower) if lower is not None else "0"
                upper_value = self._value(upper) if upper is not None else "0x7FFFFFFFFFFFFFFFL"
                if collection_type == "str":
                    if as_list:
                        # STARRED_SLICE_LIST_V1: `*b` sobre str -> lista de chars
                        # (CPython: a,*b='xyz' -> b=['y','z']). piton_str_slice
                        # devuelve bits (long) y items es PitonSlot{bits,kind}.
                        out.append(
                            f'    {{const char*_cs=(const char*)piton_str_slice((const char*){self._value(coll)},{lower_value},{upper_value});'
                            f'long _n=(long)piton_strlen(_cs);PitonSeq*_sl=piton_seq_new(PK_LIST,_n);'
                            f'for(long _si=0;_si<_n;_si++)_sl->items[_si]=(PitonSlot){{(long)piton_str_slice(_cs+_si,0,1),PK_STR}};'
                            f'{_name(result)}=(long)_sl;}}'
                        )
                        types[result] = "list"
                    else:
                        out.append(f'    {_name(result)}=(long)piton_str_slice((const char*){self._value(coll)},{lower_value},{upper_value});')
                        types[result] = "str"
                elif collection_type == "range":
                    out.append(f'    {_name(result)}=piton_range_slice((PitonRange*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = "range"
                elif collection_type in {"bytes", "bytearray"}:
                    fn = "piton_bytes_slice" if collection_type == "bytes" else "piton_bytearray_slice"
                    cty = "PitonBytes" if collection_type == "bytes" else "PitonByteArray"
                    out.append(f'    {_name(result)}=(long){fn}(({cty}*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = collection_type
                elif collection_type == "tuple" and as_list:
                    # tupla -> copia explicita a PK_LIST (CPython: *mid es list)
                    out.append(
                        f'    {{PitonSeq*_ss=piton_seq_slice((PitonSeq*){self._value(coll)},{lower_value},{upper_value});'
                        f'PitonSeq*_sl=piton_seq_new(PK_LIST,_ss->length);'
                        f'for(long _si=0;_si<_ss->length;_si++)_sl->items[_si]=_ss->items[_si];'
                        f'{_name(result)}=(long)_sl;}}'
                    )
                    types[result] = "list"
                elif collection_type in {"list", "tuple"}:
                    out.append(f'    {_name(result)}=piton_seq_slice((PitonSeq*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = collection_type
                    if coll in self._coll_elems:
                        self._coll_elems[result] = self._coll_elems[coll]
                elif collection_type == "memoryview":
                    # MEMORYVIEW_SLICE_V1: m[a:b] -> vista (comparte buffer).
                    out.append(
                        f"    {_name(result)}=piton_memoryview_slice((PitonMemoryView*){self._value(coll)},{lower_value},{upper_value});"
                    )
                    types[result] = "memoryview"
                    if coll in self._mv_noncontig:
                        self._mv_noncontig.add(result)
                    return out
                else:
                    # SLICE_DYNAMIC_V1: tipo no conocido -> lo decide el runtime
                    # (secuencia si lo es, TypeError si no).
                    out.append(
                        f"    {_name(result)}=piton_slice_dynamic({self._value(coll)},"
                        f"{lower_value},{upper_value},0,0);"
                    )
                    self._emit_exc_check(out, function, None)
                    # tipo DINAMICO: la forma la decide el header; imprimir
                    # pasa por el despacho por cabecera de print_int_safe.
                    types[result] = "int"
                return out
            lower_value = self._value(lower) if lower is not None else "(-0x7FFFFFFFFFFFFFFFL-1)"
            upper_value = self._value(upper) if upper is not None else "0x7FFFFFFFFFFFFFFFL"
            step_value = self._value(step)
            if collection_type == "str":
                out.append(f'    {_name(result)}=(long)piton_str_slice_step((const char*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = "str"
            elif collection_type == "range":
                out.append(f'    {_name(result)}=piton_range_slice_step((PitonRange*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = "range"
            elif collection_type in {"bytes", "bytearray"}:
                fn = "piton_bytes_slice_step" if collection_type == "bytes" else "piton_bytearray_slice_step"
                cty = "PitonBytes" if collection_type == "bytes" else "PitonByteArray"
                out.append(f'    {_name(result)}=(long){fn}(({cty}*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = collection_type
            elif collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_slice_step((PitonSeq*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = collection_type
            elif collection_type == "memoryview":
                # MEMORYVIEW_STRIDE_V1: vista con stride, sin copia.
                out.append(
                    f"    {_name(result)}=piton_memoryview_slice_step((PitonMemoryView*){self._value(coll)},{lower_value},{upper_value},{step_value});"
                )
                types[result] = "memoryview"
                # Contiguo solo si el padre lo era y el step es literal 1.
                if step_value != "1" or coll in self._mv_noncontig:
                    self._mv_noncontig.add(result)
            else:
                # SLICE_DYNAMIC_V1: igual que sin step, decidido en runtime.
                out.append(
                    f"    {_name(result)}=piton_slice_dynamic({self._value(coll)},"
                    f"{lower_value},{upper_value},1,{step_value});"
                )
                self._emit_exc_check(out, function, None)
                types[result] = "int"
        elif op == "collection_len":
            coll = args[0]
            collection_type = types.get(coll)
            if collection_type == "range":
                # RANGE_VALUE_V1: exact, never materialized.
                out.append(f'    {_name(result)}=piton_range_len((PitonRange*){self._value(coll)});')
                types[result] = "int"
                return out
            if collection_type == "str":
                out.append(f'    {_name(result)}=(long)piton_strlen((const char*){self._value(coll)});')
                types[result] = "int"
                return out
            if collection_type and (collection_type.startswith("iterator:") or collection_type in {"generator", "genexpr"}):
                # COMP_ITER_V1: a comprehension over an iterator materializes
                # it IN PLACE (the temp is rewritten as the list), so the
                # following get_item on that same temp reads the list.
                _which = {
                    "iterator:enumerate": 0, "iterator:reversed": 1,
                    "iterator:map": 2, "iterator:filter": 2, "iterator:zip": 3,
                    "generator": 4, "genexpr": 4,
                }.get(collection_type)
                if _which is None:
                    raise NativeBuildError(f"Linux collection_len requires a collection (not {collection_type})")
                _tuple_items = collection_type in {"iterator:enumerate", "iterator:zip"}
                _elemkind = "PK_TUPLE" if _tuple_items else "PK_INT"
                # the loop condition re-evaluates this every iteration: a
                # static flag keeps the materialization a ONE-TIME event.
                _mat = "_piton_mat" + re.sub(r"[^A-Za-z0-9]", "", _name(coll))
                # COMP_ITER_V1: si el iterador rinde tuplas (enumerate/zip),
                # el target tupla necesita los tipos POR ÍNDICE: se dejan
                # anotados para el desempaquetado del cuerpo.
                if _tuple_items and collection_type == "iterator:enumerate":
                    # enumerate rinde (int, elemento): el primer índice es
                    # siempre int; el segundo es el tipo de la fuente.
                    _src_kind = self._enum_elem.get(args[0]) or self._coll_elems.get(args[0])
                    if not _src_kind:
                        _src_kind = self._coll_elems.get(self._iter_source.get(args[0], ""))
                    _val_kind = _src_kind
                    self._tuple_list_elems[coll] = ("int", _val_kind if _val_kind and "|" not in _val_kind else "int")
                out.append(f"    static int {_mat}_done=0;")
                out.append(f"    if(!{_mat}_done){{{_mat}_done=1;")
                out.append(f"    long {_mat}=piton_collect({_which},{self._value(coll)},{_elemkind});")
                out.append(f"    {_name(coll)}={_mat};}}")
                types[coll] = "list"
                self._coll_elems[coll] = "tuple" if _tuple_items else "int"
                out.append(f'    {_name(result)}=((PitonSeq*){self._value(coll)})->length;')
                types[result] = "int"
                return out
            struct_name = {"list": "PitonSeq", "tuple": "PitonSeq", "dict": "PitonDict", "set": "PitonSet"}.get(collection_type)
            if not struct_name:
                out.append(f'    {_name(result)}=({self._value(coll)} ? ((PitonSeq*){self._value(coll)})->length : 0);')
                types[result] = "int"
                return out
            out.append(f'    {_name(result)}=(({struct_name}*){self._value(coll)})->length;')
            types[result] = "int"
        elif op == "list_append":
            coll, value = args
            collection_type = types.get(coll)
            if collection_type not in ("list", "tuple"):
                raise NativeBuildError("Linux list_append requires a list or tuple")
            out.append(f'    {{PitonSeq*_c=(PitonSeq*){self._value(coll)};PitonSlot _s;_s.bits={self._value(value)};_s.kind={self._kind(types.get(value, "int"))};piton_seq_append(_c,_s);}}')
            # LIST_APPEND_ELEM_V1: el valor appendido define el elem-kind de
            # la lista (getopt: la comprension `[o for o in longopts]` deja
            # la lista sin elem-kind, y `possibilities[0]` quedaba sin tipo
            # -> `unique_match[:-1]` fallaba cerrado). kind mezclado -> "a|b"
            # (marcado como no uniforme, como el resto del backend).
            _val_kind = types.get(value, "int")
            if _val_kind and "|" not in _val_kind:
                _cur_kind = self._coll_elems.get(coll)
                if _cur_kind is None:
                    self._coll_elems[coll] = _val_kind
                elif "|" not in _cur_kind and _cur_kind != _val_kind:
                    self._coll_elems[coll] = f"{_cur_kind}|{_val_kind}"
                # el store del nombre puede preceder a los appends (la
                # comprension se llena despues del store) -> propagar el
                # elem-kind a los nombres que guardaron esa lista.
                for _nm in self._store_targets.get(coll, ()):
                    if self._coll_elems.get(_nm) is None:
                        self._coll_elems[_nm] = self._coll_elems.get(coll)
        elif op == "gen_init":
            func_name = args[0]
            gen_args = tuple(args[1]) if len(args) > 1 and args[1] else ()
            layout = self.generator_layouts.get(func_name)
            if layout is None:
                raise NativeBuildError(f"native generator '{func_name}' has no persisted-slot layout")
            params = self.function_params.get(func_name, [])
            vararg = self.function_varargs.get(func_name)
            _named2 = [p for p in params if p != vararg]
            if self.function_kwargs.get(func_name):
                raise NativeBuildError(
                    f"native generator '{func_name}' with **kwargs is not supported yet"
                )
            if vararg is None:
                if len(gen_args) != len(_named2):
                    raise NativeBuildError(
                        f"native generator '{func_name}' called with wrong number of arguments "
                        "(defaults not supported yet)"
                    )
                # GEN_ARGS_N_V1: los argumentos se empaquetan slot por slot
                # (ver _emit_gen_args), no hay límite de registros — el
                # guard de >4 args era conservador.
            else:
                if len(gen_args) < len(_named2):
                    raise NativeBuildError(
                        f"native generator '{func_name}' called with wrong number of arguments "
                        "(defaults not supported yet)"
                    )
                if vararg not in layout:
                    raise NativeBuildError(
                        f"native generator '{func_name}' *args slot is missing from the persisted layout"
                    )
            self._emit_gen_args(out, result, func_name, layout, params, vararg, list(gen_args), types)
            types[result] = "generator"
        elif op == "gen_next":
            out.append(f"    {_name(result)}=piton_gen_next({self._value(args[0])});")
            types[result] = "int"
        elif op == "gen_throw":
            gen_ref, exc_type, handler_label = args
            out.append(f"    {_name(result)}=piton_gen_throw({self._value(gen_ref)},{json.dumps(exc_type)});")
            types[result] = "int"
            out.append("    if(piton_exc_flag){")
            if handler_label:
                out.append(f"        goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("        piton_report_unhandled();piton_exit(1);")
            out.append("    }")
        elif op == "gen_close":
            # FILE_IO_V1: el lowerer manda `.close()` a gen_close; sobre un
            # handle de fichero es `piton_file_close` (cerrar el descriptor),
            # no el cierre de un generador.
            _gc_type = types.get(args[0], "")
            if _gc_type == "file" or aliases.get(args[0], "") in self._files:
                out.append(f"    piton_file_close((PitonFile*){self._value(args[0])});{_name(result)}=0;")
                types[result] = "int"
                return out
            out.append(f"    {_name(result)}=piton_gen_close({self._value(args[0])});")
            types[result] = "int"
        elif op == "coro_run":
            out.append(f"    {_name(result)}=piton_coro_run({self._value(args[0])});")
            types[result] = "int"
        elif op == "event_run":
            out.append(f"    {_name(result)}=piton_event_run({self._value(args[0])});")
            types[result] = "int"
        elif op == "call_unpack":
            # CALL_UNPACKING_DYNAMIC4_V1: Linux keeps the value kind in
            # PitonSlot, so ** operands can be checked safely at runtime.
            func_name, pos_parts, kw_parts, handler_label = args
            if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1 is not supported inside generator bodies yet")
            if func_name not in self.function_names:
                raise NativeBuildError("native dynamic call unpacking requires a module-level function callee")
            f_params = self.function_params.get(func_name) or []
            if not f_params or len(f_params) > 4:
                raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1 supports callees with one to four parameters")
            f_defaults = self.function_defaults.get(func_name) or []
            n_params = len(f_params)
            f_vararg = self.function_varargs.get(func_name)
            f_kwarg = self.function_kwargs.get(func_name)
            if f_kwarg is not None:
                # CALL_UNPACK_VARIADIC_V1: `f(**kw)` sobre un callee **kwargs
                # pasa el dict directamente (el parámetro ya ES un dict).
                kwstars = [part for part in kw_parts if part[0] == "kwstar"]
                if len(kwstars) == 1 and len(kw_parts) == 1 and not pos_parts:
                    out.append(f"    {_name(result)}={_name(func_name)}((long){self._value(kwstars[0][2])});")
                    self._emit_exc_check(out, function, handler_label)
                    return out
                raise NativeBuildError("Linux dynamic ** unpacking over a **kwargs callee supports only f(**kw)")
            if f_vararg is not None:
                # CALL_UNPACK_VARIADIC_V1: `f(*xs)` sobre un callee *args.
                # Los primeros NN elementos llenan los parámetros fijos; el
                # resto se empaqueta en la tupla del vararg (mismo ABI que la
                # llamada directa, que también pasa un PitonSeq* PK_TUPLE).
                named = [p for p in f_params if p != f_vararg]
                n_named = len(named)
                slots = max(n_named, 1)
                site = f"unpack_{self._gen_counter}"
                self._gen_counter += 1
                sentinel = "0x504954554E424E44LL"
                out.append("    {")
                out.append(f"    long b_{site}[{slots}]; long f_{site}=0;")
                for idx in range(n_named):
                    if idx < len(f_defaults) and f_defaults[idx] is not None:
                        out.append(f"    b_{site}[{idx}]={self._value(f_defaults[idx])};")
                    else:
                        out.append(f"    b_{site}[{idx}]={sentinel};")
                out.append(f"    PitonSeq* x_{site}=piton_seq_new(PK_TUPLE,0);")
                for part in pos_parts:
                    if part[0] == "value":
                        out.append(f"    if(f_{site}<{n_named}){{b_{site}[(int)f_{site}]=(long)({self._value(part[1])});f_{site}+=1;}}")
                        out.append(f"    else piton_seq_append(x_{site},piton_slot((long)({self._value(part[1])}),PK_INT));")
                    else:
                        ptype = types.get(part[1], "")
                        if ptype not in {"list", "tuple"}:
                            raise NativeBuildError("CALL_UNPACK_VARIADIC_V1: * operand must be a statically-known list or tuple")
                        out.append(f"    {{PitonSeq* s_{site}=(PitonSeq*){self._value(part[1])};")
                        out.append(f"     long k_{site}=0;")
                        out.append(f"     while(f_{site}<{n_named}&&k_{site}<s_{site}->length){{b_{site}[(int)f_{site}]=s_{site}->items[k_{site}].bits;f_{site}+=1;k_{site}+=1;}}")
                        out.append(f"     for(;k_{site}<s_{site}->length;++k_{site})piton_seq_append(x_{site},s_{site}->items[k_{site}]);}}")
                for idx in range(n_named):
                    if not (idx < len(f_defaults) and f_defaults[idx] is not None):
                        out.append(f"    if(b_{site}[{idx}]=={sentinel}){{piton_raise_set(\"TypeError\",\"missing required positional argument\");goto {site}_runtime_err;}}")
                call_args = ",".join(f"b_{site}[{idx}]" for idx in range(n_named)) + (f",(long)x_{site}" if n_named else f"(long)x_{site}")
                out.append(f"    {_name(result)}={_name(func_name)}({call_args});")
                out.append(f"    goto {site}_done;")
                out.append(f"    {site}_runtime_err:")
                if handler_label:
                    out.append(f"    goto {_name(function.name + '_' + handler_label)};")
                else:
                    out.append("    piton_report_unhandled();piton_exit(1);")
                out.append(f"    {site}_done: ;")
                out.append("    }")
                types[result] = "int"
                return out
            # CALL_STAR_DYNAMIC_V1: el `*` que no es list/tuple estatico se
            # decide en runtime. Es al menos tan fiel como el build error: un
            # int real levanta el TypeError de CPython (capturable con
            # intentar/excepto) y un valor que SI era secuencia compila.
            for part in kw_parts:
                if part[0] == "kwstar" and types.get(part[2]) != "dict":
                    raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1: ** operand must be a dict")
            site = f"unpack_{self._gen_counter}"
            self._gen_counter += 1
            sentinel = "0x504954554E424E44LL"
            out.append("    {")
            out.append(f"    long b_{site}[4]; long f_{site}=0; long m_{site}=0;")
            for idx in range(4):
                if idx >= n_params:
                    out.append(f"    b_{site}[{idx}]={sentinel};")
                elif idx < len(f_defaults) and f_defaults[idx] is not None:
                    out.append(f"    b_{site}[{idx}]={self._value(f_defaults[idx])};")
                else:
                    out.append(f"    b_{site}[{idx}]={sentinel};")
            for part in pos_parts:
                if part[0] == "value":
                    out.append(f"    if(f_{site}>={n_params}){{piton_raise_set(\"TypeError\",\"too many positional arguments for call\");goto {site}_runtime_err;}}")
                    out.append(f"    b_{site}[(int)f_{site}]=(long)({self._value(part[1])}); m_{site}|=1LL<<(int)f_{site}; f_{site}+=1;")
                elif types.get(part[1]) in {"list", "tuple"}:
                    out.append(f"    long n_{site}=piton_unpack_seq4((PitonSlot){{(long){self._value(part[1])},{'PK_LIST' if types.get(part[1]) == 'list' else 'PK_TUPLE'}}},{n_params}-f_{site},b_{site}+(int)f_{site});")
                    out.append(f"    if(n_{site}<0)goto {site}_runtime_err; if(n_{site}>0){{m_{site}|=((1LL<<n_{site})-1)<<(int)f_{site}; f_{site}+=n_{site};}}")
                else:
                    # CALL_STAR_DYNAMIC_V1
                    out.append(f"    long n_{site}=piton_unpack_seq4_dynamic((long){self._value(part[1])},{n_params}-f_{site},b_{site}+(int)f_{site});")
                    out.append(f"    if(n_{site}<0)goto {site}_runtime_err; if(n_{site}>0){{m_{site}|=((1LL<<n_{site})-1)<<(int)f_{site}; f_{site}+=n_{site};}}")
            for part in kw_parts:
                if part[0] == "keyword":
                    name, value = part[1], part[2]
                    if name not in f_params:
                        raise NativeBuildError(f"unexpected keyword argument: {name}")
                    idx = f_params.index(name)
                    out.append(f"    if(m_{site}&(1LL<<{idx})){{piton_raise_set(\"TypeError\",\"multiple values for argument\");goto {site}_runtime_err;}}")
                    out.append(f"    m_{site}|=1LL<<{idx}; b_{site}[{idx}]=(long)({self._value(value)});")
                else:
                    names = ",".join(json.dumps(param) for param in f_params)
                    out.append(f"    static const char* names_{site}[{n_params}]={{{names}}};")
                    out.append(f"    if(piton_dict_unpack4((PitonSlot){{(long){self._value(part[2])},PK_DICT}},names_{site},{n_params},b_{site},&m_{site})<0)goto {site}_runtime_err;")
            for idx in range(n_params):
                if not (idx < len(f_defaults) and f_defaults[idx] is not None):
                    out.append(f"    if(b_{site}[{idx}]=={sentinel}){{piton_raise_set(\"TypeError\",\"missing required positional argument\");goto {site}_runtime_err;}}")
            out.append(f"    {_name(result)}={_name(func_name)}(" + ",".join(f"b_{site}[{idx}]" for idx in range(n_params)) + ");")
            out.append(f"    goto {site}_done;")
            out.append(f"    {site}_runtime_err:")
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("    piton_report_unhandled();piton_exit(1);")
            out.append(f"    {site}_done: ;")
            out.append("    }")
            types[result] = "int"
        elif op == "task_new":
            out.append(f"    {_name(result)}=piton_task_new({self._value(args[0])});")
            types[result] = "task"
        elif op == "task_cancel":
            out.append(f"    {_name(result)}=piton_task_cancel({self._value(args[0])});")
            types[result] = "int"
        elif op == "sleep0":
            out.append(f"    {_name(result)}=piton_sleep0({self._value(args[0])});")
            types[result] = "int"
        elif op == "gather_new":
            out.append(f"    {_name(result)}=piton_gather_new({int(args[0])});")
            types[result] = "gather"
        elif op == "gather_add":
            gather_ref, index, task_ref = args
            out.append(f"    {_name(result)}=piton_gather_add({self._value(gather_ref)},{int(index)},{self._value(task_ref)});")
            types[result] = "int"
        elif op == "gen_retval":
            gen_ref = args[0]
            out.append(f"    {_name(result)}=((PitonGenerator*){self._value(gen_ref)})->slots[62];")
            types[result] = "int"
        elif op == "gen_send":
            gen_ref, send_value, handler_label = args
            out.append(f"    {_name(result)}=piton_gen_send({self._value(gen_ref)},{self._value(send_value)});")
            types[result] = "int"
            out.append("    if(piton_exc_flag){")
            if handler_label:
                out.append(f"        goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("        piton_report_unhandled();piton_exit(1);")
            out.append("    }")
        elif op == "gen_collect":
            out.append(f"    {_name(result)}=piton_gen_collect({self._value(args[0])});")
            types[result] = "list"
        elif op == "gen_yield":
            if not (getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False)):
                raise NativeBuildError("Linux gen_yield outside a generator/coroutine body is not supported")
            self._emit_linux_gen_suspend(out, types, args[0] if args else None, result,
                                         await_flag=getattr(function, "is_async_generator", False))
        elif op == "agen_emit":
            if not getattr(function, "is_async_generator", False):
                raise NativeBuildError("Linux agen_emit outside an async generator body is not supported")
            # ASYNC_GENERATOR_V1: data yield inside an async generator; clears
            # the await marker (slot 63) so piton_agen_next returns it as data.
            self._emit_linux_gen_suspend(out, types, args[0] if args else None, result, await_flag=False)
        elif op == "agen_next":
            if not args:
                raise NativeBuildError("agen_next requires an async generator operand")
            out.append(f"    {_name(result)}=piton_agen_next({self._value(args[0])});")
            types[result] = "int"
        elif op == "agen_done":
            if not args:
                raise NativeBuildError("agen_done requires an async generator operand")
            out.append(f"    {_name(result)}=((PitonGenerator*){self._value(args[0])})->finished;")
            types[result] = "int"
        elif op == "genexpr_new":
            source = args[0]
            if types.get(source) != "list":
                raise NativeBuildError("Linux genexpr requires a list-backed sequence")
            out.append(f"    {_name(result)}=piton_genexpr_new((PitonSeq*){self._value(source)});")
            types[result] = "genexpr"
        elif op == "set_add":
            coll, value = args
            if types.get(coll) != "set":
                raise NativeBuildError("Linux set_add requires a set")
            out.append(f'    piton_set_add((PitonSet*){self._value(coll)},{self._slot(value, types)});')
        elif op == "dict_put":
            coll, key, value = args
            if types.get(coll) != "dict":
                raise NativeBuildError("Linux dict_put requires a dict")
            out.append(f'    piton_dict_append((PitonDict*){self._value(coll)},piton_slot({self._value(key)},{self._kind(types.get(key, "int"))}),{self._slot(value, types)});')
            _ptarget = coll if coll in self._dict_elems else aliases.get(coll)
            if _ptarget is not None and _ptarget in self._dict_elems:
                _ckey = key if (isinstance(key, str) and not key.startswith("%")) else None
                if _ckey is None:
                    try:
                        _ckey = self._fn_consts.get(key)
                    except TypeError:
                        _ckey = None
                if isinstance(_ckey, str):
                    if isinstance(value, str) and value.startswith("%"):
                        self._dict_elems[_ptarget][_ckey] = (value, types.get(value, "int"))
                    else:
                        try:
                            self._dict_elems[_ptarget][_ckey] = (value, self._percent_literal_type(value))
                        except NativeBuildError:
                            del self._dict_elems[_ptarget]
                else:
                    # unknown key may overwrite a known one: drop the record
                    # so later mapping use fails closed (sound over-strict).
                    del self._dict_elems[_ptarget]
        elif op == "runtime_call":
            raise NativeBuildError(f"runtime operation not supported in Linux native subset: {args[0]}")
        else:
            raise NativeBuildError(f"Linux MIR operation not supported: {op}")
        return out


def windows_to_wsl_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        if _is_native_linux():
            return str(resolved)
        raise NativeBuildError(f"WSL path requires a Windows drive: {resolved}")
    tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{tail}"



def _is_native_linux() -> bool:
    """True when running on native Linux (not WSL or Windows)."""
    return Path("/").resolve().drive == ""


def linux_run_cmd(args: list[str]) -> list[str]:
    """Wrap *args* for execution on the current platform.

    On native Linux the command runs directly.  Under WSL the command is
    prefixed with ``wsl.exe /usr/bin/env -i`` so that it executes in a
    clean environment inside WSL.
    """
    if _is_native_linux():
        return args
    return ["wsl.exe", "/usr/bin/env", "-i"] + args


def _gcc_compile(c_path: Path, output_path: Path) -> None:
    """Compile a freestanding ELF using the available gcc."""
    if _is_native_linux():
        cmd = [
            "gcc", "-std=c11", "-O2", "-ffreestanding",
            "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdlib", "-static",
            str(c_path), "-o", str(output_path),
        ]
    else:
        cmd = [
            "wsl.exe", "gcc", "-std=c11", "-O2", "-ffreestanding",
            "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdlib", "-static",
            windows_to_wsl_path(c_path), "-o", windows_to_wsl_path(output_path),
        ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise NativeBuildError(completed.stderr or completed.stdout or f"Linux compiler exited {completed.returncode}")


def compile_native_linux(source: str, output: str | Path) -> Path:
    try:
        mir = lower_hir_to_mir(lower_cst_to_hir(parse(source)))
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="piton-linux-") as directory:
        c_path = Path(directory) / "program.c"
        c_path.write_text(LinuxCEmitter().emit(mir), encoding="utf-8")
        _gcc_compile(c_path, output_path)
    return output_path


def compile_native_linux_files(entry: str | Path, output: str | Path) -> Path:
    """Compila un entry multi-módulo (hermanos, paquetes con ``__init__.piton``,
    submódulos ``desde pkg.sub importar fn``) a ELF con el backend Linux."""
    entry_path = Path(entry).resolve()
    hir = lower_cst_to_hir(parse(entry_path.read_text(encoding="utf-8-sig")))
    modules, from_imports, module_meta = _scan_native_modules(entry_path)
    try:
        mir = lower_hir_to_mir(
            hir, modules, from_imports=from_imports,
            entry_file=str(entry_path), module_meta=module_meta,
        )
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="piton-linux-") as directory:
        c_path = Path(directory) / "program.c"
        c_path.write_text(LinuxCEmitter().emit(mir), encoding="utf-8")
        _gcc_compile(c_path, output_path)
    return output_path


def build_single_file_initramfs(executable: str | Path, output: str | Path) -> Path:
    """Build a deterministic newc initramfs containing only executable /init."""
    payload = Path(executable).read_bytes()

    def entry(name: str, data: bytes, mode: int, inode: int) -> bytes:
        encoded_name = name.encode("ascii") + b"\0"
        fields = (
            inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0,
            len(encoded_name), 0,
        )
        header = b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in fields)
        record = header + encoded_name
        record += b"\0" * (-len(record) % 4)
        record += data
        record += b"\0" * (-len(record) % 4)
        return record

    archive = entry("init", payload, 0o100755, 1) + entry("TRAILER!!!", b"", 0, 2)
    output_path = Path(output).resolve()
    output_path.write_bytes(gzip.compress(archive, mtime=0))
    return output_path
