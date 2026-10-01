"""Backend ELF x86-64 Linux para el subconjunto MIR escalar."""
from __future__ import annotations

import json
import gzip
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .lower import LoweringError, lower_cst_to_hir
from .mir import MIRFunction, MIRInstruction, MIRLoweringError, MIRModule, lower_hir_to_mir
from .parser import parse
from .x86 import NativeBuildError, _BUILTINS, _scan_native_modules, generator_slot_layout


# FLOAT_REPR_V1: shortest round-trip float -> text, shared VERBATIM with
# piton/native_runtime.c (single source of truth: piton/float_repr.h).
# Read at import time and spliced BEFORE the runtime below, because the
# float printers live inside the runtime string.
_FLOAT_REPR_C = Path(__file__).with_name("float_repr.h").read_text(encoding="utf-8")

_RICH_FREESTANDING_C = r"""
enum{PK_NONE,PK_BOOL,PK_INT,PK_FLOAT,PK_STR,PK_LIST,PK_TUPLE,PK_DICT,PK_SET,PK_OBJECT,PK_BIGINT};
typedef struct{long bits;int kind;}PitonSlot;
static unsigned char piton_arena[8*1024*1024];
static usize piton_arena_used=0;
static void piton_memzero(void*p,usize n){unsigned char*b=p;for(usize i=0;i<n;++i)b[i]=0;}
static void piton_memcpy(void*d,const void*s,usize n){unsigned char*dd=d;const unsigned char*ss=s;for(usize i=0;i<n;++i)dd[i]=ss[i];}
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
/* Refcount helpers */
static long piton_slot_rc(PitonSlot v){
    if(v.kind>=PK_LIST&&v.kind<=PK_OBJECT){long*rc=(long*)v.bits;return*rc;}
    return-1;
}
static void piton_slot_incref(PitonSlot v){if(v.kind>=PK_LIST&&v.kind<=PK_OBJECT){long*rc=(long*)v.bits;++(*rc);}}
static void piton_slot_decref(PitonSlot v);
/* Deep free: decrements refcount; if zero, detach children and free struct */
static void piton_slot_decref(PitonSlot v){
    if(v.kind<PK_LIST||v.kind>PK_OBJECT)return;
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
static long piton_float_add(long a,long b){return piton_double_bits(piton_bits_double(a)+piton_bits_double(b));}
static long piton_float_sub(long a,long b){return piton_double_bits(piton_bits_double(a)-piton_bits_double(b));}
static long piton_float_mul(long a,long b){return piton_double_bits(piton_bits_double(a)*piton_bits_double(b));}
static long piton_float_neg(long a){return(long)((unsigned long)a^(1UL<<63));}
static long piton_float_sqrt(long a){double x=piton_bits_double(a),r;__asm__ volatile("sqrtsd %1,%0":"=x"(r):"x"(x));return piton_double_bits(r);}
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
static void piton_write_uint(unsigned long v){char b[32];usize i=sizeof(b);do{b[--i]=(char)('0'+v%10);v/=10;}while(v);piton_write(1,b+i,sizeof(b)-i);}
static void piton_write_uint_big(double d){unsigned long u;double t=d;int i,n=0,be;unsigned long frac,m;int e;unsigned int w[32];unsigned int c;unsigned long cur,rem;int nz;char buf[400];__builtin_memcpy(&u,&t,8);frac=u&0xFFFFFFFFFFFFFULL;be=(int)((u>>52)&0x7FF);m=be?frac|0x10000000000000UL:frac;e=be?be-1075:-1074;for(i=0;i<32;++i)w[i]=0;w[0]=(unsigned int)m;w[1]=(unsigned int)(m>>32);while(e>0){c=0;for(i=0;i<32;++i){cur=((unsigned long)w[i]<<1)|c;w[i]=(unsigned int)cur;c=(unsigned int)(cur>>32);}--e;}for(;;){rem=0;nz=0;for(i=31;i>=0;--i){cur=(rem<<32)|w[i];w[i]=(unsigned int)(cur/10);rem=cur%10;if(w[i])nz=1;}buf[n++]=(char)('0'+(char)rem);if(!nz)break;}while(n>0){--n;piton_write(1,buf+n,1);}}
static void piton_print_float_bits_raw(long bits){char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)bits);piton_write(1,r,(usize)n);}
static void piton_print_float_bits(long bits){piton_print_float_bits_raw(bits);piton_write(1,"\n",1);}
static int piton_slot_eq(PitonSlot a,PitonSlot b){if(a.kind!=b.kind)return 0;if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)==0;return a.bits==b.bits;}
static PitonSeq*piton_seq_new(int kind,long n){PitonSeq*s=piton_alloc(sizeof(*s));s->refcount=1;s->kind=(long)kind;s->length=n;s->capacity=n;s->items=n>0?piton_alloc((usize)n*sizeof(PitonSlot)):0;return s;}
static void piton_seq_put(PitonSeq*s,long i,PitonSlot v){if(i>=0&&i<s->length)s->items[i]=v;}
static long piton_str_repeat(const char*s,long n){if(n<=0){char*p=piton_alloc(1);p[0]=0;return(long)p;}usize sl=piton_strlen(s);char*p=piton_alloc(sl*(usize)n+1);for(long i=0;i<n;++i)piton_memcpy(p+i*sl,s,sl);p[sl*(usize)n]=0;return(long)p;}
static long piton_seq_concat(PitonSeq*a,PitonSeq*b){if(!a||!b||a->kind!=b->kind||((a->kind!=PK_LIST)&&(a->kind!=PK_TUPLE))){piton_write(2,"TypeError: cannot concatenate\n",30);piton_exit(1);}PitonSeq*s=piton_seq_new((int)a->kind,a->length+b->length);for(long i=0;i<a->length;++i)s->items[i]=a->items[i];for(long i=0;i<b->length;++i)s->items[a->length+i]=b->items[i];return(long)s;}
static long piton_str_contains(const char*h,const char*n){usize hl=piton_strlen(h),nl=piton_strlen(n);if(nl==0)return 1;if(nl>hl)return 0;for(usize i=0;i+nl<=hl;++i){usize j=0;while(j<nl&&h[i+j]==n[j])++j;if(j==nl)return 1;}return 0;}
static long piton_seq_contains(PitonSeq*s,PitonSlot v){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return 1;return 0;}
static long piton_dict_contains(PitonDict*d,PitonSlot v){if(!d)return 0;for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,v))return 1;return 0;}
static long piton_set_contains(PitonSet*s,PitonSlot v){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return 1;return 0;}
static void piton_seq_append(PitonSeq*s,PitonSlot v){if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*na=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(na,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=na;s->capacity=nc;}s->items[s->length++]=v;}
static int piton_ws(unsigned char c){return c==' '||c=='\t'||c=='\n'||c=='\r'||c=='\v'||c=='\f';}
static long piton_str_case(const char*s,int upper){usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c>=0x80){piton_write(2,"ValueError: str case conversion on non-ASCII text is not supported in the native subset\n",88);piton_exit(1);}p[i]=(char)(upper?((c>='a'&&c<='z')?c-32:c):((c>='A'&&c<='Z')?c+32:c));}p[n]=0;return(long)p;}
static long piton_str_find(const char*s,const char*n){usize hl=piton_strlen(s),nl=piton_strlen(n);if(nl==0)return 0;if(nl>hl)return -1;for(usize i=0;i+nl<=hl;++i){usize j=0;while(j<nl&&s[i+j]==n[j])++j;if(j==nl)return(long)i;}return -1;}
static long piton_str_startswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[i]!=p[i])return 0;return 1;}
static long piton_str_endswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[hl-nl+i]!=p[i])return 0;return 1;}
static long piton_str_replace(const char*s,const char*a,const char*b){usize sl=piton_strlen(s),al=piton_strlen(a),bl=piton_strlen(b);usize count=0;if(al==0){count=sl+1;}else{for(usize i=0;i+al<=sl;){usize k=0;while(k<al&&s[i+k]==a[k])++k;if(k==al){++count;i+=al;}else++i;}}usize total=sl+count*bl-(al==0?0:count*al);char*p=piton_alloc(total+1);usize o=0;if(al==0){for(usize i=0;i<sl;++i){piton_memcpy(p+o,b,bl);o+=bl;p[o++]=s[i];}piton_memcpy(p+o,b,bl);o+=bl;}else{for(usize i=0;i<sl;){usize k=0;while(k<al&&i+k<sl&&s[i+k]==a[k])++k;if(k==al){piton_memcpy(p+o,b,bl);o+=bl;i+=al;}else p[o++]=s[i++];}}p[o]=0;return(long)p;}
static long piton_str_from_int_base(long v,int base,int upper){char*p=piton_alloc(70);usize o=0;unsigned long u;if(v<0){p[o++]='-';u=(unsigned long)(-(v+1))+1;}else u=(unsigned long)v;const char*digits=upper?"0123456789ABCDEF":"0123456789abcdef";char tmp[64];long n=0;do{tmp[n++]=digits[u%(unsigned)base];u/=(unsigned)base;}while(u);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static usize piton_utf8_chars(const char*s,usize maxbytes){usize i=0;long cc=0;while(s[i]&&(usize)cc<maxbytes){unsigned char c=(unsigned char)s[i];usize adv=1;if(c>=0x80){if((c&0xE0)==0xC0)adv=2;else if((c&0xF0)==0xE0)adv=3;else if((c&0xF8)==0xF0)adv=4;}i+=adv;++cc;}return i;}
static long piton_str_pad(const char*s,long width,long prec,long flags,int isnum){int neg=0;const char*digits=s;if(isnum&&s[0]=='-'){neg=1;digits=s+1;}usize dl=piton_strlen(digits);const char*core=digits;usize cl=dl;if(prec>=0){if(isnum){usize need=(usize)prec;if(dl==1&&digits[0]=='0'&&prec==0)need=0;if(dl<need){char*pad=piton_alloc(need+1);for(usize i=0;i<need-dl;++i)pad[i]='0';piton_memcpy(pad+need-dl,digits,dl);pad[need]=0;core=pad;cl=need;}}else{usize cut=piton_utf8_chars(s,(usize)prec);char*tr=piton_alloc(cut+1);piton_memcpy(tr,s,cut);tr[cut]=0;core=tr;cl=cut;neg=0;}}usize totallen=cl+(neg?1:0);long pad=(width>0&&totallen<(usize)width)?(width-(long)totallen):0;int left=(flags&1)!=0;int zero=(flags&2)&&!left&&isnum&&prec<0;char*out=piton_alloc(totallen+(usize)pad+1);usize o=0;if(!left){if(zero){if(neg)out[o++]='-';for(long i=0;i<pad;++i)out[o++]='0';}else{for(long i=0;i<pad;++i)out[o++]=' ';if(neg)out[o++]='-';}}else if(neg){out[o++]='-';}piton_memcpy(out+o,core,cl);o+=cl;if(left){for(long i=0;i<pad;++i)out[o++]=' ';}out[o]=0;return(long)out;}
static long piton_str_quote(const char*s){usize n=piton_strlen(s);int q=0;for(usize i=0;i<n;++i)if(s[i]=='\'')q=1;char qc=q?'"':'\'';usize extra=0;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c=='\\'||c=='\n'||c=='\t'||c=='\r'||(unsigned char)c==qc)extra+=1;}char*p=piton_alloc(n+extra+3);usize o=0;p[o++]=qc;for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(c=='\\'){p[o++]='\\';p[o++]='\\';}else if(c=='\n'){p[o++]='\\';p[o++]='n';}else if(c=='\t'){p[o++]='\\';p[o++]='t';}else if(c=='\r'){p[o++]='\\';p[o++]='r';}else if(c==qc){p[o++]='\\';p[o++]=c;}else p[o++]=(char)c;}p[o++]=qc;p[o]=0;return(long)p;}
static long piton_str_single_char(const char*s){if(piton_strlen(s)!=1){piton_raise_set("TypeError","%c requires int or char");return 0;}return(long)s;}
static long piton_str_format(const char*t,long n,const char**av){usize total=0;int auto_idx=0;for(usize i=0;t[i];){if(t[i]=='{'){if(t[i+1]=='{'){total+=1;i+=2;continue;}usize j=i+1;int idx=-2;int has=0;if(t[j]=='}'){idx=auto_idx++;j+=1;has=1;}else{idx=0;while(t[j]>='0'&&t[j]<='9'){idx=idx*10+(t[j]-'0');j+=1;has=1;}if(has&&t[j]=='}'){j+=1;}else{has=0;}}if(!has){piton_write(2,"ValueError: single '{' in format string\n",40);piton_exit(1);}if(idx<0||idx>=n){piton_write(2,"IndexError: replacement index out of range\n",43);piton_exit(1);}total+=piton_strlen(av[idx]);i=j;}else if(t[i]=='}'){if(t[i+1]=='}'){total+=1;i+=2;}else{piton_write(2,"ValueError: single '}' in format string\n",40);piton_exit(1);}}else{total+=1;i+=1;}}char*p=piton_alloc(total+1);usize o=0;auto_idx=0;for(usize i=0;t[i];){if(t[i]=='{'){if(t[i+1]=='{'){p[o++]='{';i+=2;continue;}usize j=i+1;int idx=-2;if(t[j]=='}'){idx=auto_idx++;j+=1;}else{idx=0;while(t[j]>='0'&&t[j]<='9'){idx=idx*10+(t[j]-'0');j+=1;}j+=1;}usize el=piton_strlen(av[idx]);piton_memcpy(p+o,av[idx],el);o+=el;i=j;}else if(t[i]=='}'){p[o++]='}';i+=2;}else{p[o++]=t[i++];}}p[o]=0;return(long)p;}

static long piton_str_split_ws(const char*s){PitonSeq*r=piton_seq_new(PK_LIST,0);usize n=piton_strlen(s),i=0;while(i<n){while(i<n&&piton_ws((unsigned char)s[i]))++i;if(i>=n)break;usize j=i;while(j<n&&!piton_ws((unsigned char)s[j]))++j;usize len=j-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});i=j;}return(long)r;}
static long piton_str_split(const char*s,const char*sep){if(!sep)return piton_str_split_ws(s);PitonSeq*r=piton_seq_new(PK_LIST,0);usize sl=piton_strlen(s),nl=piton_strlen(sep);if(nl==0){for(usize k=0;k<=sl;++k){char*q=piton_alloc(2);q[0]=k<sl?s[k]:0;q[1]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});}return(long)r;}usize i=0;while(1){usize j=i;while(j+nl<=sl){usize k=0;while(k<nl&&s[j+k]==sep[k])++k;if(k==nl)break;++j;}usize len=j-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});if(j+nl>sl)break;i=j+nl;}return(long)r;}
static long piton_str_strip(const char*s,int mode){usize n=piton_strlen(s),a=0,b=n;if(mode!=2){while(a<n&&piton_ws((unsigned char)s[a]))++a;}if(mode!=1){while(b>a&&piton_ws((unsigned char)s[b-1]))--b;}char*p=piton_alloc(b-a+1);piton_memcpy(p,s+a,b-a);p[b-a]=0;return(long)p;}
static long piton_str_join(const char*sep,PitonSeq*items){usize sl=piton_strlen(sep);usize total=0;long cnt=items?items->length:0;for(long i=0;i<cnt;++i){if(items->items[i].kind!=PK_STR){piton_write(2,"TypeError: sequence item is not a string\n",41);piton_exit(1);}total+=piton_strlen((const char*)items->items[i].bits);}total+=sl*(usize)(cnt>0?cnt-1:0);char*p=piton_alloc(total+1);usize o=0;for(long i=0;i<cnt;++i){if(i){piton_memcpy(p+o,sep,sl);o+=sl;}usize el=piton_strlen((const char*)items->items[i].bits);piton_memcpy(p+o,(const char*)items->items[i].bits,el);o+=el;}p[o]=0;return(long)p;}
static long piton_str_index(const char*s,long i){long n=(long)piton_strlen(s);if(i<0)i+=n;if(i<0||i>=n){piton_write(2,"IndexError\n",11);piton_exit(1);}char*p=piton_alloc(2);p[0]=s[i];p[1]=0;return(long)p;}
static long piton_str_slice(const char*s,long lo,long hi){long n=(long)piton_strlen(s);if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;char*p=piton_alloc((usize)(hi-lo)+1);for(long i=0;i<hi-lo;++i)p[i]=s[lo+i];p[hi-lo]=0;return(long)p;}
static PitonSlot piton_seq_pop(PitonSeq*s,long i){if(!s||s->length<=0){piton_write(2,"IndexError: pop from empty list\n",32);piton_exit(1);}if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError: pop index out of range\n",35);piton_exit(1);}PitonSlot v=s->items[i];for(long j=i;j+1<s->length;++j)s->items[j]=s->items[j+1];--s->length;return v;}
static void piton_seq_reverse(PitonSeq*s){if(!s)return;for(long i=0,j=s->length-1;i<j;++i,--j){PitonSlot t=s->items[i];s->items[i]=s->items[j];s->items[j]=t;}}
static void piton_seq_insert(PitonSeq*s,long i,PitonSlot v){if(!s)return;if(i<0)i+=s->length;if(i<0)i=0;if(i>s->length)i=s->length;if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*na=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(na,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=na;s->capacity=nc;}for(long j=s->length;j>i;--j)s->items[j]=s->items[j-1];s->items[i]=v;++s->length;}
static long piton_seq_count(PitonSeq*s,PitonSlot v){if(!s)return 0;long n=0;for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))++n;return n;}
static void piton_seq_sort(PitonSeq*s){if(!s||s->length<2)return;int allint=1,allstr=1;for(long i=0;i<s->length;++i){if(s->items[i].kind!=PK_INT&&s->items[i].kind!=PK_BOOL)allint=0;if(s->items[i].kind!=PK_STR)allstr=0;}if(!allint&&!allstr){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}for(long i=1;i<s->length;++i){PitonSlot k=s->items[i];long j=i-1;if(allstr&&!allint){while(j>=0&&piton_strcmp((const char*)s->items[j].bits,(const char*)k.bits)>0){s->items[j+1]=s->items[j];--j;}}else{while(j>=0&&s->items[j].bits>k.bits){s->items[j+1]=s->items[j];--j;}}s->items[j+1]=k;}}
static PitonSlot piton_dict_get_1(PitonDict*d,PitonSlot k){if(d)for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k))return d->items[i].value;piton_write(2,"KeyError\n",9);piton_exit(1);}
static PitonSlot piton_dict_get_d(PitonDict*d,PitonSlot k,PitonSlot dflt){if(d)for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,k))return d->items[i].value;return dflt;}
static long piton_seq_slice_step(PitonSeq*s,long lo,long hi,long st){if(!s)return 0;if(st==0){piton_write(2,"ValueError: slice step cannot be zero\n",38);piton_exit(1);}long n=s->length;long len=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;len=(hi-lo+st-1)/st;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);}PitonSeq*r=piton_seq_new((int)s->kind,len);for(long i=0;i<len;++i)r->items[i]=s->items[lo+i*st];return(long)r;}
static long piton_str_slice_step(const char*s,long lo,long hi,long st){if(st==0){piton_write(2,"ValueError: slice step cannot be zero\n",38);piton_exit(1);}long n=(long)piton_strlen(s);long len=0;long a=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;len=(hi-lo+st-1)/st;a=lo;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;if(lo<=hi)len=0;else len=(lo-hi-st-1)/(-st);a=lo;}char*p=piton_alloc((usize)len+1);for(long i=0;i<len;++i)p[i]=s[a+i*st];p[len]=0;return(long)p;}
static long piton_seq_slice(PitonSeq*s,long lo,long hi){if(!s)return 0;long n=s->length;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;PitonSeq*r=piton_seq_new((int)s->kind,hi-lo);for(long i=0;i<hi-lo;++i)r->items[i]=s->items[lo+i];return(long)r;}
static PitonSlot piton_seq_get(PitonSeq*s,long i){if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError\n",11);piton_exit(1);}return s->items[i];}
static long piton_iterator_new(PitonSeq*s){if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->index=0;return(long)i;}
static long piton_iterator_next(long raw){PitonIterator*i=(PitonIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->seq->length){piton_write(2,"StopIteration\n",14);piton_exit(1);}return i->seq->items[i->index++].bits;}
static long piton_genexpr_new(PitonSeq*s){if(!s){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}PitonGenExpr*g=piton_alloc(sizeof(*g));g->source=s;g->index=0;return(long)g;}
static long piton_genexpr_iter(PitonGenExpr*g){return(long)g;}
static long piton_genexpr_next(PitonGenExpr*g){if(!g||!g->source){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}if(g->index>=g->source->length){piton_raise_set("StopIteration","");return 0;}return g->source->items[g->index++].bits;}
static PitonDict*piton_dict_new(long n){PitonDict*d=piton_alloc(sizeof(*d));d->refcount=1;d->kind=PK_DICT;d->length=n;d->capacity=n;d->items=n>0?piton_alloc((usize)n*sizeof(PitonDictEntry)):0;return d;}
static void piton_dict_put(PitonDict*d,long i,PitonSlot k,PitonSlot v){if(i>=0&&i<d->length){d->items[i].key=k;d->items[i].value=v;}}
static void piton_dict_append(PitonDict*d,PitonSlot k,PitonSlot v){if(d->length>=d->capacity){long nc=d->capacity?d->capacity*2:4;PitonDictEntry*ni=piton_alloc((usize)nc*sizeof(PitonDictEntry));if(d->items)piton_memcpy(ni,d->items,(usize)d->capacity*sizeof(PitonDictEntry));d->items=ni;d->capacity=nc;}d->items[d->length].key=k;d->items[d->length].value=v;++d->length;}
static PitonSlot piton_dict_get(PitonDict*d,PitonSlot key){for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,key))return d->items[i].value;piton_write(2,"KeyError\n",9);piton_exit(1);}
static int piton_unpack_seq4(PitonSlot obj,long capacity,long*out4){if(obj.kind!=PK_LIST&&obj.kind!=PK_TUPLE){piton_raise_set("TypeError","argument after * must be a list or tuple");return -1;}PitonSeq*s=(PitonSeq*)obj.bits;if(s->length>capacity){piton_raise_set("TypeError","too many positional arguments for call");return -1;}for(long i=0;i<s->length;++i)out4[i]=s->items[i].bits;return s->length;}
static int piton_dict_unpack4(PitonSlot obj,const char**names,long count,long*out4,long*mask){if(obj.kind!=PK_DICT){piton_raise_set("TypeError","argument after ** must be a dict");return -1;}PitonDict*d=(PitonDict*)obj.bits;for(long i=0;i<d->length;++i){PitonSlot k=d->items[i].key;if(k.kind!=PK_STR){piton_raise_set("TypeError","keywords must be strings");return -1;}const char*key=(const char*)k.bits;long matched=-1;for(long j=0;j<count;++j)if(piton_strcmp(key,names[j])==0){matched=j;break;}if(matched<0){piton_raise_set("TypeError","unexpected keyword argument in ** expansion");return -1;}if(*mask&(1LL<<matched)){piton_raise_set("TypeError","multiple values for argument");return -1;}*mask|=1LL<<matched;out4[matched]=d->items[i].value.bits;}return 0;}
static PitonSet*piton_set_new(long cap){PitonSet*s=piton_alloc(sizeof(*s));s->refcount=1;s->kind=PK_SET;s->capacity=cap;s->items=cap>0?piton_alloc((usize)cap*sizeof(PitonSlot)):0;return s;}
static void piton_set_add(PitonSet*s,PitonSlot v){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return;if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*ni=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(ni,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=ni;s->capacity=nc;}s->items[s->length++]=v;}
typedef struct{long magic;long kind;void*raw;long index;}PitonAnyIterator;
static long piton_iterator_new_any(void*raw,long kind){if(!raw||(kind!=PK_LIST&&kind!=PK_TUPLE&&kind!=PK_DICT&&kind!=PK_SET)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonAnyIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->raw=raw;i->index=0;i->kind=kind;return(long)i;}
static long piton_iterator_next_any(long raw){PitonAnyIterator*i=(PitonAnyIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}long n=0;if(i->kind==PK_LIST||i->kind==PK_TUPLE)n=((PitonSeq*)i->raw)->length;else if(i->kind==PK_DICT)n=((PitonDict*)i->raw)->length;else if(i->kind==PK_SET)n=((PitonSet*)i->raw)->length;else{piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}if(i->index>=n){piton_raise_set("StopIteration","");return 0;}PitonSlot v;if(i->kind==PK_LIST||i->kind==PK_TUPLE)v=((PitonSeq*)i->raw)->items[i->index++];else if(i->kind==PK_DICT)v=((PitonDict*)i->raw)->items[i->index++].key;else v=((PitonSet*)i->raw)->items[i->index++];return v.bits;}
typedef struct{long magic;const char*str;long index;long length;}PitonStrIterator;
static long piton_str_iterator_new(const char*str){if(!str){piton_write(2,"TypeError: 'NoneType' object is not iterable\n",42);piton_exit(1);}PitonStrIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E53545249LL;i->str=str;i->index=0;i->length=piton_strlen(str);return(long)i;}
static long piton_str_iterator_next(long raw){PitonStrIterator*i=(PitonStrIterator*)raw;if(!i||i->magic!=0x5049544E53545249LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->length){piton_raise_set("StopIteration","");return 0;}unsigned char c=i->str[i->index++];char*p=piton_alloc(2);p[0]=(char)c;p[1]=0;return(long)p;}
typedef struct{long magic;long index;long start;PitonSeq*seq;}PitonEnumerateIterator;
static long piton_enumerate_new(void*raw,long start){PitonSeq*s=(PitonSeq*)raw;if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: enumerate() argument is not iterable\n",48);piton_exit(1);}PitonEnumerateIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->start=start;return(long)i;}
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
static long piton_sorted_new(void*raw){PitonSeq*s=(PitonSeq*)raw;if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: sorted() argument is not iterable\n",46);piton_exit(1);}PitonSeq*r=piton_seq_new(PK_LIST,s->length);for(long i=0;i<s->length;++i)r->items[i]=s->items[i];for(long i=1;i<r->length;++i){PitonSlot v=r->items[i];long j=i;while(j>0&&r->items[j-1].bits>v.bits){r->items[j]=r->items[j-1];--j;}r->items[j]=v;}return(long)r;}
#define PITON_GEN_MAGIC 0x5049544E47654ELL
#define PITON_GEN_MAX_SLOTS 64
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
static PitonSlot piton_object_get(PitonObject*o,const char*name){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value;piton_write(2,"AttributeError\n",15);piton_exit(1);}
static long piton_object_lookup(PitonObject*o,const char*name,long fallback){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value.bits;return ((long(*)(long,long))fallback)((long)o,(long)name);}
#define PITON_CLOSURE_MAGIC 0x5049544EC10557LL
#define PITON_BOUND_METHOD_MAGIC 0x5049544E424D4554LL
typedef struct{long magic;long addr;long n_args;long n_cells;long*cells;long has_vararg;}PitonClosure;
typedef struct{long magic;long addr;long self;long n_args;}PitonBoundMethod;
static long piton_bound_method_new(long addr,long n_args,long self){PitonBoundMethod*m=piton_alloc(sizeof(*m));m->magic=PITON_BOUND_METHOD_MAGIC;m->addr=addr;m->self=self;m->n_args=n_args;return(long)m;}
static long piton_bound_method_self(long raw){PitonBoundMethod*m=(PitonBoundMethod*)raw;if(!m||m->magic!=PITON_BOUND_METHOD_MAGIC){piton_write(2,"AttributeError: bound method has no __self__\n",45);piton_exit(1);}return m->self;}
static long piton_closure_new8(long addr,long n_args,long n_cells,long c0,long c1,long c2,long c3){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;c->has_vararg=0;c->cells=piton_alloc((usize)n_cells*sizeof(long));long cs[4]={c0,c1,c2,c3};for(long i=0;i<n_cells&&i<4;++i)c->cells[i]=cs[i];return(long)c;}
static long piton_closure_call6(long callee,long argc,long a0,long a1,long a2,long a3){if(!callee||((long*)callee)[0]!=PITON_CLOSURE_MAGIC)return((long(*)(long,long,long,long))callee)(a0,a1,a2,a3);PitonClosure*c=(PitonClosure*)callee;if(argc!=c->n_args){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long total=c->n_cells+argc;if(total>4){piton_write(2,"TypeError: closure cell count plus arguments exceeds four\n",59);piton_exit(2);}long x[4]={a0,a1,a2,a3};for(long i=0;i<c->n_cells&&i<4;++i){for(long j=3;j>i;--j)x[j]=x[j-1];x[i]=c->cells[i];}return((long(*)(long,long,long,long))c->addr)(x[0],x[1],x[2],x[3]);}
static long piton_closure_new_frame(long addr,long n_args,long n_cells,long*cells,long has_vararg){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;c->has_vararg=has_vararg;c->cells=piton_alloc((usize)(n_cells?n_cells:1)*sizeof(long));for(long i=0;i<n_cells;++i)c->cells[i]=cells[i];return(long)c;}
static long piton_closure_call_frame(long callee,long argc,long*args){if(callee&&((long*)callee)[0]==PITON_BOUND_METHOD_MAGIC){PitonBoundMethod*m=(PitonBoundMethod*)callee;if(argc!=m->n_args||argc>3){piton_write(2,"TypeError: bound method called with wrong number of arguments\n",61);piton_exit(2);}long a[4]={m->self,0,0,0};for(long i=0;i<argc;++i)a[i+1]=args[i];return((long(*)(long,long,long,long))m->addr)(a[0],a[1],a[2],a[3]);}PitonClosure*c=(PitonClosure*)callee;if(!c||c->magic!=PITON_CLOSURE_MAGIC){if(argc>4){piton_write(2,"TypeError: native call exceeds four direct arguments\n",52);piton_exit(2);}long a[4]={0,0,0,0};for(long i=0;i<argc;++i)a[i]=args[i];return((long(*)(long,long,long,long))callee)(a[0],a[1],a[2],a[3]);}if(argc<c->n_args||(!c->has_vararg&&argc!=c->n_args)){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long extra=c->has_vararg&&argc>c->n_args?argc-c->n_args:0;long total=c->n_cells+c->n_args+(c->has_vararg?1:0);long*frame=piton_alloc((usize)total*sizeof(long));for(long i=0;i<c->n_cells;++i)frame[i]=c->cells[i];for(long i=0;i<c->n_args;++i)frame[c->n_cells+i]=args[i];if(c->has_vararg){PitonSeq*t=piton_seq_new(PK_TUPLE,extra);for(long i=0;i<extra;++i)t->items[i]=(PitonSlot){args[c->n_args+i],PK_INT};frame[c->n_cells+c->n_args]=(long)t;}return((long(*)(long*))c->addr)(frame);}
static long piton_callback_invoke(long callback,long value){long args[1]={value};return piton_closure_call_frame(callback,1,args);}
static long piton_frame_call(long addr,long argc,long*args){long*frame=piton_alloc((usize)argc*sizeof(long));for(long i=0;i<argc;++i)frame[i]=args[i];return((long(*)(long*))addr)(frame);}
static void piton_print_slot(PitonSlot v);
static void piton_print_seq(PitonSeq*s){piton_write(1,s->kind==PK_TUPLE?"(":"[",1);for(long i=0;i<s->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(s->items[i]);}if(s->kind==PK_TUPLE&&s->length==1)piton_write(1,",",1);piton_write(1,s->kind==PK_TUPLE?")":"]",1);}
static void piton_print_dict(PitonDict*d){piton_write(1,"{",1);for(long i=0;i<d->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(d->items[i].key);piton_write(1,": ",2);piton_print_slot(d->items[i].value);}piton_write(1,"}",1);}
static void piton_print_set(PitonSet*s){piton_write(1,"{",1);for(long i=0;i<s->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(s->items[i]);}piton_write(1,"}",1);}
static void piton_print_slot(PitonSlot v){switch(v.kind){case PK_NONE:piton_write(1,"None",4);break;case PK_BOOL:piton_write(1,v.bits?"True":"False",v.bits?4:5);break;case PK_INT:piton_write_int(v.bits);break;case PK_FLOAT:{char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)v.bits);piton_write(1,r,(usize)n);break;}case PK_STR:piton_write(1,"'",1);piton_write(1,(const char*)v.bits,piton_strlen((const char*)v.bits));piton_write(1,"'",1);break;case PK_LIST:case PK_TUPLE:piton_print_seq((PitonSeq*)v.bits);break;case PK_DICT:piton_print_dict((PitonDict*)v.bits);break;case PK_SET:piton_print_set((PitonSet*)v.bits);break;default:piton_write(1,"<object>",8);}}
static long piton_sum_seq(PitonSeq*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static long piton_sum_dict(PitonDict*d){long r=0;for(long i=0;i<d->length;++i)r+=d->items[i].key.bits;return r;}
static long piton_sum_set(PitonSet*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static const char*piton_type_repr(int kind){switch(kind){case PK_NONE:return"<class 'NoneType'>";case PK_BOOL:return"<class 'bool'>";case PK_INT:return"<class 'int'>";case PK_FLOAT:return"<class 'float'>";case PK_STR:return"<class 'str'>";case PK_LIST:return"<class 'list'>";case PK_TUPLE:return"<class 'tuple'>";case PK_DICT:return"<class 'dict'>";case PK_SET:return"<class 'set'>";default:return"<class 'object'>";}}
/* M14 BUILTINS_CORE_V2 (matriz declarada; ver native_runtime.c) */
static int piton_slot_truthy(PitonSlot v){switch(v.kind){case PK_NONE:return 0;case PK_BOOL:return v.bits?1:0;case PK_INT:return v.bits!=0;case PK_FLOAT:return piton_bits_double(v.bits)!=0.0;case PK_STR:return v.bits&&((const char*)v.bits)[0]!=0;case PK_LIST:case PK_TUPLE:return ((PitonSeq*)v.bits)->length>0;default:return v.bits!=0;}}
static long piton_all_seq(PitonSeq*s){if(!s)return 1;for(long i=0;i<s->length;++i)if(!piton_slot_truthy(s->items[i]))return 0;return 1;}
static long piton_any_seq(PitonSeq*s){if(!s)return 0;for(long i=0;i<s->length;++i)if(piton_slot_truthy(s->items[i]))return 1;return 0;}
static long piton_pow_int(long b,long e){if(e<0){piton_raise_set("TypeError","pow() negative exponent unsupported (M14 v1)");return 0;}long acc=1;while(e>0){if(e&1)acc*=b;b*=b;e>>=1;}return acc;}
static long piton_pow_float(long bb,long e){double b=piton_bits_double(bb);long neg=e<0;if(neg)e=-e;double acc=1.0;while(e>0){if(e&1)acc*=b;b*=b;e>>=1;}if(neg)acc=1.0/acc;return piton_double_bits(acc);}
static long piton_ord(const char*s){if(!s||!s[0]){piton_raise_set("TypeError","ord() expected a character, but string of length 0 found");return 0;}const unsigned char*u=(const unsigned char*)s;long cp;long n;if(u[0]<0x80){cp=u[0];n=1;}else if((u[0]&0xE0)==0xC0){cp=u[0]&0x1F;n=2;}else if((u[0]&0xF0)==0xE0){cp=u[0]&0x0F;n=3;}else if((u[0]&0xF8)==0xF0){cp=u[0]&0x07;n=4;}else{piton_raise_set("TypeError","ord() received invalid UTF-8");return 0;}for(long i=1;i<n;++i)cp=(cp<<6)|(u[i]&0x3F);if(s[n]){piton_raise_set("TypeError","ord() expected a character, but string of length >1 found");return 0;}return cp;}
static long piton_chr(long cp);
static long piton_percent_chr(long cp){if(cp<0||cp>0x10FFFF){piton_raise_set("OverflowError","%c arg not in range(0x110000)");return 0;}return piton_chr(cp);}
static long piton_chr(long cp){if(cp<0||cp>0x10FFFF){piton_raise_set("ValueError","chr() arg not in range(0x110000)");return 0;}char*p=piton_alloc(5);if(cp<0x80){p[0]=(char)cp;p[1]=0;}else if(cp<0x800){p[0]=(char)(0xC0|(cp>>6));p[1]=(char)(0x80|(cp&0x3F));p[2]=0;}else if(cp<0x10000){p[0]=(char)(0xE0|(cp>>12));p[1]=(char)(0x80|((cp>>6)&0x3F));p[2]=(char)(0x80|(cp&0x3F));p[3]=0;}else{p[0]=(char)(0xF0|(cp>>18));p[1]=(char)(0x80|((cp>>12)&0x3F));p[2]=(char)(0x80|((cp>>6)&0x3F));p[3]=(char)(0x80|(cp&0x3F));p[4]=0;}return(long)p;}
static long piton_bin(long v){char*p=piton_alloc(70);usize o=0;unsigned long m;if(v<0){p[o++]='-';m=(unsigned long)(-(v+1))+1;}else m=(unsigned long)v;p[o++]='0';p[o++]='b';char tmp[64];long n=0;do{tmp[n++]=(char)('0'+(m&1));m>>=1;}while(m);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static long piton_round_float(long bits){double x=piton_bits_double(bits);double ax=x<0?-x:x;if(ax>=9.0e18){piton_raise_set("OverflowError","round() float too large to convert to int");return 0;}long t=(long)ax;double frac=ax-(double)t;long r;if(frac>0.5)r=t+1;else if(frac<0.5)r=t;else r=(t&1)?t+1:t;return x<0?-r:r;}
/* M14 TYPE_CONVERSION_V1 + MATH_TIER1_V1 (matriz: ver native_runtime.c) */
static long piton_int_from_str(const char*s){if(!s){piton_raise_set("TypeError","int() argument must be a string");return 0;}while(*s==' '||*s=='\t'||*s=='\n')++s;int neg=0;if(*s=='-'||*s=='+'){neg=*s=='-';++s;}if(!*s||*s<'0'||*s>'9'){piton_raise_set("ValueError","invalid literal for int() with base 10");return 0;}long v=0;while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');++s;}while(*s==' '||*s=='\t'||*s=='\n')++s;if(*s){piton_raise_set("ValueError","invalid literal for int() with base 10");return 0;}return neg?-v:v;}
static long piton_float_from_str(const char*s){if(!s){piton_raise_set("TypeError","float() argument must be a string");return 0;}while(*s==' '||*s=='\t'||*s=='\n')++s;int neg=0;if(*s=='-'||*s=='+'){neg=*s=='-';++s;}const char*q=s;long ip=0;int has=0;while(*q>='0'&&*q<='9'){ip=ip*10+(*q-'0');++q;has=1;}double frac=0.0;double div=1.0;if(*q=='.'){++q;while(*q>='0'&&*q<='9'){frac=frac*10+(*q-'0');div*=10;++q;has=1;}}if(!has){piton_raise_set("ValueError","could not convert string to float");return 0;}while(*q==' '||*q=='\t'||*q=='\n')++q;if(*q){piton_raise_set("ValueError","could not convert string to float");return 0;}double v=(double)ip+frac/div;if(neg)v=-v;return piton_double_bits(v);}
static long piton_str_from_int(long v){char*p=piton_alloc(24);usize o=0;unsigned long u;if(v<0){p[o++]='-';u=(unsigned long)(-(v+1))+1;}else u=(unsigned long)v;char tmp[24];long n=0;do{tmp[n++]=(char)('0'+u%10);u/=10;}while(u);while(n)p[o++]=tmp[--n];p[o]=0;return(long)p;}
static long piton_str_from_float(long bits){char*p=piton_alloc(PITON_REPR_MAX+1);int n=piton_repr_double(p,(unsigned long long)bits);p[n]=0;return(long)p;}
static long piton_str_truthy(const char*s){return(s&&s[0])?1:0;}
static long piton_math_floor_bits(long bits){double d=piton_bits_double(bits);long t=(long)d;if((double)t>d)--t;return t;}
static long piton_math_ceil_bits(long bits){double d=piton_bits_double(bits);long t=(long)d;if((double)t<d)++t;return t;}
static long piton_math_trunc_bits(long bits){double d=piton_bits_double(bits);return (long)d;}
static long piton_math_fabs_bits(long bits){return bits&0x7FFFFFFFFFFFFFFFL;}
static long piton_math_gcd(long a,long b){if(a<0)a=-a;if(b<0)b=-b;while(b){long t=a%b;a=b;b=t;}return a;}
static int piton_exc_flag=0;static const char*piton_exc_type=0;static const char*piton_exc_message=0;
static const char*piton_exc_cause_type=0;static const char*piton_exc_cause_msg=0;
static void piton_raise_set(const char*type,const char*message){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=message;}
static long piton_int_add(long a,long b){i64 r;if(__builtin_add_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_sub(long a,long b){i64 r;if(__builtin_sub_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_mul(long a,long b){i64 r;if(__builtin_mul_overflow(a,b,&r)){piton_raise_set("OverflowError","integer arithmetic result too large for the native int subset");}return r;}
static long piton_int_truediv(long a,long b){if(b==0){piton_raise_set("ZeroDivisionError","division by zero");return 0;}return piton_double_bits((double)a/(double)b);}
static long piton_float_div(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float division by zero");return 0;}return piton_double_bits(x/y);}
static long piton_float_floor_div(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float floor division by zero");return 0;}return piton_double_bits(__builtin_floor(x/y));}
static void piton_raise_chain_set(const char*type,const char*message,const char*cause_type,const char*cause_msg){piton_exc_cause_type=cause_type;piton_exc_cause_msg=cause_msg;piton_raise_set(type,message);}
static const char*piton_reraise_type=0;static const char*piton_reraise_message=0;
static void piton_reraise_save(void){piton_reraise_type=piton_exc_type;piton_reraise_message=piton_exc_message;}
static void piton_reraise_set(const char*type){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=piton_reraise_message;}
static void piton_catch_clear(void){piton_exc_flag=0;piton_exc_type=0;piton_exc_message=0;piton_exc_cause_type=0;piton_exc_cause_msg=0;}
static void piton_report_unhandled(void){if(piton_exc_cause_type){piton_write(2,piton_exc_cause_type,piton_strlen(piton_exc_cause_type));if(piton_exc_cause_msg&&piton_exc_cause_msg[0]){piton_write(2,": ",2);piton_write(2,piton_exc_cause_msg,piton_strlen(piton_exc_cause_msg));}piton_write(2," -> causada por\n",16);}piton_write(2,piton_exc_type,piton_strlen(piton_exc_type));piton_write(2,": ",2);if(piton_exc_message)piton_write(2,piton_exc_message,piton_strlen(piton_exc_message));piton_write(2,"\n",1);}
"""

_BIGINT_FREESTANDING_C = r"""
static unsigned long piton_bump_buf[1024*1024];
static unsigned long*piton_bump_ptr=piton_bump_buf;
static void*piton_bump_alloc(unsigned long n){void*r=(void*)piton_bump_ptr;piton_bump_ptr+=n;return r;}
typedef struct{int sign;long count;unsigned long capacity;unsigned long*limbs;}PitonBigInt;
static long bi_bit_width(unsigned long v){long w=0;while(v){v>>=1;++w;}return w;}
static void*bi_from_u64(unsigned long v);
static int bi_cmp_mag(PitonBigInt*a,PitonBigInt*b){long aw=0,bw=0;for(long i=a->count-1;i>=0;--i){long w=bi_bit_width(a->limbs[i]);if(!w)w=1;aw=i*64+w;if(aw<0)aw=0;}for(long i=b->count-1;i>=0;--i){long w=bi_bit_width(b->limbs[i]);if(!w)w=1;bw=i*64+w;if(bw<0)bw=0;}if(aw!=bw)return aw>bw?1:-1;for(long i=a->count-1;i>=0;--i){if(a->limbs[i]!=b->limbs[i])return a->limbs[i]>b->limbs[i]?1:-1;}return 0;}
static void bi_ensure(PitonBigInt*r,long n){if(n<=0)n=1;if((unsigned long)n<=r->capacity)return;unsigned long c=r->capacity?r->capacity*2:2;while(c<(unsigned long)n)c*=2;unsigned long*p=(unsigned long*)piton_bump_alloc(c*sizeof(unsigned long));for(unsigned long i=0;i<r->count;++i)p[i]=r->limbs[i];for(unsigned long i=r->count;i<c;++i)p[i]=0;r->limbs=p;r->capacity=c;}
static void bi_trim(PitonBigInt*r){while(r->count>0&&r->limbs[r->count-1]==0)--r->count;if(r->count==0){r->count=1;r->sign=0;}}
static void bi_add_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){long max_c=a->count>b->count?a->count:b->count;bi_ensure(r,max_c+1);u128 carry=0;for(long i=0;i<=max_c;++i){if(i<a->count)carry+=a->limbs[i];if(i<b->count)carry+=b->limbs[i];r->limbs[i]=(unsigned long)carry;carry>>=64;r->count=i+1;}bi_trim(r);}
static void bi_sub_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){long max_c=a->count>b->count?a->count:b->count;bi_ensure(r,max_c);u128 borrow=0;for(long i=0;i<max_c;++i){u128 av=i<a->count?a->limbs[i]:0;u128 bv=i<b->count?b->limbs[i]:0;u128 diff=av-bv-borrow;r->limbs[i]=(unsigned long)diff;borrow=(diff>>127)&1;r->count=i+1;}bi_trim(r);}
static void*bi_from_u64(unsigned long v){PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;if(v){bi_ensure(r,1);r->limbs[0]=v;r->count=1;}return r;}
static void*piton_bigint_from_i64(long v){if(v==0)return bi_from_u64(0);int neg=v<0?-1:1;unsigned long av=(unsigned long)(v<0?-v:v);void*r=bi_from_u64(av);((PitonBigInt*)r)->sign=neg;return r;}
static void*piton_bigint_from_str(const char*s){PitonBigInt*a=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));a->sign=1;a->count=0;a->capacity=0;a->limbs=0;while(*s==' '||*s=='\t')++s;if(*s=='-'){a->sign=-1;++s;}else if(*s=='+'){++s;}while(*s>='0'&&*s<='9'){int digit=*s++-'0';u128 carry=0;for(long i=0;i<a->count;++i){carry+=(u128)a->limbs[i]*10;a->limbs[i]=(unsigned long)carry;carry>>=64;}if(carry){bi_ensure(a,a->count+1);a->limbs[a->count++]=(unsigned long)carry;}carry=digit;for(long i=0;i<a->count&&carry;++i){carry+=a->limbs[i];a->limbs[i]=(unsigned long)carry;carry>>=64;}if(carry){bi_ensure(a,a->count+1);a->limbs[a->count++]=(unsigned long)carry;}}bi_trim(a);return a;}
static int piton_bigint_is_zero(void*a){PitonBigInt*x=(PitonBigInt*)a;if(!x)return 1;for(long i=0;i<x->count;++i)if(x->limbs[i])return 0;return 1;}
static void piton_bigint_print_raw(void*a){if(piton_bigint_is_zero(a)){piton_write(1,"0",1);return;}PitonBigInt*x=(PitonBigInt*)a;char buf[64];int pos=sizeof(buf);buf[--pos]=0;unsigned long tmp[64];int tc=0;for(long i=0;i<x->count;++i)tmp[i]=x->limbs[i];tc=(int)x->count;while(tc>0){unsigned long carry=0;for(int i=tc-1;i>=0;--i){unsigned long cur=(carry<<32)|(tmp[i]>>32);unsigned long q1=cur/10;unsigned long r1=cur-q1*10;unsigned long mid=(r1<<32)|(tmp[i]&0xFFFFFFFF);unsigned long q2=mid/10;unsigned long r2=mid-q2*10;tmp[i]=(q1<<32)|q2;carry=r2;}buf[--pos]=(char)('0'+carry);while(tc>0&&tmp[tc-1]==0)--tc;}if(x->sign<0)buf[--pos]='-';piton_write(1,buf+pos,piton_strlen(buf+pos));}
static void piton_bigint_print(void*a){piton_bigint_print_raw(a);piton_write(1,"\n",1);}
static void*piton_bigint_add(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;if(x->sign==y->sign){r->sign=x->sign;bi_add_mag(r,x,y);}else{int c=bi_cmp_mag(x,y);if(c==0)return r;if(c>0){r->sign=x->sign;bi_sub_mag(r,x,y);}else{r->sign=y->sign;bi_sub_mag(r,y,x);}}bi_trim(r);return r;}
static void*piton_bigint_negate(void*a){PitonBigInt*x=(PitonBigInt*)a;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=-x->sign;r->count=x->count;r->capacity=x->capacity;r->limbs=x->limbs;return r;}
static void*piton_bigint_sub(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*negy=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));negy->sign=-y->sign;negy->count=y->count;negy->capacity=y->capacity;negy->limbs=y->limbs;return piton_bigint_add(x,negy);}
static void bi_mul_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){if(a->count==0||b->count==0){r->count=0;return;}long max_c=a->count+b->count;bi_ensure(r,max_c);for(unsigned long i=0;i<r->capacity;++i)r->limbs[i]=0;for(long i=0;i<a->count;++i){u128 carry=0;for(long j=0;j<b->count||carry;++j){u128 cur=r->limbs[i+j]+(u128)a->limbs[i]*(j<b->count?b->limbs[j]:0)+carry;r->limbs[i+j]=(unsigned long)cur;carry=cur>>64;}r->count=i+b->count+1;}bi_trim(r);}
static void*piton_bigint_mul(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=x->sign^y->sign;r->count=0;r->capacity=0;r->limbs=0;bi_mul_mag(r,x,y);return r;}
static void*piton_bigint_pow_small(long base,long exp){void*acc=piton_bigint_from_i64(1);void*b=piton_bigint_from_i64(base);while(exp>0){if(exp&1)acc=piton_bigint_mul(acc,b);exp>>=1;if(exp)b=piton_bigint_mul(b,b);}return acc;}
static long bi_cmp_magnitude(PitonBigInt*a,PitonBigInt*b){long ac=a->count,bc=b->count;while(ac>0&&a->limbs[ac-1]==0)ac--;while(bc>0&&b->limbs[bc-1]==0)bc--;if(ac!=bc)return ac>bc?1:-1;for(long i=ac-1;i>=0;--i){if(a->limbs[i]!=b->limbs[i])return a->limbs[i]>b->limbs[i]?1:-1;}return 0;}
static void bi_div_mod_internal(PitonBigInt*quot,PitonBigInt*rem,PitonBigInt*dividend,PitonBigInt*divisor){long dc=dividend->count;long dvc=divisor->count;bi_ensure(quot,dc);for(long i=0;i<dc;++i)quot->limbs[i]=0;quot->count=dc;bi_ensure(rem,dvc);for(long i=0;i<dvc;++i)rem->limbs[i]=0;rem->count=0;for(long i=dc-1;i>=0;--i){for(int b=63;b>=0;--b){rem->count=(i+1>rem->count)?i+1:rem->count;for(long j=rem->count-1;j>0;--j)rem->limbs[j]=((rem->limbs[j]<<1)|((rem->limbs[j-1]>>63)&1));rem->limbs[0]=(rem->limbs[0]<<1)|((dividend->limbs[i]>>b)&1);if(bi_cmp_magnitude(rem,divisor)>=0){bi_sub_mag(rem,rem,divisor);quot->limbs[i]|=(1UL<<b);}}}bi_trim(quot);bi_trim(rem);}
static void*piton_bigint_floor_div(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);q->sign=x->sign^y->sign;bi_trim(q);return q;}
static void*piton_bigint_mod(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);if(r->count!=0&&r->sign!=0){PitonBigInt*ys=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));ys->sign=y->sign^1;ys->count=y->count;ys->capacity=y->capacity;ys->limbs=y->limbs;r= piton_bigint_add(r,ys);}bi_trim(r);return r;}
static void*bi_shl_u64(unsigned long v,long k){PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=1;r->count=0;r->capacity=0;r->limbs=0;if(!v||k<0)return r;long word=k/64;int bits=(int)(k%64);if(bits==0){bi_ensure(r,word+1);r->limbs[word]=v;r->count=word+1;}else{bi_ensure(r,word+2);r->limbs[word]=(v<<bits);r->limbs[word+1]=(v>>(64-bits));r->count=word+2;}bi_trim(r);return r;}
static int piton_bi_bit(void*a,long p){PitonBigInt*x=(PitonBigInt*)a;if(p<0)return 0;long i=p/64;int b=(int)(p%64);if(i>=x->count)return 0;return(int)((x->limbs[i]>>b)&1UL);}
static double piton_bigint_scaled_to_double(void*a,long E,int xsign){PitonBigInt*x=(PitonBigInt*)a;long p=-1;for(long i=x->count-1;i>=0;--i){unsigned long w=x->limbs[i];if(w){p=i*64+63-__builtin_clzll(w);break;}}unsigned long long signbit=((unsigned long long)(xsign?1:0))<<63;if(p<0){double d;__builtin_memcpy(&d,&signbit,8);return d;}unsigned long long t=0;int sticky=0;for(long i=0;i<55;++i){long pos=p-i;t=(t<<1)|(unsigned long long)(pos>=0?piton_bi_bit(a,pos):0);}long lo=p-54;if(lo>0){for(long i=0;i<lo;++i)if(piton_bi_bit(a,i)){sticky=1;break;}}int e2=(int)(p+E);int guard=(int)((t>>1)&1ULL),rnd=(int)(t&1ULL);t>>=2;if(guard&&(rnd||sticky||(t&1ULL))){t+=1;if(t>=(1ULL<<53)){t>>=1;e2+=1;}}if(e2>=-1022){if(e2>1023){unsigned long long b=signbit|0x7FF0000000000000ULL;double d;__builtin_memcpy(&d,&b,8);return d;}unsigned long long b=signbit|((unsigned long long)(e2+1023)<<52)|(t&0xFFFFFFFFFFFFFULL);double d;__builtin_memcpy(&d,&b,8);return d;}int s=-1022-e2;if(s>=64){double d;__builtin_memcpy(&d,&signbit,8);return d;}unsigned long long dropped=t&((s==64)?~0ULL:((1ULL<<s)-1));int g2=(int)((t>>(s-1))&1ULL);int r2=(s>1)&&((dropped&(((1ULL<<(s-1))-1)))!=0);t>>=s;if(g2&&(r2||sticky||(t&1ULL)))t+=1;if(t>=(1ULL<<52)){unsigned long long b=signbit|(1ULL<<52);double d;__builtin_memcpy(&d,&b,8);return d;}unsigned long long b=signbit|t;double d;__builtin_memcpy(&d,&b,8);return d;}
static double piton_fmod_core(double x,double y){unsigned long long ux,uy;__builtin_memcpy(&ux,&x,8);__builtin_memcpy(&uy,&y,8);int ex=(int)((ux>>52)&0x7FFULL),ey=(int)((uy>>52)&0x7FFULL);unsigned long long mx=ux&0xFFFFFFFFFFFFFULL,my=uy&0xFFFFFFFFFFFFFULL;int sx=(int)(ux>>63);if(ex==0x7FF||(ey==0x7FF&&my!=0)||(uy&0x7FFFFFFFFFFFFFFFULL)==0)return(x*y)/(x*y);if((ux&0x7FFFFFFFFFFFFFFFULL)==0)return x;int uex,uey;if(ex==0){int s=__builtin_clzll(mx)-11;mx<<=(unsigned)s;uex=-1074-s;}else{mx|=0x10000000000000ULL;uex=ex-1075;}if(ey==0){int s=__builtin_clzll(my)-11;my<<=(unsigned)s;uey=-1074-s;}else{my|=0x10000000000000ULL;uey=ey-1075;}if(uex<uey||(uex==uey&&mx<my))return x;long E=uex<uey?uex:uey;void*Mx=bi_shl_u64(mx,(long)(uex-E));void*My=bi_shl_u64(my,(long)(uey-E));void*R=piton_bigint_mod(Mx,My);return piton_bigint_scaled_to_double(R,E,sx);}
static int piton_double_isinf(long bits){unsigned long long u=(unsigned long long)bits;return ((u&0x7FF0000000000000ULL)==0x7FF0000000000000ULL)&&((u&0xFFFFFFFFFFFFFULL)==0);}
static int piton_double_isfinite(long bits){unsigned long long u=(unsigned long long)bits;return ((u&0x7FF0000000000000ULL)!=0x7FF0000000000000ULL);}
static long piton_float_pow_v1(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0)return piton_double_bits(1.0);int neg=(x<0.0)&&(y==-1.0||y==1.0);double ax=neg?-x:x;if(y==1.0)return a;if(y==2.0||y==-2.0){double sq=ax*ax;long sb=piton_double_bits(sq);if(piton_double_isinf(sb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}if(y==2.0)return sb;if(x==0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}double r=1.0/sq;long rb=piton_double_bits(r);if(piton_double_isinf(rb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}return rb;}if(y==-1.0){if(ax==0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}double r=1.0/ax;if(neg)r=-r;long rb=piton_double_bits(r);if(piton_double_isinf(rb)&&piton_double_isfinite(a)){piton_raise_set("OverflowError","numerical result out of range");return 0;}return rb;}if(y==0.5||y==-0.5){if(x<0.0){piton_raise_set("ValueError","negative number cannot be raised to a fractional power");return 0;}if(x==0.0){if(y<0.0){piton_raise_set("ZeroDivisionError","0.0 cannot be raised to a negative power");return 0;}return piton_double_bits(0.0);}long sb=piton_float_sqrt(a);if(y==0.5)return sb;double r=1.0/piton_bits_double(sb);return piton_double_bits(r);}piton_raise_set("ValueError","float ** with non-trivial exponent is not supported in the native subset");return 0;}
static long piton_float_mod(long a,long b){double x=piton_bits_double(a),y=piton_bits_double(b);if(y==0.0){piton_raise_set("ZeroDivisionError","float modulo");return 0;}double r=piton_fmod_core(x,y);if(r==0.0)r=(y<0.0?-0.0:0.0);else if((y<0.0)!=(r<0.0))r+=y;return piton_double_bits(r);}
static long piton_bigint_cmp(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;if(x->sign!=y->sign)return x->sign?-1:1;int c=bi_cmp_mag(x,y);return x->sign?-c:c;}
static void piton_bigint_free(void*a){(void)a;}
"""


def _name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
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
        self._dict_elems: dict[str, dict[str, tuple[str, str]]] = {}
        self._gen_layout: dict[str, int] = {}
        self._gen_resumes: list[str] = []
        self._gen_counter = 0

    def emit(self, module: MIRModule) -> str:
        self.mir_module = module
        self.classes = getattr(module, "classes", {})
        self.class_parents = getattr(module, "class_parents", {})
        self.class_mro = getattr(module, "class_mro", {})
        self.class_properties = getattr(module, "class_properties", {})
        self.function_names = {function.name for function in module.functions}
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
        self.function_params = {function.name: list(function.params) for function in module.functions}
        self.function_frame_abi = {function.name: bool(function.frame_abi) for function in module.functions}
        self._scan_module_globals(module)
        self.function_return_types = self._infer_return_types(module)
        self.generator_layouts = {}
        for function in module.functions:
            if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                self.generator_layouts[function.name] = generator_slot_layout(function)
        self._has_bigint = any(
            instruction.op == "const" and instruction.args
            and isinstance(instruction.args[0], int) and abs(instruction.args[0]) > 9223372036854775807
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        ) or self._const_fold_overflows(module) or self._uses_float_mod(module)
        # Rich runtime (with __argc/__argv/_start and object/dict/set structs) needed for
        # bigint or sys.argv/os.name or any object/dict/set operations
        self._has_rich_runtime = self._has_bigint or any(
            instruction.op in {"object_new", "set_attr", "build_collection", "get_attr", "get_item", "collection_len", "list_append", "set_add", "dict_put", "sys_argv"}
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        )
        lines = [
            "typedef long i64; typedef unsigned long usize; typedef unsigned long long u64; typedef unsigned __int128 u128;",
            "static long piton_write(long fd,const void*buf,usize n){long r;__asm__ volatile(\"syscall\":\"=a\"(r):\"a\"(1L),\"D\"(fd),\"S\"(buf),\"d\"(n):\"rcx\",\"r11\",\"memory\");return r;}",
            "__attribute__((noreturn)) static void piton_exit(long code){__asm__ volatile(\"syscall\"::\"a\"(60L),\"D\"(code):\"rcx\",\"r11\",\"memory\");__builtin_unreachable();}",
            "static usize piton_strlen(const char*s){usize n=0;while(s[n])++n;return n;}",
            "static int piton_strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return (unsigned char)*a-(unsigned char)*b;}",
            "static void piton_print_str_raw(const char*s){piton_write(1,s,piton_strlen(s));}",
            "static void piton_print_str(const char*s){piton_print_str_raw(s);piton_write(1,\"\\n\",1);}",
            "static void piton_print_dynamic(long bits){if(!bits){piton_write(1,\"None\",4);}else{piton_write(1,(const char*)bits,piton_strlen((const char*)bits));}}",
            "static void piton_write_int(i64 number){char b[32];usize i=sizeof(b);unsigned long value;if(number<0){piton_write(1,\"-\",1);value=0-(unsigned long)number;}else value=(unsigned long)number;do{b[--i]=(char)(\'0\'+value%10);value/=10;}while(value);piton_write(1,b+i,sizeof(b)-i);}",
            "static void piton_print_int_raw(i64 number){piton_write_int(number);}",
            "static void piton_print_int(i64 number){piton_print_int_raw(number);piton_write(1,\"\\n\",1);}",
            "static i64 piton_floor_div(i64 a,i64 b){i64 q=a/b,r=a%b;if(r&&((r<0)!=(b<0)))--q;return q;}",
            "static i64 piton_mod(i64 a,i64 b){i64 r=a%b;if(r&&((r<0)!=(b<0)))r+=b;return r;}",
            "static char piton_concat_buf[65536];static char*piton_concat_ptr=0;",
            "static long piton_str_concat(const char*a,const char*b){if(!piton_concat_ptr)piton_concat_ptr=piton_concat_buf;usize la=piton_strlen(a),lb=piton_strlen(b);char*r=piton_concat_ptr;for(usize i=0;i<la;++i)r[i]=a[i];for(usize i=0;i<lb;++i)r[la+i]=b[i];r[la+lb]=0;piton_concat_ptr+=la+lb;return(long)r;}",
        ]
        if self._has_rich_runtime:
            lines.append(_FLOAT_REPR_C)
            lines.append(_RICH_FREESTANDING_C)
        if self._has_bigint:
            bigint_code = _BIGINT_FREESTANDING_C
            lines.extend(bigint_code.split("\n"))
        for function in module.functions:
            if function.name != "<module>":
                if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                    lines.append(f"static long {_name(function.name)}(PitonGenerator *piton_gen);")
                    continue
                params = "long *frame" if function.frame_abi else ", ".join(f"long {_name(param)}" for param in function.params) or "void"
                lines.append(f"static long {_name(function.name)}({params});")
        if self._shared_globals:
            # GLOBAL_DECL_V1: module-level names shared cross-function live
            # in file scope so every function references the same variable.
            for name in sorted(self._shared_globals):
                lines.append(f"static long {_name(name)};")
        if self._has_rich_runtime:
            # Forward declarations for runtime helpers used in <module>
            lines.append("static long piton_argv_new(void);")
        for function in module.functions:
            lines.extend(self._emit_function(function))
        if self._has_rich_runtime:
            lines.append("__attribute__((used)) int __argc=0;__attribute__((used)) char**__argv=0;")
            lines.append("static long piton_argv_new(void){PitonSeq*s=piton_seq_new(PK_LIST,__argc);for(int i=0;i<__argc;++i){piton_seq_put(s,i,(PitonSlot){(long)__argv[i],PK_STR});}return(long)s;}")
            lines.append('__attribute__((naked)) void _start(void){__asm__ volatile("mov (%rsp),%rax;mov %rax,__argc(%rip);lea 8(%rsp),%rax;mov %rax,__argv(%rip);xor %rbp,%rbp;call piton_main;mov %rax,%rdi;mov $60,%eax;syscall");}')
        return "\n".join(lines) + "\n"

    def _emit_function(self, function: MIRFunction) -> list[str]:
        if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
            return self._emit_generator_function(function)
        is_main = function.name == "<module>"
        params = "long *frame" if function.frame_abi else ", ".join(f"long {_name(param)}" for param in function.params) or "void"
        signature = "long piton_main(void)" if is_main else f"static long {_name(function.name)}({params})"
        slots = set(function.params)
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.result:
                    slots.add(instruction.result)
                if instruction.op == "store":
                    slots.add(instruction.args[0])
        locals_ = sorted((slots if function.frame_abi else slots - set(function.params)) - self._shared_globals)
        lines = [signature + " {"]
        if locals_:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot in locals_) + ";")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        self._fn_consts = {}
        self._tuple_elems = {k: v for k, v in self._tuple_elems.items() if not k.startswith('%')}
        self._dict_elems = {k: v for k, v in self._dict_elems.items() if not k.startswith('%')}
        if function.vararg:
            types[function.vararg] = "tuple"
        if function.kwarg:
            types[function.kwarg] = "dict"
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
                lines.append(f"    {_name(param)}=frame[{index}];")
        bigint_slots: list[str] = []
        for block in function.blocks:
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
        ordered = sorted(layout.items(), key=lambda item: item[1])
        lines = [f"static long {_name(function.name)}(PitonGenerator *piton_gen) {{"]
        if ordered:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot, _ in ordered) + ";")
        lines.append("    long __sent = 0;")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        self._fn_consts = {}
        self._tuple_elems = {k: v for k, v in self._tuple_elems.items() if not k.startswith('%')}
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
            for block in function.blocks:
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

    def _emit_generator_function(self, function: MIRFunction) -> list[str]:
        """Emit a suspendible generator body: ``state`` dispatch over heap slots.

        Same logical ABI as the Win64 backend: ``PitonGenerator*`` in,
        yielded value out, ``state`` selects the resume point, ``finished``
        marks completion. All mutable slots are mirrored into ``piton_gen->slots``.
        """
        layout = self.generator_layouts.get(function.name)
        if layout is None:
            raise NativeBuildError(f"native generator '{function.name}' has no persisted-slot layout")
        ordered = sorted(layout.items(), key=lambda item: item[1])
        lines = [f"static long {_name(function.name)}(PitonGenerator *piton_gen) {{"]
        if ordered:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot, _ in ordered) + ";")
        lines.append("    long __sent = 0;")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        self._fn_consts = {}
        self._tuple_elems = {k: v for k, v in self._tuple_elems.items() if not k.startswith('%')}
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
            for block in function.blocks:
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

    def _value(self, value: Any) -> str:
        if isinstance(value, str) and value.startswith("%"):
            return _name(value)
        if isinstance(value, str):
            return f"(long){json.dumps(value)}"
        if value is None:
            return "0"
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"piton_double_bits({_float_c_literal(value)})"
        raise NativeBuildError(f"Linux scalar backend cannot encode {value!r}")

    @staticmethod
    def _kind(value_type: str | None) -> str:
        return {
            "none": "PK_NONE", "bool": "PK_BOOL", "int": "PK_INT",
            "float": "PK_FLOAT", "str": "PK_STR", "list": "PK_LIST",
            "tuple": "PK_TUPLE", "dict": "PK_DICT", "set": "PK_SET",
            "bigint": "PK_BIGINT",
        }.get(value_type or "int", "PK_OBJECT")

    def _slot(self, value: Any, types: dict[str, str]) -> str:
        return f"piton_slot({self._value(value)},{self._kind(types.get(value, 'int'))})"

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
                if i < n and template[i] in "+ #":
                    raise NativeBuildError("str % +, space and # flags are not supported")
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
                if c2 in "srdc":
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

    def _emit_collection_method(self, out: list[str], result: Any, method: str, obj: Any,
                                call_args: list[Any], coll_type: str, types: dict[str, str]) -> None:
        """COLL_METHODS_V1: builtin collection methods bound by static dispatch.

        Same contract as STR_METHODS_V1: exact arity checked at build time,
        mutators return None (types "none"), element-returning reads follow
        the get_item convention (types "int" — the untagged model cannot know
        the element type). Out-of-subset runtime inputs (pop from empty,
        heterogeneous sort) exit with the CPython exception name, like the
        other runtime helpers.
        """
        operand = self._value(obj)

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
                require_count((0,), "no arguments")
                out.append(f"    piton_seq_sort((PitonSeq*){operand});")
                types[result] = "none"
            else:
                raise NativeBuildError(f"Linux list.{method}() is not supported")
        elif coll_type == "tuple":
            if method == "count":
                require_count((1,), "exactly one argument")
                out.append(f"    {_name(result)}=piton_seq_count((PitonSeq*){operand},{self._slot(call_args[0], types)});")
                types[result] = "int"
            else:
                raise NativeBuildError(f"Linux tuple.{method}() is not supported")
        elif coll_type == "dict":
            if method == "get":
                require_count((1, 2), "one or two arguments (key[, default])")
                if len(call_args) == 2:
                    default = self._slot(call_args[1], types)
                    out.append(f"    {_name(result)}=piton_dict_get_d((PitonDict*){operand},{self._slot(call_args[0], types)},{default}).bits;")
                else:
                    # single-arg get on a missing key raises KeyError — the
                    # untagged model cannot print an int-or-None union, and a
                    # loud error beats a silent wrong value (consistent with
                    # d[k] in this backend, which also raises KeyError).
                    out.append(f"    {_name(result)}=piton_dict_get_1((PitonDict*){operand},{self._slot(call_args[0], types)}).bits;")
                types[result] = "int"
            else:
                raise NativeBuildError(f"Linux dict.{method}() is not supported")
        elif coll_type == "set":
            if method == "add":
                require_count((1,), "exactly one argument")
                out.append(f"    piton_set_add((PitonSet*){operand},{self._slot(call_args[0], types)});")
                types[result] = "none"
            else:
                raise NativeBuildError(f"Linux set.{method}() is not supported")

    def _emit_str_method(self, out: list[str], result: Any, method: str, obj: Any,
                         call_args: list[Any], types: dict[str, str]) -> None:
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
            require_count(1, "exactly one str argument")
            out.append(f"    {_name(result)}=piton_str_find((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "int"
        elif method in {"startswith", "endswith"}:
            require_count(1, "exactly one str argument")
            helper = "piton_str_startswith" if method == "startswith" else "piton_str_endswith"
            out.append(f"    {_name(result)}={helper}((const char*){operand},{require_str(0, 'a str argument')});")
            types[result] = "bool"
        elif method == "replace":
            require_count(2, "exactly two str arguments")
            out.append(
                f"    {_name(result)}=(long)piton_str_replace((const char*){operand},"
                f"{require_str(0, 'two str arguments')},{require_str(1, 'two str arguments')});"
            )
            types[result] = "str"
        elif method == "split":
            if len(call_args) > 1:
                raise NativeBuildError("Linux str.split() requires zero or one argument")
            if call_args and types.get(call_args[0]) not in {"str", "none"}:
                raise NativeBuildError("Linux str.split() requires a str separator or nothing")
            sep = "(const char*)0" if not call_args or types.get(call_args[0]) == "none" else f"(const char*){self._value(call_args[0])}"
            out.append(f"    {_name(result)}=piton_str_split((const char*){operand},{sep});")
            types[result] = "list"
        elif method in {"strip", "lstrip", "rstrip"}:
            require_count(0, "no arguments")
            mode = {"strip": 0, "lstrip": 1, "rstrip": 2}[method]
            out.append(f"    {_name(result)}=(long)piton_str_strip((const char*){operand},{mode});")
            types[result] = "str"
        elif method == "join":
            require_count(1, "exactly one list or tuple argument")
            if types.get(call_args[0]) not in {"list", "tuple"}:
                raise NativeBuildError("Linux str.join() requires one list or tuple argument")
            out.append(f"    {_name(result)}=(long)piton_str_join((const char*){operand},(PitonSeq*){self._value(call_args[0])});")
            types[result] = "str"
        elif method == "format":
            # CPython {}/ {N} positional substitution; each argument is
            # str()-converted by static type first. {name}, format specs and
            # keywords are out of subset (the C parser reports and exits).
            pieces = []
            for index, value in enumerate(call_args):
                arg_type = types.get(value, "int")
                if arg_type == "int":
                    pieces.append(f"const char*_pf{index}=(const char*)piton_str_from_int({self._value(value)});")
                elif arg_type == "float":
                    pieces.append(f"const char*_pf{index}=(const char*)piton_str_from_float({self._value(value)});")
                elif arg_type == "bool":
                    pieces.append(f'const char*_pf{index}={self._value(value)}?"True":"False";')
                elif arg_type == "none":
                    pieces.append(f'const char*_pf{index}="None";')
                elif arg_type == "str":
                    pieces.append(f"const char*_pf{index}=(const char*){self._value(value)};")
                else:
                    raise NativeBuildError(f"Linux str.format() does not support {arg_type} arguments")
            names = ",".join(f"_pf{index}" for index in range(len(call_args))) or "_pf0"
            if not call_args:
                pieces.append('const char*_pf0="";')
            out.append(f"    {{{''.join(pieces)}const char*_fa[]={{{names}}}; {_name(result)}=(long)piton_str_format((const char*){operand},{len(call_args)},_fa);}}")
            types[result] = "str"
        else:
            raise NativeBuildError(f"Linux str.{method}() is not supported")

    def _emit_exc_check(self, out: list[str], function: MIRFunction, handler_label: Any) -> None:
        """Route a live native exception (piton_raise_set from a helper) to
        the enclosing try handler, or report and exit when unhandled."""
        out.append("    if(piton_exc_flag){")
        if handler_label:
            out.append(f"        goto {_name(function.name + '_' + handler_label)};")
        else:
            out.append("        piton_report_unhandled();piton_exit(1);")
        out.append("    }")

    def _emit_contains(self, out: list[str], result: Any, left: Any, right: Any,
                       types: dict[str, str], negate: bool) -> None:
        """CONTAINS_V1: CPython membership (`in` / `no en`).

        The haystack dispatches statically; the needle becomes a PitonSlot so
        elements compare by value (slot_eq: int bits, str strcmp). Needles of
        any other type fail closed — value equality for floats (0.0 vs -0.0),
        bigints and objects needs either a tagged runtime or __eq__ dispatch
        (follow-up), and comparing raw bits would be silent approximation.
        """
        needle_type = types.get(left, "int")
        haystack_type = types.get(right, "int")
        if needle_type not in {"int", "bool", "str"}:
            raise NativeBuildError(
                f"Linux 'in' requires an int/bool/str needle, not {needle_type}"
            )
        if haystack_type == "str":
            if needle_type != "str":
                raise NativeBuildError("Linux 'in' on str requires a str needle")
            expression = f"piton_str_contains((const char*){self._value(right)},(const char*){self._value(left)})"
        elif haystack_type in {"list", "tuple"}:
            expression = f"piton_seq_contains((PitonSeq*){self._value(right)},{self._slot(left, types)})"
        elif haystack_type == "dict":
            expression = f"piton_dict_contains((PitonDict*){self._value(right)},{self._slot(left, types)})"
        elif haystack_type == "set":
            expression = f"piton_set_contains((PitonSet*){self._value(right)},{self._slot(left, types)})"
        else:
            raise NativeBuildError(f"Linux 'in' is not supported on {haystack_type}")
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

    def _complete_call_args(self, function_name: str, values: list[Any]) -> list[Any]:
        defaults = self.function_defaults.get(function_name)
        if not defaults:
            return values
        while len(values) < len(defaults):
            default_value = defaults[len(values)]
            if default_value is None:
                break
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
        self._shared_globals = self._module_stored & declared_anywhere

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
            if isinstance(value, float):
                return "float"
            if isinstance(value, int):
                return "bigint" if abs(value) > 9223372036854775807 else "int"
            return self._UNKNOWN

        def infer(name: str) -> str | None:
            if name in memo:
                return memo[name]
            if name in resolving:
                return None
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
                    return None

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
                                if isinstance(callee, str) and callee in by_name and callee not in function.params:
                                    origins[result] = ("call", callee)
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

                if not returns:
                    inferred: str | None = "none"
                else:
                    candidates = {operand_type(operand) for operand in returns}
                    if len(candidates) == 1:
                        inferred = next(iter(candidates))
                    else:
                        inferred = None
                if inferred is None or inferred == self._UNKNOWN:
                    inferred = None
            finally:
                resolving.discard(name)
            memo[name] = inferred
            return inferred

        resolved: dict[str, str] = {}
        for function in module.functions:
            inferred = infer(function.name)
            resolved[function.name] = inferred if inferred else "int"
        return resolved

    def _emit_instruction(
        self, instruction: MIRInstruction, function: MIRFunction,
        aliases: dict[str, str], types: dict[str, str], bigint_slots: list[str],
    ) -> list[str]:
        op, args, result = instruction.op, instruction.args, instruction.result
        out: list[str] = []
        if op == "iter_new":
            source = args[0]
            source_type = types.get(source, "")
            if source_type in {"genexpr", "iterator:genexpr"}:
                out.append(f"    {_name(result)}=piton_genexpr_iter((PitonGenExpr*){self._value(source)});")
                types[result] = "iterator:genexpr"
            elif source_type.startswith("object:"):
                class_name = source_type.split(":", 1)[1]
                method_class = self._resolve_method(class_name, "__iter__")
                out.append(f"    {_name(result)}={_name(method_class+'__'+'__iter__')}({self._value(source)});")
                types[result] = f"iterator:object:{class_name}"
            elif source_type == "generator":
                out.append(f"    {_name(result)}={self._value(source)};")
                types[result] = "generator"
                return out
            elif source_type.startswith("iterator:"):
                # ITER_PASSTHROUGH_V1: iter(x) on an iterator returns x
                # itself (CPython semantics) — this unblocks for-loops over
                # builtin iterators (enumerate/zip/map/filter/reversed),
                # whose iter_next dispatch already exists.
                out.append(f"    {_name(result)}={self._value(source)};")
                types[result] = source_type
                return out
            elif source_type == "str":
                out.append(f"    {_name(result)}=piton_str_iterator_new((char*){self._value(source)});")
                types[result] = "iterator:str"
            else:
                iterator_kind = {"list": "PK_LIST", "tuple": "PK_TUPLE", "dict": "PK_DICT", "set": "PK_SET"}.get(source_type)
                if iterator_kind is None:
                    raise NativeBuildError("Linux iter requires a native collection or user __iter__")
                out.append(f"    {_name(result)}=piton_iterator_new_any((void*){self._value(source)},{iterator_kind});")
            types[result] = f"iterator:{source_type or 'unknown'}"
        elif op == "builtin_iter_new":
            builtin, source, start = args
            if builtin == "calliter":
                callable_src, sentinel_src = source
                out.append(f"    {_name(result)}=piton_calliter_new({self._value(callable_src)},{self._value(sentinel_src)});")
            elif builtin == "enumerate":
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native enumerate currently requires a list or tuple")
                start_value = self._value(start) if start is not None else "0"
                out.append(f"    {_name(result)}=piton_enumerate_new((void*){self._value(source)},{start_value});")
            elif builtin == "reversed":
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native reversed currently requires a list or tuple")
                out.append(f"    {_name(result)}=piton_reversed_new((void*){self._value(source)});")
            elif builtin == "zip":
                left, right = source
                if types.get(left) not in {"list", "tuple"} or types.get(right) not in {"list", "tuple"}:
                    raise NativeBuildError("native zip currently requires two lists or tuples")
                out.append(f"    {_name(result)}=piton_zip_new((void*){self._value(left)},(void*){self._value(right)});")
            elif builtin in {"map", "filter"}:
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native map/filter require a list or tuple")
                if types.get(start) == "builtin":
                    # ITER_PASSTHROUGH_V1 guard: a builtin NAME has no native
                    # function address — passing its uninitialized marker as
                    # the callback would jump through garbage (crash).
                    raise NativeBuildError(
                        f"native {builtin} requires a user function callback, not a builtin name"
                    )
                callback = self._value(start)
                out.append(f"    {_name(result)}=piton_callback_iterator_new((void*){self._value(source)},(long){callback},{1 if builtin == 'filter' else 0});")
            elif builtin == "sorted":
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native sorted currently requires one list or tuple")
                out.append(f"    {_name(result)}=piton_sorted_new((void*){self._value(source)});")
            else:
                raise NativeBuildError(f"native builtin iterator not supported: {builtin}")
            types[result] = f"iterator:{builtin}"
        elif op == "iter_next":
            iterator, handler_label = args[0], args[1]
            iterator_type = types.get(iterator, "")
            # ITER_PASSTHROUGH_V1: a nested loop's exhaustion flag would
            # otherwise trip the outer loop's check (same stale-flag class
            # the Windows backend clears at the loop-exit block). Any live
            # flag here is stale by construction — live raises route or exit
            # at their own emission site — so clearing on entry is sound.
            out.append("    piton_catch_clear();")
            if iterator_type in {"genexpr", "iterator:genexpr"}:
                out.append(f"    {_name(result)}=piton_genexpr_next((PitonGenExpr*){self._value(iterator)});")
            elif iterator_type == "generator":
                out.append(f"    {_name(result)}=piton_gen_next({self._value(iterator)});")
            elif iterator_type == "iterator:str":
                out.append(f"    {_name(result)}=piton_str_iterator_next({self._value(iterator)});")
            elif iterator_type.startswith("iterator:object:") or iterator_type.startswith("object:"):
                class_name = iterator_type.split(":", 2)[2] if iterator_type.startswith("iterator:") else iterator_type.split(":", 1)[1]
                method_class = self._resolve_method(class_name, "__next__")
                out.append(f"    {_name(result)}={_name(method_class+'__'+'__next__')}({self._value(iterator)});")
            else:
                next_helper = {"iterator:enumerate": "piton_enumerate_next", "iterator:reversed": "piton_reversed_next", "iterator:zip": "piton_zip_next", "iterator:map": "piton_callback_iterator_next", "iterator:filter": "piton_callback_iterator_next", "iterator:calliter": "piton_calliter_next"}.get(iterator_type, "piton_iterator_next_any")
                out.append(f"    {_name(result)}={next_helper}({self._value(iterator)});")
            types[result] = "tuple" if iterator_type in {"iterator:enumerate", "iterator:zip"} else "str" if iterator_type in {"iterator:dict", "iterator:str"} else "int"
            out.append("    if(piton_exc_flag){")
            if handler_label:
                out.append(f"        goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("        piton_report_unhandled();piton_exit(1);")
            out.append("    }")
        elif op == "const":
            value = args[0]
            # INTOVF_GUARD_V1: record constant values so the binary lowering
            # can fold int + - * exactly (Python bignum arithmetic IS the
            # oracle) instead of emitting wrapping i64 C arithmetic.
            if result:
                self._fn_consts[result] = value
            if isinstance(value, str):
                out.append(f"    {_name(result)}=(long){json.dumps(value)};")
                types[result] = "str"
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
            else:
                raise NativeBuildError(f"Linux backend cannot encode constant {value!r}")
        elif op == "load":
            source = args[0]
            aliases[result] = source
            types[result] = types.get(source, "int")
            if source in self._tuple_elems:
                self._tuple_elems[result] = self._tuple_elems[source]
            if source in self._dict_elems:
                self._dict_elems[result] = self._dict_elems[source]
            if source in self.function_names:
                out.append(f"    {_name(result)}=(long)&{_name(source)};")
                return out
            if source in _BUILTINS and source not in function.params:
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
                return out
            if (
                source in self._module_stored
                and function.name != "<module>"
                and source not in function.params
                and source not in self._func_stores.get(function.name, ())
            ):
                # CPython allows reads without declaration, but the subset
                # needs the explicit opt-in (it keeps writes local); fail
                # closed with an actionable message instead of C spew.
                raise NativeBuildError(
                    f"Linux '{source}' is assigned at module level; declare it global to read it inside '{function.name}'"
                )
            out.append(f"    {_name(result)}={_name(source)};")
        elif op == "global_decl":
            # GLOBAL_DECL_V1: pure metadata (resolved in the pre-scan); no code.
            return out
        elif op == "store":
            if types.get(args[1]) == "builtin":
                # BUILTIN_MARKER_V1 (kept first: it applies to globals too).
                raise NativeBuildError(
                    f"Linux native store of builtin '{aliases.get(args[1], args[1])}' as a value is not supported"
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
                return out
            out.append(f"    {_name(args[0])}={self._value(args[1])};")
            types[args[0]] = types.get(args[1], "int")
            if args[1] in self._tuple_elems:
                self._tuple_elems[args[0]] = self._tuple_elems[args[1]]
            if args[1] in self._dict_elems:
                self._dict_elems[args[0]] = self._dict_elems[args[1]]
        elif op == "unary":
            operator = {"no": "!", "not": "!"}.get(args[0], args[0])
            if operator not in {"+", "-", "~", "!"}:
                raise NativeBuildError(f"Linux unary operator not supported: {operator}")
            if types.get(args[1]) == "none" and operator in {"+", "-", "~"}:
                raise NativeBuildError(
                    f"Linux unary {operator} with None is not supported (CPython raises TypeError)"
                )
            if operator == "-" and types.get(args[1]) == "bigint":
                out.append(f"    {_name(result)}=(long)piton_bigint_negate((void*){_name(args[1])});")
                types[result] = "bigint"
                bigint_slots.append(result)
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
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
            if operator == "in":
                # CONTAINS_V1: membership; mir lowers `a in b` here and
                # `a no en b` as a compare op that reuses the same helper.
                # Must run before the str/collection branches: they treat
                # 'in' as an unsupported str/collection operator.
                self._emit_contains(out, result, left, right, types, negate=False)
                return out
            if "str" in {left_type, right_type}:
                if operator == "+" and left_type == right_type == "str":
                    out.append(f"    {_name(result)}=(long)piton_str_concat((const char*){_name(left)},(const char*){_name(right)});")
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
                                raise NativeBuildError(
                                    "Linux str % formatting a bare parameter is not supported (type unknown)"
                                )
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
                        if aliases.get(right, right) in function.params:
                            # the parameter's runtime type is unknown: treating
                            # it as a scalar would print a raw pointer for
                            # tuple/object arguments (fail-open).
                            raise NativeBuildError(
                                "Linux str % formatting a bare parameter is not supported (type unknown)"
                            )
                        operands = [self._value(right)]
                        etypes = [types.get(right, "int")]
                    if len(fields) != len(operands):
                        raise NativeBuildError(
                            f"Linux str % formatting: {len(fields)} conversion(s) but {len(operands)} argument(s)"
                        )
                    bases = []
                    pads: list[tuple | None] = []
                    for index, (field, operand, arg_type) in enumerate(zip(fields, operands, etypes)):
                        _fname, spec, flags, width, prec = field
                        if spec == "s":
                            if arg_type == "int":
                                base = f"piton_str_from_int({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_float({operand})"
                            elif arg_type == "bool":
                                base = f'({operand}?"True":"False")'
                            elif arg_type == "none":
                                base = '"None"'
                            elif arg_type == "str":
                                base = f"((const char*){operand})"
                            else:
                                raise NativeBuildError(f"Linux str %s does not support {arg_type} arguments")
                        elif spec == "d":
                            if arg_type in {"int", "bool"}:
                                base = f"piton_str_from_int({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_int((long)piton_bits_double({operand}))"
                            else:
                                raise NativeBuildError(f"Linux str %d requires a real number, not {arg_type}")
                        elif spec == "r":
                            if arg_type == "str":
                                base = f"piton_str_quote((const char*){operand})"
                            elif arg_type == "bool":
                                base = f'({operand}?"True":"False")'
                            elif arg_type == "int":
                                base = f"piton_str_from_int({operand})"
                            elif arg_type == "float":
                                base = f"piton_str_from_float({operand})"
                            elif arg_type == "none":
                                base = '"None"'
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
                            base = f"piton_str_from_int_base({operand},{base_num},{1 if spec == 'X' else 0})"
                        else:
                            raise NativeBuildError(f"Linux str %{spec} is not supported")
                        bases.append(f"const char*_pb{index}=(const char*){base};")
                        if width >= 0 or prec >= 0:
                            isnum = 1 if spec in {"d", "x", "X", "o"} else 0
                            pads.append((width, prec, flags, isnum))
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
                    out.append(f"    {{const char*_pa[]={{{names}}}; {_name(result)}=(long)piton_str_format({json.dumps(translated)},{len(operands)},_pa);}}")
                    self._emit_exc_check(out, function, handler_label)
                    out.append("    }")
                    types[result] = "str"
                    return out
                raise NativeBuildError(f"Linux string binary operator not supported: {operator}")
            if {left_type, right_type} & {"list", "tuple", "dict", "set"}:
                # SEQ_CONCAT_V1: list+list / tuple+tuple concatenate; any other
                # collection arithmetic is a CPython TypeError — fail closed at
                # build time instead of doing C pointer arithmetic (previously
                # printed a raw pointer as an int).
                if operator == "+" and left_type == right_type and left_type in {"list", "tuple"}:
                    out.append(f"    {_name(result)}=piton_seq_concat((PitonSeq*){self._value(left)},(PitonSeq*){self._value(right)});")
                    types[result] = left_type
                    return out
                raise NativeBuildError(f"Linux collection binary operator not supported: {operator} on {left_type}/{right_type}")
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
                expression = f"({self._value(left)} {operator} {self._value(right)})"
            else:
                raise NativeBuildError(f"Linux binary operator not supported: {operator}")
            out.append(f"    {_name(result)}={expression};")
            types[result] = "int"
        elif op == "compare":
            operator, left, right = args
            if operator == "es":
                out.append(f"    {_name(result)}=({self._value(left)} == {self._value(right)});")
                types[result] = "bool"
                return out
            if operator == "no en":
                # CONTAINS_V1: `a no en b` shares the membership lowering.
                self._emit_contains(out, result, left, right, types, negate=True)
                return out
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
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
            if left_type == "bigint" or right_type == "bigint":
                out.append(f"    {_name(result)}=(piton_bigint_cmp((void*){_name(left)},(void*){_name(right)}) {operator} 0);")
                types[result] = "bool"
                return out
            if "float" in {left_type, right_type}:
                left_value = f"piton_bits_double({self._value(left)})" if left_type == "float" else f"(double){self._value(left)}"
                right_value = f"piton_bits_double({self._value(right)})" if right_type == "float" else f"(double){self._value(right)}"
                out.append(f"    {_name(result)}=({left_value} {operator} {right_value});")
                types[result] = "bool"
                return out
            if types.get(left) == types.get(right) == "str":
                expression = f"(piton_strcmp((char*){self._value(left)},(char*){self._value(right)}) {operator} 0)"
            else:
                expression = f"({self._value(left)} {operator} {self._value(right)})"
            out.append(f"    {_name(result)}={expression};")
            types[result] = "bool"
        elif op == "branch":
            out.append(f"    if({self._value(args[0])}) goto {_name(function.name + '_' + args[1])}; else goto {_name(function.name + '_' + args[2])};")
        elif op == "jump":
            out.append(f"    goto {_name(function.name + '_' + args[0])};")
        elif op == "call":
            function_name = aliases.get(args[0], args[0])
            values = list(args[1])
            call_handler = args[2] if len(args) > 2 else None
            if function_name in {"imprimir", "print"}:
                if values:
                    # PRINT_ARGS_V1: CPython print(a, b, ...) str()s every
                    # positional argument and joins them with single spaces.
                    # Each backend printer is the *_raw (no newline) variant;
                    # one newline is written after the last argument.
                    for index, value in enumerate(values):
                        value_type = types.get(value, "int")
                        if index:
                            out.append('    piton_write(1," ",1);')
                        # PRINT_UNPRINTABLE_V1: iterators, generators, closures,
                        # module markers and builtin markers can never match
                        # CPython (addresses / uninitialized C values) — fail
                        # closed instead of printing garbage.
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
                            for candidate in self.class_mro.get(cls_name, []):
                                if "__str__" in self.classes.get(candidate, set()):
                                    str_cls = candidate
                                    break
                            if str_cls is None and "__str__" in self.classes.get(cls_name, set()):
                                str_cls = cls_name
                            if str_cls is not None:
                                out.append(f'    piton_print_str_raw((const char*){_name(str_cls+"__"+"__str__")}({self._value(value)}));')
                                continue
                            raise NativeBuildError(
                                f"Linux native print of a '{cls_name}' instance without __str__ is not supported "
                                "(can never match CPython object repr)"
                            )
                        if value_type == "str":
                            out.append(f'    piton_print_str_raw((char*){self._value(value)});')
                        elif value_type == "bool":
                            out.append(f'    piton_print_str_raw({self._value(value)}?"True":"False");')
                        elif value_type == "none":
                            out.append('    piton_print_str_raw("None");')
                        elif value_type == "bigint":
                            out.append(f'    piton_bigint_print_raw((void*){_name(value)});')
                        elif value_type == "float":
                            out.append(f'    piton_print_float_bits_raw({self._value(value)});')
                        elif value_type == "module-pkg":
                            out.append(f'    piton_print_dynamic({self._value(value)});')
                        elif value_type in {"list", "tuple", "dict", "set"}:
                            out.append(f'    piton_print_slot({self._slot(value, types)});')
                        else:
                            out.append(f'    piton_print_int_raw((long){self._value(value)});')
                    out.append('    piton_write(1,"\\n",1);')
                else:
                    out.append('    piton_write(1,"\\n",1);')
                out.append(f"    {_name(result)}=0;")
            elif function_name in {"longitud", "len"}:
                if values:
                    v0_type = types.get(values[0], "")
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
                if len(values) == 1 and types.get(values[0]) == "str":
                    # STR_LEN_V1: len('hola') is the C string length.
                    out.append(f"    {_name(result)}=(long)piton_strlen((const char*){self._value(values[0])});")
                    types[result] = "int"
                    return out
                if len(values) != 1 or types.get(values[0]) not in {"list", "tuple", "dict", "set"}:
                    raise NativeBuildError("Linux len requires one collection")
                value_type = types[values[0]]
                if value_type in {"list", "tuple"}:
                    expression = f"((PitonSeq*){self._value(values[0])})->length"
                elif value_type == "dict":
                    expression = f"((PitonDict*){self._value(values[0])})->length"
                else:
                    expression = f"((PitonSet*){self._value(values[0])})->length"
                out.append(f"    {_name(result)}={expression};")
                types[result] = "int"
            elif function_name == "abs":
                if len(values) != 1:
                    raise NativeBuildError("Linux abs requires one argument")
                if types.get(values[0]) == "float":
                    out.append(f"    {_name(result)}={self._value(values[0])}&0x7fffffffffffffffUL;")
                    types[result] = "float"
                else:
                    out.append(f"    {_name(result)}={self._value(values[0])}<0?-{self._value(values[0])}:{self._value(values[0])};")
                    types[result] = "int"
            elif function_name in {"all", "any"}:
                if len(values) != 1 or types.get(values[0]) not in {"list", "tuple"}:
                    raise NativeBuildError(f"Linux {function_name} requires one list or tuple (M14 v1)")
                helper = "piton_all_seq" if function_name == "all" else "piton_any_seq"
                out.append(f"    {_name(result)}={helper}((PitonSeq*){self._value(values[0])});")
                types[result] = "bool"
            elif function_name == "pow":
                if len(values) != 2:
                    raise NativeBuildError("Linux pow requires exactly two arguments (M14 v1)")
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
                    raise NativeBuildError("Linux pow requires int or float base (M14 v1)")
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
                    raise NativeBuildError("Linux ord requires a str argument")
                helper = f"piton_{function_name}"
                if function_name == "ord":
                    out.append(f"    {_name(result)}={helper}((const char*){self._value(values[0])});")
                    types[result] = "int"
                else:
                    out.append(f"    {_name(result)}={helper}({self._value(values[0])});")
                    types[result] = "str"
                if call_handler is not None:
                    out.append(f"    if(piton_exc_flag){{goto {_name(function.name + '_' + call_handler)};}}")
                else:
                    out.append('    if(piton_exc_flag){piton_report_unhandled();piton_exit(1);}')
            elif function_name in {"round", "redondear"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux round requires exactly one argument (M14 v1; ndigits not supported)")
                arg_type = types.get(values[0])
                if arg_type == "float":
                    out.append(f"    {_name(result)}=piton_round_float({self._value(values[0])});")
                elif arg_type in {"int", "bool"}:
                    out.append(f"    {_name(result)}={self._value(values[0])};")
                else:
                    raise NativeBuildError("Linux round requires int or float")
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
                    raise NativeBuildError("Linux int() requires int, float or str (M14 v1)")
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
                if len(values) != 1:
                    raise NativeBuildError("Linux str requires exactly one argument")
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
                else:
                    raise NativeBuildError("Linux str() requires int/float/bool/None/str (M14 v1)")
                types[result] = "str"
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
                elif arg_type == "set":
                    out.append(f"    {_name(result)}=(((PitonSet*){operand})->length>0);")
                elif arg_type == "none":
                    out.append(f"    {_name(result)}=0;")
                else:
                    raise NativeBuildError("Linux bool() requires int/float/str/collection/None (M14 v1)")
                types[result] = "bool"
            elif function_name in {"min", "max"}:
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
            elif function_name == "sum":
                if len(values) != 1:
                    raise NativeBuildError("Linux sum requires one collection")
                value_type = types.get(values[0])
                struct_map = {"list": "PitonSeq", "tuple": "PitonSeq", "dict": "PitonDict", "set": "PitonSet"}
                helper_map = {"list": "piton_sum_seq", "tuple": "piton_sum_seq", "dict": "piton_sum_dict", "set": "piton_sum_set"}
                if value_type not in struct_map:
                    raise NativeBuildError("Linux sum requires one collection")
                out.append(f"    {_name(result)}={helper_map[value_type]}(({struct_map[value_type]}*){self._value(values[0])});")
                types[result] = "int"
            elif function_name in {"type", "tipo"}:
                if len(values) != 1:
                    raise NativeBuildError("Linux type requires one argument")
                out.append(f"    {_name(result)}=(long)piton_type_repr({self._kind(types.get(values[0], 'int'))});")
                types[result] = "str"
            elif function_name in {"sorted", "ordenar"}:
                if len(values) != 1 or types.get(values[0]) not in {"list", "tuple"}:
                    raise NativeBuildError("native sorted currently requires one list or tuple")
                out.append(f"    {_name(result)}=piton_sorted_new((void*){self._value(values[0])});")
                types[result] = "list"
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
                    encoded_values = ",".join(self._value(value) for value in values)
                    out.append(f"    {_name(result)}={_name(function_name)}({encoded_values});")
                    # RETURNTYPE_V1: statically-known user functions propagate
                    # their inferred return type so downstream consumers print
                    # collections/str/float correctly instead of a raw pointer.
                    types[result] = self.function_return_types.get(function_name, "int")
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
                    else:
                        argc = len(values)
                        arg_values = ",".join(self._value(value) for value in values)
                        out.append(f"    {{long _frame_args[]={{ {arg_values} }};")
                        out.append(
                            f"    {_name(result)}=piton_closure_call_frame({self._value(args[0])},{argc},_frame_args);"
                        )
                        out.append("    }")
                    types[result] = "int"
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
            types[result] = f"object:{cls_name}"
        elif op == "cell_new":
            value_arg = args[0]
            out.append("    {")
            out.append("        long*cell=(long*)piton_alloc(16);")
            if value_arg is None:
                out.append("        cell[0]=0;")
            else:
                out.append(f"        cell[0]={self._value(value_arg)};")
            out.append("        cell[1]=0;")
            out.append(f"        {_name(result)}=(long)cell;")
            out.append("    }")
            types[result] = "cell"
        elif op == "cell_load":
            cell_ptr_name = args[0]
            out.append(f"    {_name(result)}=((long*){_name(cell_ptr_name)})[0];")
            types[result] = "int"
        elif op == "cell_store":
            cell_ptr_name, value_arg = args
            out.append(f"    ((long*){_name(cell_ptr_name)})[0]={self._value(value_arg)};")
        elif op == "closure_new":
            lifted_name, n_args, capture_ops, has_vararg = args
            cell_values = [self._value(cap) for cap in capture_ops]
            out.append(
                f"    {_name(result)}=piton_closure_new_frame((long)&{_name(lifted_name)},{n_args},"
                f"{len(capture_ops)},(long[]){{ {','.join(cell_values)} }},{int(has_vararg)});"
            )
            types[result] = "closure"
        elif op == "frame_call":
            callee, call_args = args
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
            if len(call_args) > 4:
                raise NativeBuildError("Linux closure calls with more than four arguments are not supported yet")
            arg_values = [self._value(arg) for arg in call_args]
            arg_values += ["0"] * (4 - len(arg_values))
            out.append(
                f"    {_name(result)}=piton_closure_call6({self._value(callee)},{argc},{','.join(arg_values)});"
            )
            types[result] = "int"
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
                    out.append(f'    piton_object_set((PitonObject*){self._value(obj)},"{attr}",{self._slot(val, types)});')
        elif op == "get_attr":
            obj, attr = args
            owner_type = types.get(obj, "")
            prop_class = self._resolve_property_class(owner_type, attr)
            module_attr_types = {"__name__": "str", "__file__": "str", "__package__": "module-pkg", "modules": "dict:module"}
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
                    if self.function_frame_abi.get(target, False):
                        out.append(f'    {_name(result)}=piton_closure_new_frame((long)&{_name(target)},{len(params)-1},1,(long[]){{(long){self._value(obj)}}},0);')
                    else:
                        out.append(f'    {_name(result)}=piton_bound_method_new((long)&{_name(target)},{len(params)-1},(long){self._value(obj)});')
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
                out.append(f'    {_name(result)}=piton_object_get((PitonObject*){self._value(obj)},"{attr}").bits;')
                if owner_type == "object:module":
                    types[result] = module_attr_types.get(attr, "int")
                else:
                    types[result] = "int"
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
                else:
                    raise NativeBuildError(
                        f"native del on '{attr}' is not a property of a natively-typed object"
                    )
        elif op == "method_call":
            cls_name, method, obj = args[0], args[1], args[2]
            call_args = args[3] if len(args) > 3 else ()
            if cls_name is None:
                owner_type = types.get(obj, "")
                if owner_type == "str":
                    # STR_METHODS_V1: builtin str methods bind statically here;
                    # anything not in the table fails closed.
                    self._emit_str_method(out, result, method, obj, list(call_args), types)
                    return out
                coll_type = types.get(obj, "")
                if coll_type in {"list", "tuple", "dict", "set"}:
                    # COLL_METHODS_V1: builtin collection methods bind
                    # statically here, mirroring the str table.
                    self._emit_collection_method(out, result, method, obj, list(call_args), coll_type, types)
                    return out
                if not owner_type.startswith("object:"):
                    raise NativeBuildError("Linux method receiver class is not statically known")
                cls_name = owner_type.split(":", 1)[1]
                if self._resolve_property_class(owner_type, method) is not None:
                    raise NativeBuildError(
                        f"native property '{method}' of '{cls_name}' object is not a method (calling a property is unsupported)"
                    )
            cls_name = self._resolve_method(cls_name, method)
            values = ",".join(self._value(v) for v in ([obj] + list(call_args)))
            target = f"{cls_name}__{method}"
            if self.function_frame_abi.get(target, False):
                all_values = [obj, *call_args]
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
                out.extend(["    piton_report_unhandled();", "    piton_exit(1);"])
        elif op == "raise_typed":
            exc_type, payload, handler_label = args
            message = f"(const char*){self._value(payload)}" if payload is not None else '""'
            out.append(f'    piton_raise_set("{exc_type}",{message});')
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                if exc_type == "StopIteration":
                    out.append(f"    goto {_name(function.name + '___exit')};")
                else:
                    out.extend(["    piton_report_unhandled();", "    piton_exit(1);"])
        elif op == "try_push":
            pass  # no-op in static flag-based model
        elif op == "try_pop":
            pass  # no-op in static flag-based model
        elif op == "catch_flag":
            out.append(f'    {_name(result)}=piton_exc_flag;')
            types[result] = "bool"
        elif op == "catch_clear":
            out.append('    piton_catch_clear();')
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
                out.extend(["    piton_report_unhandled();", "    piton_exit(1);"])
        elif op == "raise_active_dynamic":
            # Dynamic re-raise: read the reraise slots (the handler may have
            # already cleared the live exception state via catch_clear).
            (handler_label,) = args
            out.append("    piton_reraise_set(piton_reraise_type);")
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
            else:
                out.extend(["    piton_report_unhandled();", "    piton_exit(1);"])
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
        elif op == "get_item":
            coll, idx = args
            collection_type = types.get(coll)
            if collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_get((PitonSeq*){self._value(coll)},{self._value(idx)}).bits;')
            elif collection_type == "str":
                # PARITY_P2_V1: str[s] yields the one-character string.
                out.append(f'    {_name(result)}=(long)piton_str_index((const char*){self._value(coll)},{self._value(idx)});')
                types[result] = "str"
                return out
            elif collection_type in {"dict", "dict:module"}:
                out.append(f'    {_name(result)}=piton_dict_get((PitonDict*){self._value(coll)},{self._slot(idx, types)}).bits;')
            else:
                raise NativeBuildError(f"Linux subscription not supported for {collection_type}")
            types[result] = "object:module" if collection_type == "dict:module" else "int"
        elif op == "get_slice":
            # PARITY_P2_V1: [a:b] slices. Missing bounds arrive as None; the
            # emitter substitutes 0 / INT64_MAX and the C helpers normalize
            # negative indices and clamp, matching CPython semantics.
            # SLICE_STEP_V1: with a step operand (possibly runtime), missing
            # bounds become INT64_MIN / INT64_MAX sentinels and the step
            # helpers apply direction-aware defaults.
            coll, lower, upper = args[0], args[1], args[2]
            step = args[3] if len(args) > 3 else None
            collection_type = types.get(coll)
            if step is None:
                lower_value = self._value(lower) if lower is not None else "0"
                upper_value = self._value(upper) if upper is not None else "0x7FFFFFFFFFFFFFFFL"
                if collection_type == "str":
                    out.append(f'    {_name(result)}=(long)piton_str_slice((const char*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = "str"
                elif collection_type in {"list", "tuple"}:
                    out.append(f'    {_name(result)}=piton_seq_slice((PitonSeq*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = collection_type
                else:
                    raise NativeBuildError(f"Linux slice not supported for {collection_type}")
                return out
            lower_value = self._value(lower) if lower is not None else "(-0x7FFFFFFFFFFFFFFFL-1)"
            upper_value = self._value(upper) if upper is not None else "0x7FFFFFFFFFFFFFFFL"
            step_value = self._value(step)
            if collection_type == "str":
                out.append(f'    {_name(result)}=(long)piton_str_slice_step((const char*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = "str"
            elif collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_slice_step((PitonSeq*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = collection_type
            else:
                raise NativeBuildError(f"Linux slice not supported for {collection_type}")
        elif op == "collection_len":
            coll = args[0]
            collection_type = types.get(coll)
            struct_name = {"list": "PitonSeq", "tuple": "PitonSeq", "dict": "PitonDict", "set": "PitonSet"}.get(collection_type)
            if not struct_name:
                raise NativeBuildError("Linux collection_len requires a collection")
            out.append(f'    {_name(result)}=(({struct_name}*){self._value(coll)})->length;')
            types[result] = "int"
        elif op == "list_append":
            coll, value = args
            collection_type = types.get(coll)
            if collection_type != "list":
                raise NativeBuildError("Linux list_append requires a list")
            out.append(f'    {{PitonSeq*_c=(PitonSeq*){self._value(coll)};PitonSlot _s;_s.bits={self._value(value)};_s.kind={self._kind(types.get(value, "int"))};piton_seq_append(_c,_s);}}')
        elif op == "gen_init":
            func_name = args[0]
            gen_args = tuple(args[1]) if len(args) > 1 and args[1] else ()
            layout = self.generator_layouts.get(func_name)
            if layout is None:
                raise NativeBuildError(f"native generator '{func_name}' has no persisted-slot layout")
            params = self.function_params.get(func_name, [])
            if len(gen_args) != len(params):
                raise NativeBuildError(
                    f"native generator '{func_name}' called with wrong number of arguments "
                    "(defaults not supported yet)"
                )
            if len(gen_args) > 4:
                raise NativeBuildError("native generator calls with more than four arguments are not supported yet")
            out.append(f"    {_name(result)}=piton_gen_new((long)&{_name(func_name)},{len(layout)});")
            for arg, param in zip(gen_args, params):
                out.append(f"    ((PitonGenerator*){_name(result)})->slots[{layout[param]}]={self._value(arg)};")
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
            for part in pos_parts:
                if part[0] == "star" and types.get(part[1]) not in {"list", "tuple"}:
                    raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1: * operand must be a statically-known list or tuple")
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
                else:
                    out.append(f"    long n_{site}=piton_unpack_seq4((PitonSlot){{(long){self._value(part[1])},{'PK_LIST' if types.get(part[1]) == 'list' else 'PK_TUPLE'}}},{n_params}-f_{site},b_{site}+(int)f_{site});")
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
