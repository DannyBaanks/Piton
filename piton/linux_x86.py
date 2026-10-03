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
enum{PK_NONE,PK_BOOL,PK_INT,PK_FLOAT,PK_STR,PK_LIST,PK_TUPLE,PK_DICT,PK_SET,PK_OBJECT,PK_BIGINT,PK_RANGE};
typedef struct{long bits;int kind;}PitonSlot;
typedef struct{long refcount;long kind;long start;long stop;long step;}PitonRange;
static long piton_range_len(PitonRange*r);
static long piton_range_eq(PitonRange*a,PitonRange*b);
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
    if((v.kind>=PK_LIST&&(v.kind<=PK_OBJECT||v.kind==PK_RANGE))){long*rc=(long*)v.bits;return*rc;}
    return-1;
}
static void piton_slot_incref(PitonSlot v){if((v.kind>=PK_LIST&&(v.kind<=PK_OBJECT||v.kind==PK_RANGE))){long*rc=(long*)v.bits;++(*rc);}}
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
    case PK_RANGE:{PitonRange*r=(PitonRange*)v.bits;
        piton_gc_unregister(r);piton_heap_free(r);break;}
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
static void piton_write_uint(unsigned long v){char b[32];usize i=sizeof(b);do{b[--i]=(char)('0'+v%10);v/=10;}while(v);piton_write(1,b+i,sizeof(b)-i);}
static void piton_write_uint_big(double d){unsigned long u;double t=d;int i,n=0,be;unsigned long frac,m;int e;unsigned int w[32];unsigned int c;unsigned long cur,rem;int nz;char buf[400];__builtin_memcpy(&u,&t,8);frac=u&0xFFFFFFFFFFFFFULL;be=(int)((u>>52)&0x7FF);m=be?frac|0x10000000000000UL:frac;e=be?be-1075:-1074;for(i=0;i<32;++i)w[i]=0;w[0]=(unsigned int)m;w[1]=(unsigned int)(m>>32);while(e>0){c=0;for(i=0;i<32;++i){cur=((unsigned long)w[i]<<1)|c;w[i]=(unsigned int)cur;c=(unsigned int)(cur>>32);}--e;}for(;;){rem=0;nz=0;for(i=31;i>=0;--i){cur=(rem<<32)|w[i];w[i]=(unsigned int)(cur/10);rem=cur%10;if(w[i])nz=1;}buf[n++]=(char)('0'+(char)rem);if(!nz)break;}while(n>0){--n;piton_write(1,buf+n,1);}}
static void piton_print_float_bits_raw(long bits){char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)bits);piton_write(1,r,(usize)n);}
static void piton_print_float_bits(long bits){piton_print_float_bits_raw(bits);piton_write(1,"\n",1);}
static int piton_slot_eq(PitonSlot a,PitonSlot b){if(a.kind!=b.kind)return 0;if(a.kind==PK_RANGE)return piton_range_eq((PitonRange*)a.bits,(PitonRange*)b.bits);if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)==0;return a.bits==b.bits;}
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
static long piton_str_startswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[i]!=p[i])return 0;return 1;}
static long piton_str_endswith(const char*s,const char*p){usize hl=piton_strlen(s),nl=piton_strlen(p);if(nl>hl)return 0;for(usize i=0;i<nl;++i)if(s[hl-nl+i]!=p[i])return 0;return 1;}
static long piton_str_replace(const char*s,const char*a,const char*b){usize sl=piton_strlen(s),al=piton_strlen(a),bl=piton_strlen(b);usize count=0;if(al==0){count=sl+1;}else{for(usize i=0;i+al<=sl;){usize k=0;while(k<al&&s[i+k]==a[k])++k;if(k==al){++count;i+=al;}else++i;}}usize total=sl+count*bl-(al==0?0:count*al);char*p=piton_alloc(total+1);usize o=0;if(al==0){for(usize i=0;i<sl;++i){piton_memcpy(p+o,b,bl);o+=bl;p[o++]=s[i];}piton_memcpy(p+o,b,bl);o+=bl;}else{for(usize i=0;i<sl;){usize k=0;while(k<al&&i+k<sl&&s[i+k]==a[k])++k;if(k==al){piton_memcpy(p+o,b,bl);o+=bl;i+=al;}else p[o++]=s[i++];}}p[o]=0;return(long)p;}
static int piton_chrinstr(const char*set,char c){if(!set)return piton_ws((unsigned char)c);for(usize i=0;set[i];++i)if(set[i]==c)return 1;return 0;}
static long piton_str_strip_chrs(const char*s,const char*chrs,int mode){usize n=piton_strlen(s),a=0,b=n;if(mode!=2){while(a<n&&piton_chrinstr(chrs,s[a]))++a;}if(mode!=1){while(b>a&&piton_chrinstr(chrs,s[b-1]))--b;}char*p=piton_alloc(b-a+1);piton_memcpy(p,s+a,b-a);p[b-a]=0;return(long)p;}
static long piton_str_capitalize(const char*s){usize n=piton_strlen(s);char*p=piton_alloc(n+1);for(usize i=0;i<n;++i){unsigned char c=(unsigned char)s[i];if(!i){if(c>='a'&&c<='z')c-=32;}else{if(c>='A'&&c<='Z')c+=32;}p[i]=(char)c;}p[n]=0;return(long)p;}
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
static long piton_str_partition(const char*s,const char*sep){usize n=piton_strlen(s),m=piton_strlen(sep);long at=-1;for(usize i=0;i+m<=n;++i){usize k=0;while(k<m&&s[i+k]==sep[k])++k;if(k==m){at=(long)i;break;}}PitonSeq*r=piton_seq_new(PK_TUPLE,3);long b=at<0?(long)n:at,a=at<0?(long)n:(long)(at+m);char*p1=piton_alloc((usize)b+1);piton_memcpy(p1,s,(usize)b);p1[b]=0;char*p2=piton_alloc(m+1);if(at<0)p2[0]=0;else piton_memcpy(p2,sep,m+1);char*p3=piton_alloc(n-(usize)a+1);piton_memcpy(p3,s+a,n-(usize)a);p3[n-(usize)a]=0;r->items[0]=(PitonSlot){(long)p1,PK_STR};r->items[1]=(PitonSlot){(long)p2,PK_STR};r->items[2]=(PitonSlot){(long)p3,PK_STR};return(long)r;}
static long piton_str_rsplit(const char*s,const char*sep,long maxsplit){if(!sep)return piton_str_split_ws(s);usize n=piton_strlen(s),m=piton_strlen(sep);PitonSeq*r=piton_seq_new(PK_LIST,0);if(m==0){for(long k=(long)n;k>=0;--k){char*q=piton_alloc(2);q[0]=s[k];q[1]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});}return(long)r;}usize end=n;long budget=maxsplit<0?(long)n:maxsplit;while(budget>0){long at=-1;for(long i=(long)end-(long)m;i>=0;--i){usize k=0;while(k<m&&s[i+(long)k]==sep[k])++k;if(k==m){at=i;break;}}if(at<0)break;--budget;usize tail=end-(usize)at-m;char*q=piton_alloc(tail+1);piton_memcpy(q,s+(usize)at+m,tail);q[tail]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});end=(usize)at;}char*head=piton_alloc(end+1);piton_memcpy(head,s,end);head[end]=0;piton_seq_append(r,(PitonSlot){(long)head,PK_STR});long total=r->length;for(long i=0;i<total/2;++i){PitonSlot t=r->items[i];r->items[i]=r->items[total-1-i];r->items[total-1-i]=t;}return(long)r;}
static long piton_str_splitlines(const char*s){usize n=piton_strlen(s);PitonSeq*r=piton_seq_new(PK_LIST,0);usize i=0;while(i<n){usize j=i;while(j<n&&s[j]!='\n'&&s[j]!='\r')++j;usize take=j-i;if(take>0&&s[j-1]=='\r')--take;char*q=piton_alloc(take+1);piton_memcpy(q,s+i,take);q[take]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});if(j<n&&s[j]=='\r'&&j+1<n&&s[j+1]=='\n')i=j+2;else i=j+1;}return(long)r;}
static long piton_str_expandtabs(const char*s,long tabsize){usize n=piton_strlen(s);char*out=piton_alloc(n*2+n+1);usize o=0,col=0;if(tabsize<1)tabsize=1;for(usize i=0;i<n;++i){char c=s[i];if(c=='\t'){usize w=(usize)tabsize-(col%(usize)tabsize);for(usize k=0;k<w;++k)out[o++]=' ';col+=w;}else{out[o++]=c;++col;}}out[o]=0;return(long)out;}
static long piton_str_split(const char*s,const char*sep){if(!sep)return piton_str_split_ws(s);PitonSeq*r=piton_seq_new(PK_LIST,0);usize sl=piton_strlen(s),nl=piton_strlen(sep);if(nl==0){for(usize k=0;k<=sl;++k){char*q=piton_alloc(2);q[0]=k<sl?s[k]:0;q[1]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});}return(long)r;}usize i=0;while(1){usize j=i;while(j+nl<=sl){usize k=0;while(k<nl&&s[j+k]==sep[k])++k;if(k==nl)break;++j;}usize len=j-i;char*q=piton_alloc(len+1);piton_memcpy(q,s+i,len);q[len]=0;piton_seq_append(r,(PitonSlot){(long)q,PK_STR});if(j+nl>sl)break;i=j+nl;}return(long)r;}
static long piton_str_strip(const char*s,int mode){usize n=piton_strlen(s),a=0,b=n;if(mode!=2){while(a<n&&piton_ws((unsigned char)s[a]))++a;}if(mode!=1){while(b>a&&piton_ws((unsigned char)s[b-1]))--b;}char*p=piton_alloc(b-a+1);piton_memcpy(p,s+a,b-a);p[b-a]=0;return(long)p;}
static long piton_str_join(const char*sep,PitonSeq*items){usize sl=piton_strlen(sep);usize total=0;long cnt=items?items->length:0;for(long i=0;i<cnt;++i){if(items->items[i].kind!=PK_STR){piton_write(2,"TypeError: sequence item is not a string\n",41);piton_exit(1);}total+=piton_strlen((const char*)items->items[i].bits);}total+=sl*(usize)(cnt>0?cnt-1:0);char*p=piton_alloc(total+1);usize o=0;for(long i=0;i<cnt;++i){if(i){piton_memcpy(p+o,sep,sl);o+=sl;}usize el=piton_strlen((const char*)items->items[i].bits);piton_memcpy(p+o,(const char*)items->items[i].bits,el);o+=el;}p[o]=0;return(long)p;}
/* CHR_NUL_V1: encoded length of a chr() result. strlen stops at NUL, so
 * `chr(0)` measured 0. A chr() result is always exactly one character; the
 * leading byte determines its UTF-8 width, and a leading NUL means chr(0)
 * itself (width 1). */
static long piton_chr_len(const char*s){unsigned char c=(unsigned char)s[0];
  if(c==0)return 1;if(c<0x80)return 1;if(c<0xE0)return 2;if(c<0xF0)return 3;return 4;}
static long piton_str_index(const char*s,long i){long n=(long)piton_strlen(s);if(i<0)i+=n;if(i<0||i>=n){piton_write(2,"IndexError\n",11);piton_exit(1);}char*p=piton_alloc(2);p[0]=s[i];p[1]=0;return(long)p;}
static long piton_str_slice(const char*s,long lo,long hi){long n=(long)piton_strlen(s);if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;char*p=piton_alloc((usize)(hi-lo)+1);for(long i=0;i<hi-lo;++i)p[i]=s[lo+i];p[hi-lo]=0;return(long)p;}
static PitonSlot piton_seq_pop(PitonSeq*s,long i){if(!s||s->length<=0){piton_write(2,"IndexError: pop from empty list\n",32);piton_exit(1);}if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError: pop index out of range\n",35);piton_exit(1);}PitonSlot v=s->items[i];for(long j=i;j+1<s->length;++j)s->items[j]=s->items[j+1];--s->length;return v;}
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
static long piton_range_get(PitonRange*r,long i){long n=piton_range_len(r);
  if(i<0)i+=n;if(i<0||i>=n){piton_write(2,"IndexError\n",11);piton_exit(1);}
  return r->start+i*r->step;}
static long piton_range_contains(PitonRange*r,long v){if(!r)return 0;
  long d=v-r->start;if(r->step>0){if(v<r->start||v>=r->stop)return 0;}else{if(v>r->start||v<=r->stop)return 0;}
  return d%r->step==0;}
static long piton_range_eq(PitonRange*a,PitonRange*b){if(!a||!b)return a==b;
  long na=piton_range_len(a),nb=piton_range_len(b);if(na!=nb)return 0;
  if(na==0)return 1;return a->start==b->start&&a->step==b->step;}
static void piton_range_print(PitonRange*r){if(!r){piton_write(1,"range(0, 0)",11);return;}
  piton_write(1,"range(",6);piton_write_int(r->start);piton_write(1,", ",2);
  piton_write_int(r->stop);
  if(r->step!=1){piton_write(1,", ",2);piton_write_int(r->step);}
  piton_write(1,")",1);}
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
typedef struct{long magic;long kind;void*raw;long index;}PitonAnyIterator;
static long piton_iterator_new_any(void*raw,long kind){if(!raw||(kind!=PK_LIST&&kind!=PK_TUPLE&&kind!=PK_DICT&&kind!=PK_SET&&kind!=PK_RANGE)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonAnyIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->raw=raw;i->index=0;i->kind=kind;return(long)i;}
static long piton_iterator_next_any(long raw){PitonAnyIterator*i=(PitonAnyIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}long n=0;if(i->kind==PK_LIST||i->kind==PK_TUPLE)n=((PitonSeq*)i->raw)->length;else if(i->kind==PK_DICT)n=((PitonDict*)i->raw)->length;else if(i->kind==PK_RANGE)n=piton_range_len((PitonRange*)i->raw);else if(i->kind==PK_SET)n=((PitonSet*)i->raw)->length;else{piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}if(i->index>=n){piton_raise_set("StopIteration","");return 0;}PitonSlot v;if(i->kind==PK_LIST||i->kind==PK_TUPLE)v=((PitonSeq*)i->raw)->items[i->index++];else if(i->kind==PK_DICT)v=((PitonDict*)i->raw)->items[i->index++].key;else if(i->kind==PK_RANGE){PitonRange*rr=(PitonRange*)i->raw;v.bits=rr->start+(i->index++)*rr->step;v.kind=PK_INT;}else v=((PitonSet*)i->raw)->items[i->index++];return v.bits;}
typedef struct{long magic;const char*str;long index;long length;}PitonStrIterator;
static long piton_str_iterator_new(const char*str){if(!str){piton_write(2,"TypeError: 'NoneType' object is not iterable\n",42);piton_exit(1);}PitonStrIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E53545249LL;i->str=str;i->index=0;i->length=piton_strlen(str);return(long)i;}
static long piton_str_iterator_next(long raw){PitonStrIterator*i=(PitonStrIterator*)raw;if(!i||i->magic!=0x5049544E53545249LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->length){piton_raise_set("StopIteration","");return 0;}unsigned char c=i->str[i->index++];char*p=piton_alloc(2);p[0]=(char)c;p[1]=0;return(long)p;}
typedef struct{long magic;long index;long start;PitonSeq*seq;}PitonEnumerateIterator;
static long piton_enumerate_new(PitonSlot src,long start){PitonSeq*s=(PitonSeq*)src.bits;if(src.kind==PK_STR){/* ENUM_STR_V1: enumerate() sobre un str explota 1-char */const char*txt=(const char*)src.bits;usize n=piton_strlen(txt);PitonSeq*l=piton_seq_new(PK_LIST,0);for(usize i=0;i<n;++i){char*q=piton_alloc(2);q[0]=txt[i];q[1]=0;piton_seq_append(l,(PitonSlot){(long)q,PK_STR});}s=l;}if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: enumerate() argument is not iterable\n",48);piton_exit(1);}PitonEnumerateIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->start=start;return(long)i;}
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
static long piton_sorted_new(void*raw){PitonSeq*s=(PitonSeq*)raw;if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: sorted() argument is not iterable\n",46);piton_exit(1);}PitonSeq*r=piton_seq_new(PK_LIST,s->length);for(long i=0;i<s->length;++i)r->items[i]=s->items[i];for(long i=1;i<r->length;++i){PitonSlot v=r->items[i];long j=i;while(j>0){int l=piton_slot_less(v,r->items[j-1]);if(l<0){piton_write(2,"TypeError: '<' not supported between incompatible types\n",56);piton_exit(1);}if(l){r->items[j]=r->items[j-1];--j;}else break;}r->items[j]=v;}return(long)r;}
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
static long piton_closure_new_frame(long addr,long n_args,long n_cells,long*cells,long has_vararg){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;c->has_vararg=has_vararg;c->cells=piton_alloc((usize)(n_cells?n_cells:1)*sizeof(long));for(long i=0;i<n_cells;++i)c->cells[i]=cells[i];return(long)c;}
static long piton_closure_call_frame(long callee,long argc,long*args){if(!piton_callable_value(callee)){piton_raise_not_callable();}if(callee&&((long*)callee)[0]==PITON_BOUND_METHOD_MAGIC){PitonBoundMethod*m=(PitonBoundMethod*)callee;if(argc!=m->n_args||argc>3){piton_write(2,"TypeError: bound method called with wrong number of arguments\n",61);piton_exit(2);}long a[4]={m->self,0,0,0};for(long i=0;i<argc;++i)a[i+1]=args[i];return((long(*)(long,long,long,long))m->addr)(a[0],a[1],a[2],a[3]);}PitonClosure*c=(PitonClosure*)callee;if(!c||c->magic!=PITON_CLOSURE_MAGIC){if(!piton_is_code_addr(callee)){piton_raise_not_callable();}if(argc>4){piton_write(2,"TypeError: native call exceeds four direct arguments\n",52);piton_exit(2);}long a[4]={0,0,0,0};for(long i=0;i<argc;++i)a[i]=args[i];return((long(*)(long,long,long,long))callee)(a[0],a[1],a[2],a[3]);}if(argc<c->n_args||(!c->has_vararg&&argc!=c->n_args)){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long extra=c->has_vararg&&argc>c->n_args?argc-c->n_args:0;long total=c->n_cells+c->n_args+(c->has_vararg?1:0);long*frame=piton_alloc((usize)total*sizeof(long));for(long i=0;i<c->n_cells;++i)frame[i]=c->cells[i];for(long i=0;i<c->n_args;++i)frame[c->n_cells+i]=args[i];if(c->has_vararg){PitonSeq*t=piton_seq_new(PK_TUPLE,extra);for(long i=0;i<extra;++i)t->items[i]=(PitonSlot){args[c->n_args+i],PK_INT};frame[c->n_cells+c->n_args]=(long)t;}return((long(*)(long*))c->addr)(frame);}
static long piton_callback_invoke(long callback,long value){long args[1]={value};return piton_closure_call_frame(callback,1,args);}
static long piton_frame_call(long addr,long argc,long*args){long*frame=piton_alloc((usize)argc*sizeof(long));for(long i=0;i<argc;++i)frame[i]=args[i];return((long(*)(long*))addr)(frame);}
static void piton_bigint_print_raw(void*a);
static void piton_print_slot(PitonSlot v);
static void piton_print_seq(PitonSeq*s){piton_write(1,s->kind==PK_TUPLE?"(":"[",1);for(long i=0;i<s->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(s->items[i]);}if(s->kind==PK_TUPLE&&s->length==1)piton_write(1,",",1);piton_write(1,s->kind==PK_TUPLE?")":"]",1);}
static void piton_print_dict(PitonDict*d){piton_write(1,"{",1);for(long i=0;i<d->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(d->items[i].key);piton_write(1,": ",2);piton_print_slot(d->items[i].value);}piton_write(1,"}",1);}
static void piton_print_set(PitonSet*s){piton_write(1,"{",1);for(long i=0;i<s->length;++i){if(i)piton_write(1,", ",2);piton_print_slot(s->items[i]);}piton_write(1,"}",1);}
static void piton_print_slot(PitonSlot v){switch(v.kind){case PK_NONE:piton_write(1,"None",4);break;case PK_BOOL:piton_write(1,v.bits?"True":"False",v.bits?4:5);break;case PK_INT:piton_write_int(v.bits);break;case PK_FLOAT:{char r[PITON_REPR_MAX];int n=piton_repr_double(r,(unsigned long long)v.bits);piton_write(1,r,(usize)n);break;}case PK_STR:piton_write(1,"'",1);piton_write(1,(const char*)v.bits,piton_strlen((const char*)v.bits));piton_write(1,"'",1);break;case PK_LIST:case PK_TUPLE:piton_print_seq((PitonSeq*)v.bits);break;case PK_DICT:piton_print_dict((PitonDict*)v.bits);break;case PK_SET:piton_print_set((PitonSet*)v.bits);break;case PK_BIGINT:piton_bigint_print_raw((void*)v.bits);break;case PK_RANGE:piton_range_print((PitonRange*)v.bits);break;default:piton_write(1,"<object>",8);}}
/* COMP_DICT_ITER_V1: the i-th KEY of a dict / the i-th element of a set,
 * for a comprehension's index loop. Explicit subscripts keep using
 * piton_dict_get, which raises KeyError as CPython does. */
static PitonSlot piton_dict_nth_key(PitonDict*d,long i){if(!d||i<0||i>=d->length)piton_write(2,"IndexError\n",10),piton_exit(1);return d->items[i].key;}
static PitonSlot piton_set_nth(PitonSet*s,long i){if(!s||i<0||i>=s->length)piton_write(2,"IndexError\n",10),piton_exit(1);return s->items[i];}
static long piton_sum_seq(PitonSeq*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static long piton_sum_dict(PitonDict*d){long r=0;for(long i=0;i<d->length;++i)r+=d->items[i].key.bits;return r;}
static long piton_sum_set(PitonSet*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static const char*piton_type_repr(int kind){switch(kind){case PK_NONE:return"<class 'NoneType'>";case PK_BOOL:return"<class 'bool'>";case PK_INT:return"<class 'int'>";case PK_FLOAT:return"<class 'float'>";case PK_STR:return"<class 'str'>";case PK_LIST:return"<class 'list'>";case PK_TUPLE:return"<class 'tuple'>";case PK_DICT:return"<class 'dict'>";case PK_SET:return"<class 'set'>";case PK_RANGE:return"<class 'range'>";default:return"<class 'object'>";}}
/* M14 BUILTINS_CORE_V2 (matriz declarada; ver native_runtime.c) */
static int piton_slot_truthy(PitonSlot v){switch(v.kind){case PK_NONE:return 0;case PK_BOOL:return v.bits?1:0;case PK_INT:return v.bits!=0;case PK_FLOAT:return piton_bits_double(v.bits)!=0.0;case PK_STR:return v.bits&&((const char*)v.bits)[0]!=0;case PK_LIST:case PK_TUPLE:return ((PitonSeq*)v.bits)->length>0;case PK_RANGE:return piton_range_len((PitonRange*)v.bits)>0;default:return v.bits!=0;}}
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
static long piton_dict_keys(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i)s->items[i]=d->items[i].key;return(long)s;}
static long piton_dict_values(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i)s->items[i]=d->items[i].value;return(long)s;}
static long piton_dict_items(PitonDict*d){PitonSeq*s=piton_seq_new(PK_LIST,d?d->length:0);if(d)for(long i=0;i<d->length;++i){PitonSeq*p=piton_seq_new(PK_TUPLE,2);p->items[0]=d->items[i].key;p->items[1]=d->items[i].value;s->items[i]=(PitonSlot){(long)p,PK_TUPLE};}return(long)s;}
static long piton_range_slice(PitonRange*r,long lo,long hi){if(!r)return 0;long n=piton_range_len(r);if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;return(long)piton_range_new(r->start+lo*r->step,r->start+hi*r->step,r->step);}
static long piton_range_slice_step(PitonRange*r,long lo,long hi,long st){if(!r)return 0;if(st==0){piton_raise_set("ValueError","slice step cannot be zero");return 0;}long n=piton_range_len(r);long a=0,b=0;if(st>0){if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=0;if(hi==0x7FFFFFFFFFFFFFFFL)hi=n;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo<0)lo=0;if(hi>n)hi=n;if(hi<lo)hi=lo;a=lo;b=hi;}else{if(lo==(-0x7FFFFFFFFFFFFFFFL-1))lo=n-1;if(hi==0x7FFFFFFFFFFFFFFFL)hi=-n-1;if(lo<0)lo+=n;if(hi<0)hi+=n;if(lo>=n)lo=n-1;if(hi<-1)hi=-1;a=lo;b=hi;}return(long)piton_range_new(r->start+a*r->step,r->start+b*r->step,r->step*st);}
static long piton_divmod_xy(long a,long b){if(!b){piton_raise_set("ZeroDivisionError","integer division or modulo by zero");return 0;}long q=a/b,r=a%b;if(r&&((r<0)!=(b<0))){q-=1;r+=b;}PitonSeq*p=piton_seq_new(PK_TUPLE,2);p->items[0]=(PitonSlot){q,PK_INT};p->items[1]=(PitonSlot){r,PK_INT};return(long)p;}
static PitonSlot piton_str_minmax(const char*s,int want_min){if(!s||!*s){piton_raise_set("ValueError","min() arg is an empty sequence");return (PitonSlot){0,PK_STR};}unsigned char best=(unsigned char)s[0];for(long i=1;s[i];++i){unsigned char c=(unsigned char)s[i];if(want_min?c<best:c>best)best=c;}char*r=piton_alloc(2);r[0]=(char)best;r[1]=0;return (PitonSlot){(long)r,PK_STR};}
static long piton_collect(int which,long raw,int elemkind){PitonSeq*s=piton_seq_new(PK_LIST,0);if(!s)return 0;for(;;){long v;if(which==0)v=piton_enumerate_next(raw);else if(which==1)v=piton_reversed_next(raw);else if(which==2)v=piton_callback_iterator_next(raw);else if(which==3)v=piton_zip_next(raw);else if(which==4)v=piton_gen_next(raw);else{piton_raise_set("TypeError","collect");return 0;}if(piton_exc_flag){if(piton_exc_type&&piton_strcmp(piton_exc_type,"StopIteration")==0){piton_catch_clear();break;}return 0;}piton_seq_append(s,(PitonSlot){v,elemkind});}return(long)s;}
static PitonSeq* piton_collect_gen(long raw){return (PitonSeq*)piton_collect(4,raw,PK_INT);}
static PitonSeq* piton_str_explode(const char*s){usize n=piton_strlen(s);PitonSeq*r=piton_seq_new(PK_LIST,n);for(usize i=0;i<n;++i){char*q=piton_alloc(2);q[0]=s[i];q[1]=0;piton_seq_put(r,(long)i,(PitonSlot){(long)q,PK_STR});}return r;}
static void piton_report_unhandled(void){if(piton_exc_cause_type){piton_write(2,piton_exc_cause_type,piton_strlen(piton_exc_cause_type));if(piton_exc_cause_msg&&piton_exc_cause_msg[0]){piton_write(2,": ",2);piton_write(2,piton_exc_cause_msg,piton_strlen(piton_exc_cause_msg));}piton_write(2," -> causada por\n",16);}piton_write(2,piton_exc_type,piton_strlen(piton_exc_type));piton_write(2,": ",2);if(piton_exc_message)piton_write(2,piton_exc_message,piton_strlen(piton_exc_message));piton_write(2,"\n",1);}
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
        self._tuple_list_elems: dict[str, tuple[str, str]] = {}
        self._iter_source: dict[str, str] = {}
        self._enum_elem: dict[str, str] = {}
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

    def emit(self, module: MIRModule) -> str:
        self.mir_module = module
        self.classes = getattr(module, "classes", {})
        self.class_parents = getattr(module, "class_parents", {})
        self.class_mro = getattr(module, "class_mro", {})
        self.class_properties = getattr(module, "class_properties", {})
        self._module_funcs = list(module.functions)
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
        self._has_bigint = any(
            instruction.op == "const" and instruction.args
            and isinstance(instruction.args[0], int) and abs(instruction.args[0]) > 9223372036854775807
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        ) or self._const_fold_overflows(module) or self._uses_float_mod(module) or _prints_collection
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
        self._cur_slots = slots
        locals_ = sorted((slots if function.frame_abi else slots - set(function.params)) - self._shared_globals)
        lines = [signature + " {"]
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
        if vtype in {"int", "bool"}:
            return f"({raw}!=0)"
        if vtype == "float":
            return f"(piton_bits_double({raw})!=0.0)"
        if vtype == "none":
            return "0"
        if vtype == "str":
            return f"(piton_strlen((const char*){raw})>0)"
        if vtype == "slot":
            # BOOL_SLOT_V1: mixed-type operands of `y`/`o` are tagged slots;
            # their truthiness is the per-kind CPython truth table.
            return f"(piton_slot_truthy(*(PitonSlot*){raw}))"
        if vtype in {"list", "tuple"}:
            return f"(((PitonSeq*){raw})->length>0)"
        if vtype == "dict":
            return f"(((PitonDict*){raw})->length>0)"
        if vtype == "set":
            return f"(((PitonSet*){raw})->length>0)"
        if vtype == "range":
            return f"(piton_range_len((PitonRange*){raw})>0)"
        if strict:
            raise NativeBuildError(f"Linux truth test does not support {vtype}")
        return f"({raw})"

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
            "bigint": "PK_BIGINT", "range": "PK_RANGE",
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
        fields: list[tuple[int, str, str]] = []
        i, n = 0, len(template)
        auto_idx = 0
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
                if idx_str == "":
                    idx = auto_idx
                    auto_idx += 1
                    token = "{}"
                else:
                    try:
                        idx = int(idx_str)
                    except ValueError:
                        raise NativeBuildError(
                            f"str.format(): named placeholders not supported: {{{content}}}"
                        )
                    if idx < 0:
                        raise NativeBuildError(
                            f"str.format(): negative field index {idx} is not supported"
                        )
                    token = "{" + idx_str + "}"
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
                fields.append((idx, spec, token))
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
        return "".join(out), fields

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
        if i != n:
            raise NativeBuildError(f"str.format(): unsupported format spec {pres!r}")

    @staticmethod
    def _format_spec_presentation(spec: str) -> str:
        """FMT_SPEC_V1: strip the type char from a format spec, leaving the
        presentation part (fill/align/sign/zero/width/comma/precision)."""
        if not spec:
            return ""
        t = spec[-1]
        if t in "dxXobfeEgGFgn%":
            return spec[:-1]
        return spec

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

    def _emit_user_function_call(self, out: list[str], result: Any, function_name: str,
                                values: list[Any], call_handler: Any,
                                function: MIRFunction, types: dict[str, str]) -> list[str]:
        """Call a user-defined function directly. Extracted so it can run
        BEFORE the builtin chain: a user definition SHADOWS a builtin of the
        same name (`funcion suma(a, b)` is not `sum`)."""
        _gen_layout = self.generator_layouts.get(function_name)
        if _gen_layout is not None:
            _params = self.function_params.get(function_name, [])
            if len(values) != len(_params):
                raise NativeBuildError(
                    f"native generator '{function_name}' called with wrong number of arguments"
                )
            if len(values) > 4:
                raise NativeBuildError("native generator calls with more than four arguments are not supported yet")
            out.append(f"    {_name(result)}=piton_gen_new((long)&{_name(function_name)},{len(_gen_layout)});")
            for _arg, _param in zip(values, _params):
                out.append(f"    ((PitonGenerator*){_name(result)})->slots[{_gen_layout[_param]}]={self._value(_arg)};")
            types[result] = "generator"
            return out
        _params = self.function_params.get(function_name, [])
        _defaults = self.function_defaults.get(function_name, [])
        # function_defaults is a full-length list with None holes for params
        # without a default; required = count of those holes (CPython forbids
        # a required parameter after a defaulted one, so holes are leading).
        _min_args = max(0, sum(1 for d in _defaults if d is None))
        if not (_min_args <= len(values) <= len(_params)):
            raise NativeBuildError(
                f"native call to '{function_name}' passes {len(values)} argument(s) "
                f"but the function takes {_min_args}..{len(_params)}"
            )
        values = self._complete_call_args(function_name, list(values))
        encoded_values = ",".join(self._value(value) for value in values)
        out.append(f"    {_name(result)}={_name(function_name)}({encoded_values});")
        self._emit_exc_check(out, function, call_handler)
        types[result] = self.function_return_types.get(function_name, "int")
        return out

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
            elif method == "copy":
                require_count((0,), "no arguments")
                out.append(f"    {_name(result)}=piton_seq_copy((PitonSeq*){operand});")
                types[result] = coll_type
                _ek = self._coll_elems.get(obj)
                if _ek is not None:
                    self._coll_elems[result] = _ek
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
                    raise NativeBuildError(
                        "native dict.get requires a statically known value type"
                    )
                if default_type is not None and default_type != val_type:
                    raise NativeBuildError(
                        f"native dict.get default of type '{default_type}' cannot be "
                        f"unioned with values of type '{val_type}' in the untagged model"
                    )
                types[result] = val_type
            elif method == "update":
                # DICT_UPDATE_V1: merge otro dict (V1: solo dict -> dict);
                # CPython args iterable/kwargs quedan fuera.
                require_count((1,), "one argument (a dict)")
                if types.get(call_args[0], "") != "dict":
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
        elif coll_type == "set":
            if method == "add":
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
            if len(call_args) not in {0, 1}:
                raise NativeBuildError(f"Linux str.{method}() requires no or one argument")
            mode = {"strip": 0, "lstrip": 1, "rstrip": 2}[method]
            if call_args and types.get(call_args[0]) == "str":
                out.append(f"    {_name(result)}=(long)piton_str_strip_chrs((const char*){operand},{self._value(call_args[0])},{mode});")
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
        elif method == "swapcase":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_swapcase((const char*){operand});")
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
                names = ",".join(f"_pf{index}" for index in range(len(call_args))) or "_pf0"
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
            simplified, fields = self._parse_format_template(template)
            # a field may be referenced twice ({0} {0}), so the count is not
            # an equality: automatic numbering may not exceed the arguments,
            # and explicit indices are bounds-checked below.
            if len(fields) > len(call_args) and any(f[2] == "{}" for f in fields):
                raise NativeBuildError(
                    f"Linux str.format(): {len(fields)} placeholder(s) but {len(call_args)} argument(s)"
                )
            # the _pfN array is indexed by CALL position (the simplified
            # template keeps explicit {N} tokens), so each call index is
            # converted once with the spec of the field that references it.
            by_index: dict[int, tuple[Any, str]] = {}
            for arg_idx, spec, _token in fields:
                if arg_idx in by_index:
                    continue
                if arg_idx < 0 or arg_idx >= len(call_args):
                    raise NativeBuildError(
                        f"str.format(): field index {arg_idx} out of range "
                        f"for {len(call_args)} argument(s)"
                    )
                by_index[arg_idx] = (call_args[arg_idx], spec)
            pieces = []
            for index in range(len(call_args)):
                value = call_args[index]
                if index in by_index:
                    spec = by_index[index][1]
                else:
                    spec = ""
                arg_type = types.get(value, "int")
                type_char = self._format_spec_type(spec)
                pres = self._format_spec_presentation(spec)
                self._validate_presentation_spec(pres)
                # convert to target type string
                if type_char in {"x", "X", "o"}:
                    base = {"x": 16, "X": 16, "o": 8}[type_char]
                    if arg_type not in {"int", "bool"}:
                        raise NativeBuildError(f"Linux str.format() %{type_char} requires an int, not {arg_type}")
                    converted = f"piton_str_from_int_base({self._value(value)},{base},{1 if type_char == 'X' else 0})"
                elif type_char == "b":
                    if arg_type not in {"int", "bool"}:
                        raise NativeBuildError(f"Linux str.format() %b requires an int, not {arg_type}")
                    # binary without "0b" prefix (CPython {:b} is bare digits)
                    converted = f"piton_str_from_int_base({self._value(value)},2,0)"
                elif type_char == "d":
                    if arg_type in {"int", "bool"}:
                        converted = f"piton_str_from_int({self._value(value)})"
                    elif arg_type == "float":
                        converted = f"piton_str_from_int((long)piton_bits_double({self._value(value)}))"
                    else:
                        raise NativeBuildError(f"Linux str.format() %d requires a real number, not {arg_type}")
                elif type_char in {"f", "e", "E", "g", "G"}:
                    # FMT_FLOAT_V1: real decimal-based rounding for float specs.
                    prec = 6
                    if "." in pres:
                        try:
                            prec = int(pres.split(".")[1])
                        except ValueError:
                            prec = 6
                    _foldv = f"({self._value(value)})" if arg_type == "float" else f"piton_double_bits((double){self._value(value)})"
                    if type_char in {"f"}:
                        converted = f"piton_float_fmt_fixed({_foldv},{prec})"
                    elif type_char in {"e", "E"}:
                        converted = f"piton_float_fmt_exp({_foldv},{prec},{1 if type_char == 'E' else 0})"
                    else:
                        converted = f"piton_float_fmt_g({_foldv},{1 if type_char == 'G' else 0})"
                elif type_char == "%":
                    _foldv = f"({self._value(value)})" if arg_type == "float" else f"piton_double_bits((double){self._value(value)})"
                    prec = 6
                    if "." in pres:
                        try:
                            prec = int(pres.split(".")[1])
                        except ValueError:
                            prec = 6
                    converted = f"piton_float_fmt_pct({_foldv},{prec})"
                else:
                    # no type char: use the natural string conversion
                    if arg_type == "int":
                        converted = f"piton_str_from_int({self._value(value)})"
                    elif arg_type == "float":
                        converted = f"piton_str_from_float({self._value(value)})"
                    elif arg_type == "bool":
                        converted = f'{self._value(value)}?"True":"False"'
                    elif arg_type == "none":
                        converted = '"None"'
                    elif arg_type == "str":
                        converted = f"(const char*){self._value(value)}"
                    else:
                        raise NativeBuildError(f"Linux str.format() does not support {arg_type} arguments")
                # apply presentation spec
                if pres:
                    _presented = pres.split(".", 1)[0] if type_char in {"f", "e", "E", "g", "G", "%"} else pres
                    if _presented:
                        pieces.append(f"const char*_pf{index}=(const char*)piton_str_apply_spec((const char*){converted},{json.dumps(_presented)});")
                    else:
                        pieces.append(f"const char*_pf{index}=(const char*){converted};")
                else:
                    pieces.append(f"const char*_pf{index}=(const char*){converted};")
            if not call_args:
                # no placeholders: pass a one-element dummy array (the
                # engine never indexes it; n=0) so the C declaration is valid
                pieces.append('const char*_pf0="";')
            names = ",".join(f"_pf{index}" for index in range(len(call_args))) or "_pf0"
            out.append(f"    {{{''.join(pieces)}const char*_fa[]={{{names}}}; {_name(result)}=(long)piton_str_format((const char*){json.dumps(simplified)},{len(call_args)},_fa);}}")
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
        elif method == "swapcase":
            require_count(0, "no arguments")
            out.append(f"    {_name(result)}=(long)piton_str_swapcase((const char*){operand});")
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
            raise NativeBuildError(f"Linux str.{method}() is not supported")

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
        self._deleted_names = set()
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
                if source_type.startswith("iterator:"):
                    self._iter_source[result] = source
                    if source in self._iter_source_r:
                        self._iter_source_r[result] = self._iter_source_r[source]
                return out
            elif source_type == "str":
                out.append(f"    {_name(result)}=piton_str_iterator_new((char*){self._value(source)});")
                types[result] = "iterator:str"
            else:
                iterator_kind = {"list": "PK_LIST", "tuple": "PK_TUPLE", "dict": "PK_DICT", "set": "PK_SET", "range": "PK_RANGE"}.get(source_type)
                if iterator_kind is None:
                    raise NativeBuildError("Linux iter requires a native collection or user __iter__")
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
            elif builtin == "enumerate":
                if types.get(source) not in {"list", "tuple", "str"}:
                    raise NativeBuildError("native enumerate currently requires a list or tuple")
                start_value = self._value(start) if start is not None else "0"
                out.append(f"    {_name(result)}=piton_enumerate_new({self._slot(source, types)},{start_value});")
                if types.get(source) == "str":
                    # ENUM_STR_V1: el runtime explota el str; el elemento
                    #-yielded sigue siendo str (no un puntero suelto).
                    self._enum_elem[result] = "str"
                self._iter_source[result] = source
            elif builtin == "reversed":
                if types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native reversed currently requires a list or tuple")
                out.append(f"    {_name(result)}=piton_reversed_new((void*){self._value(source)});")
            elif builtin == "zip":
                left, right = source
                if types.get(left) not in {"list", "tuple"} or types.get(right) not in {"list", "tuple"}:
                    raise NativeBuildError("native zip currently requires two lists or tuples")
                out.append(f"    {_name(result)}=piton_zip_new((void*){self._value(left)},(void*){self._value(right)});")
                self._iter_source[result] = left
                self._iter_source_r[result] = right
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
            if source in self._deleted_names:
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
            if source in self._chr_results:
                self._chr_results.add(result)
            if source in self._dict_val_types:
                self._dict_val_types[result] = self._dict_val_types[source]
            if source in self.function_names:
                out.append(f"    {_name(result)}=(long)&{_name(source)};")
                return out
            if source in _BUILTINS and source not in function.params and source not in _FUNC_ONLY_BUILTINS:
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
                if source in self._dict_val_types:
                    self._dict_val_types[result] = self._dict_val_types[source]
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
                and source not in getattr(self, "_known_names", set())
                and source not in {"Verdadero", "Falso", "Nada", "Verdadera", "Falsa", "Ninguno"}
                and source not in (self._func_only_builtins or _FUNC_ONLY_BUILTINS)
            ):
                raise NativeBuildError(f"undefined name '{source}' in native subset")
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
                if args[1] in self._dict_key_types:
                    self._dict_key_types[args[0]] = self._dict_key_types[args[1]]
                return out
            out.append(f"    {_name(args[0])}={self._value(args[1])};")
            types[args[0]] = types.get(args[1], "int")
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
            if operator == "!":
                # TRUTHY_FIX_V1: `no X` is not !(raw value) — a non-null
                # pointer (empty str/list/dict/set) is always truthy that
                # way, so `no ''` and `no []` printed False instead of
                # True. Route through the same per-kind truthiness as
                # truth_test, negated (also opens float `no`, previously
                # rejected).
                operand = args[1]
                vtype = types.get(operand, "int")
                if vtype in {"int", "bool"}:
                    out.append(f"    {_name(result)}=({self._value(operand)}==0);")
                elif vtype == "float":
                    out.append(f"    {_name(result)}=(piton_bits_double({self._value(operand)})==0.0);")
                elif vtype == "none":
                    out.append(f"    {_name(result)}=1;")
                elif vtype == "str":
                    out.append(f"    {_name(result)}=(piton_strlen((const char*){self._value(operand)})==0);")
                elif vtype in {"list", "tuple"}:
                    out.append(f"    {_name(result)}=(((PitonSeq*){self._value(operand)})->length==0);")
                elif vtype == "dict":
                    out.append(f"    {_name(result)}=(((PitonDict*){self._value(operand)})->length==0);")
                elif vtype == "set":
                    out.append(f"    {_name(result)}=(((PitonSet*){self._value(operand)})->length==0);")
                elif vtype == "range":
                    # RANGE_VALUE_V1: `no rango(0)` is True.
                    out.append(f"    {_name(result)}=(piton_range_len((PitonRange*){self._value(operand)})==0);")
                else:
                    raise NativeBuildError(f"Linux truth test does not support {vtype}")
                types[result] = "bool"
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
                        elif spec in {"f", "F", "e", "E", "g", "G"}:
                            precv = prec if prec >= 0 else 6
                            if arg_type not in {"float", "int", "bool"}:
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
            else:
                raise NativeBuildError(f"Linux binary operator not supported: {operator}")
            out.append(f"    {_name(result)}={expression};")
            types[result] = "int"
        elif op == "compare":
            operator, left, right = args
            if operator in {"es", "no es"}:
                # IS_IDENTITY_V1: `es` is IDENTITY, not equality. The raw C `==`
                # answered True for `1 es Verdadero` (both are 1) where CPython
                # says False. Compare raw bits: for the untagged model that is
                # identity for the value types it can represent, and the
                # differing static types are identity-different anyway.
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
                                    negate=(operator == "no en"))
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
                raise NativeBuildError(
                    f"Linux ordering comparison '{operator}' between '{left_type}' and "
                    f"'{right_type}' is not supported (CPython raises TypeError: "
                    f"'{operator}' not supported between instances of 'range' and ...)"
                )
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
            else:
                if operator in {"<", ">", "<=", ">="} and not self._ordering_pair_ok(left_type, right_type):
                    # ORDER_MIXED_TYPES_V1: refuse instead of comparing the raw
                    # representations of two incomparable operands.
                    raise NativeBuildError(
                        f"Linux ordering comparison '{operator}' between '{left_type}' and "
                        f"'{right_type}' is not supported (CPython raises TypeError: "
                        f"'{operator}' not supported between instances of these types)"
                    )
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
            call_handler = args[2] if len(args) > 2 else None
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
                arg_type = types.get(values[0], "int")
                # ABS_TYPE_V1: `abs` of a non-numeric operand is a TypeError in
                # CPython; the else branch treated any non-float as an integer
                # and read a string/collection POINTER as a long, so
                # `abs('a')` printed an address and `abs(Nada)` answered 0.
                if arg_type not in {"int", "bool", "float"}:
                    raise NativeBuildError(
                        f"Linux abs of '{arg_type}' is not supported "
                        "(CPython raises TypeError: bad operand type for abs())"
                    )
                if arg_type == "float":
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
            elif function_name == "divmod":
                if len(values) != 2:
                    raise NativeBuildError("Linux divmod requires two arguments")
                if types.get(values[0], "int") not in {"int", "bool"} or types.get(values[1], "int") not in {"int", "bool"}:
                    raise NativeBuildError("Linux divmod requires int arguments")
                out.append(f"    {_name(result)}=piton_divmod_xy({self._value(values[0])},{self._value(values[1])});")
                types[result] = "tuple"
                self._coll_elems[result] = "int"
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
                    if function_name == "chr":
                        self._chr_results.add(result)
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
                if len(values) == 1 and types.get(values[0]) in {"list", "tuple", "str"}:
                    # MINMAX_ITER_V1: min()/max() of a single iterable. The
                    # element type rides along so the result prints correctly;
                    # heterogeneous or empty inputs raise through the helper.
                    # A str is exploded to its 1-char list first (a raw char*
                    # is not a PitonSeq*).
                    want_min = 1 if function_name == "min" else 0
                    if types.get(values[0]) == "str":
                        out.append(f"    {{PitonSlot _mm=piton_str_minmax((const char*){self._value(values[0])},{want_min});")
                    else:
                        out.append(f"    {{PitonSlot _mm=piton_seq_minmax((PitonSeq*){self._value(values[0])},{want_min});")
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
                if len(values) != 1:
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
                struct_map = {"list": "PitonSeq", "tuple": "PitonSeq", "dict": "PitonDict", "set": "PitonSet"}
                helper_map = {"list": "piton_sum_seq", "tuple": "piton_sum_seq", "dict": "piton_sum_dict", "set": "piton_sum_set"}
                if value_type not in struct_map:
                    raise NativeBuildError("Linux sum requires one collection")
                # COLL_ELEM_TYPE_V1: the helper adds each element's raw bits, so
                # a bigint element contributed its POINTER and sum printed an
                # address (`sum([10 ** 20])` -> 4210752). Refuse anything whose
                # element type is not int instead of accumulating garbage.
                elem_type = self._coll_elems.get(values[0], "int")
                kinds = set(elem_type.split("|")) if elem_type else {"int"}
                if kinds != {"int"}:
                    elem_type = "|".join(sorted(kinds))
                    raise NativeBuildError(
                        f"Linux sum over '{elem_type}' elements is not supported "
                        "(CPython sums them; the native helper only adds int bits)"
                    )
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
                if source_type == "generator" and seq_kind == "PK_LIST":
                    # CONV_GEN_V1: lista(generador) materializa en el runtime.
                    out.append(f"    {_name(result)}=piton_collect_gen({self._value(values[0])});")
                    types[result] = "list"
                    self._coll_elems[result] = "int"
                    return out
                if source_type in {"iterator:enumerate", "iterator:reversed", "iterator:zip", "iterator:map", "iterator:filter"} and seq_kind == "PK_LIST":
                    _which = {"iterator:enumerate": 0, "iterator:reversed": 1, "iterator:map": 2, "iterator:filter": 2, "iterator:zip": 3}[source_type]
                    _elemkind = "PK_TUPLE" if source_type in {"iterator:enumerate", "iterator:zip"} else "PK_INT"
                    out.append(f"    {_name(result)}=piton_collect({_which},{self._value(values[0])},{_elemkind});")
                    types[result] = "list"
                    self._coll_elems[result] = "tuple" if source_type in {"iterator:enumerate", "iterator:zip"} else "int"
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
                    out.append(f"    {_name(result)}=(long)piton_type_repr({self._kind(value_type)});")
                types[result] = "str"
            elif function_name in {"sorted", "ordenar"}:
                # SORTED_STR_V1: a str sorts like its list of 1-char strings.
                if len(values) == 1 and types.get(values[0]) == "str":
                    out.append(f"    {_name(result)}=piton_sorted_new((void*)piton_str_explode((const char*){self._value(values[0])}));")
                    types[result] = "list"
                    self._coll_elems[result] = "str"
                    return out
                if len(values) not in {1, 2} or types.get(values[0]) not in {"list", "tuple", "range"}:
                    raise NativeBuildError("native sorted currently requires one list, tuple or range")
                if types.get(values[0]) == "range":
                    out.append(f"    {_name(result)}=piton_sorted_new((void*)piton_conv_seq(PK_LIST,{self._slot(values[0], types)}));")
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
        elif op == "subscript_store":
            collection, index, value = args
            coll_type = types.get(collection, "")
            if coll_type in {"list", "tuple"}:
                if types.get(index, "") not in {"int", "bool"}:
                    raise NativeBuildError("Linux subscript store requires an int index")
                out.append(f"    piton_seq_set((PitonSeq*){self._value(collection)},{self._value(index)},{self._slot(value, types)});")
            elif coll_type in {"dict", "dict:module"}:
                out.append(f"    piton_dict_set((PitonDict*){self._value(collection)},{self._slot(index, types)},{self._slot(value, types)});")
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
            if isinstance(args[0], str):
                types.pop(args[0], None)
                self._deleted_names.add(args[0])
            else:
                for name in args:
                    types.pop(name, None)
                    self._deleted_names.add(name)
            return out
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
                    self._emit_collection_method(out, result, method, obj, list(call_args), coll_type, types, aliases)
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
                _raise_or_propagate(out, function)
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
            elif kind == "set":
                out.append(f'    {_name(result)}=(long)piton_set_new({len(items)});')
                for value in items:
                    out.append(f'    piton_set_add((PitonSet*){_name(result)},{self._slot(value, types)});')
            else:
                raise NativeBuildError(f"Linux collection kind not supported: {kind}")
            types[result] = kind
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
            vtype = types.get(value, "int")
            if vtype in {"list", "tuple"}:
                length = f"((PitonSeq*){self._value(value)})->length"
            elif vtype == "str":
                length = f"(long)piton_strlen((const char*){self._value(value)})"
            else:
                raise NativeBuildError(f"Linux unpack requires a list, tuple or str, not {vtype}")
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
            coll, idx = args
            collection_type = types.get(coll)
            if collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_get((PitonSeq*){self._value(coll)},{self._value(idx)}).bits;')
                # COLL_ELEM_TYPE_V1: an index carries the element kind. Without it
                # `lista('abc')[1]` returned a str pointer in an int slot and
                # printed an address, and `[10 ** 20][0]` printed `<object>`.
                elem_kind = self._coll_elems.get(coll)
                if elem_kind and "|" not in elem_kind:
                    types[result] = elem_kind
                    _tx = self._tuple_list_elems.get(coll) or self._tuple_list_elems.get(aliases.get(coll, coll))
                    if _tx is not None and elem_kind == "tuple":
                        self._tuple_elems[result] = (("%k", _tx[0]), ("%v", _tx[1]))
                    return out
                te = self._tuple_elems.get(coll) or self._tuple_elems.get(aliases.get(coll, coll))
                raw_idx = self._fn_consts.get(idx)
                if raw_idx is None:
                    raw_idx = self._fn_consts.get(aliases.get(idx, idx))
                if te is not None and isinstance(raw_idx, int) and 0 <= raw_idx < len(te):
                    types[result] = te[raw_idx][1]
                    return out
            elif collection_type == "str":
                # PARITY_P2_V1: str[s] yields the one-character string.
                out.append(f'    {_name(result)}=(long)piton_str_index((const char*){self._value(coll)},{self._value(idx)});')
                types[result] = "str"
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
                    return out
                out.append(f'    {_name(result)}=piton_set_nth((PitonSet*){self._value(coll)},{self._value(idx)}).bits;')
                types[result] = self._coll_elems.get(coll, "int")
                return out
            elif collection_type == "range":
                # RANGE_VALUE_V1: indexing is exact, never materialized.
                out.append(f'    {_name(result)}=piton_range_get((PitonRange*){self._value(coll)},{self._value(idx)});')
                types[result] = "int"
                return out
            elif collection_type in {"dict", "dict:module"}:
                out.append(f'    {_name(result)}=piton_dict_get((PitonDict*){self._value(coll)},{self._slot(idx, types)}).bits;')
                # DICT_VAL_TYPE_V1: `d[k]` must carry the dict's value type, or
                # a str/bigint/float value is printed as a raw int slot (an
                # address). Unknown value type keeps the historical int.
                val_type = self._dict_val_types.get(coll)
                if val_type is not None:
                    types[result] = val_type
                    return out
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
                elif collection_type == "range":
                    out.append(f'    {_name(result)}=piton_range_slice((PitonRange*){self._value(coll)},{lower_value},{upper_value});')
                    types[result] = "range"
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
            elif collection_type == "range":
                out.append(f'    {_name(result)}=piton_range_slice_step((PitonRange*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = "range"
            elif collection_type in {"list", "tuple"}:
                out.append(f'    {_name(result)}=piton_seq_slice_step((PitonSeq*){self._value(coll)},{lower_value},{upper_value},{step_value});')
                types[result] = collection_type
            else:
                raise NativeBuildError(f"Linux slice not supported for {collection_type}")
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
            if collection_type.startswith("iterator:") or collection_type in {"generator", "genexpr"}:
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
