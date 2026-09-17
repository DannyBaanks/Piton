"""Backend ELF x86-64 Linux para el subconjunto MIR escalar."""
from __future__ import annotations

import json
import gzip
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .lower import lower_cst_to_hir
from .mir import MIRFunction, MIRInstruction, MIRLoweringError, MIRModule, lower_hir_to_mir
from .parser import parse
from .x86 import NativeBuildError, _BUILTINS, _scan_native_modules, generator_slot_layout


_RICH_FREESTANDING_C = r"""
enum{PK_NONE,PK_BOOL,PK_INT,PK_FLOAT,PK_STR,PK_LIST,PK_TUPLE,PK_DICT,PK_SET,PK_OBJECT,PK_BIGINT};
typedef struct{long bits;int kind;}PitonSlot;
static unsigned char piton_arena[8*1024*1024];
static usize piton_arena_used=0;
static void piton_memzero(void*p,usize n){unsigned char*b=p;for(usize i=0;i<n;++i)b[i]=0;}
static void piton_memcpy(void*d,const void*s,usize n){unsigned char*dd=d;const unsigned char*ss=s;for(usize i=0;i<n;++i)dd[i]=ss[i];}
static void*piton_alloc(usize n){usize p=(piton_arena_used+15)&~15UL;if(n>sizeof(piton_arena)-p){piton_write(2,"MemoryError\n",12);piton_exit(1);}void*r=piton_arena+p;piton_arena_used=p+n;piton_memzero(r,n);return r;}
static PitonSlot piton_slot(long bits,int kind){PitonSlot v={bits,kind};return v;}
static long piton_double_bits(double d){union{double d;unsigned long u;}v={d};return(long)v.u;}
static double piton_bits_double(long bits){union{double d;unsigned long u;}v;v.u=(unsigned long)bits;return v.d;}
static long piton_float_add(long a,long b){return piton_double_bits(piton_bits_double(a)+piton_bits_double(b));}
static long piton_float_sub(long a,long b){return piton_double_bits(piton_bits_double(a)-piton_bits_double(b));}
static long piton_float_mul(long a,long b){return piton_double_bits(piton_bits_double(a)*piton_bits_double(b));}
static long piton_float_neg(long a){return(long)((unsigned long)a^(1UL<<63));}
static long piton_float_sqrt(long a){double x=piton_bits_double(a),r;__asm__ volatile("sqrtsd %1,%0":"=x"(r):"x"(x));return piton_double_bits(r);}
static void piton_write_uint(unsigned long v){char b[32];usize i=sizeof(b);do{b[--i]=(char)('0'+v%10);v/=10;}while(v);piton_write(1,b+i,sizeof(b)-i);}
static void piton_print_float_bits(long bits){double d=piton_bits_double(bits);if(d<0){piton_write(1,"-",1);d=-d;}unsigned long whole=(unsigned long)d;double frac=d-(double)whole;unsigned long scaled=(unsigned long)(frac*1000000000000.0+0.5);if(scaled>=1000000000000UL){++whole;scaled=0;}piton_write_uint(whole);piton_write(1,".",1);if(!scaled){piton_write(1,"0\n",2);return;}char digits[12];for(int i=11;i>=0;--i){digits[i]=(char)('0'+scaled%10);scaled/=10;}int end=12;while(end>1&&digits[end-1]=='0')--end;piton_write(1,digits,(usize)end);piton_write(1,"\n",1);}
typedef struct{int kind;long length;long capacity;PitonSlot*items;}PitonSeq;
typedef struct{long magic;PitonSeq*seq;long index;}PitonIterator;
typedef struct{PitonSeq*source;long index;}PitonGenExpr;
static void piton_raise_set(const char*,const char*);
typedef struct{PitonSlot key;PitonSlot value;}PitonDictEntry;
typedef struct{long length;long capacity;PitonDictEntry*items;}PitonDict;
typedef struct{long length;long capacity;PitonSlot*items;}PitonSet;
typedef struct{const char*name;PitonSlot value;}PitonAttr;
typedef struct{const char*class_name;const char*parent_name;long length;PitonAttr attrs[32];}PitonObject;
static int piton_slot_eq(PitonSlot a,PitonSlot b){if(a.kind!=b.kind)return 0;if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)==0;return a.bits==b.bits;}
static PitonSeq*piton_seq_new(int kind,long n){PitonSeq*s=piton_alloc(sizeof(*s));s->kind=kind;s->length=n;s->capacity=n;s->items=n>0?piton_alloc((usize)n*sizeof(PitonSlot)):0;return s;}
static void piton_seq_put(PitonSeq*s,long i,PitonSlot v){if(i>=0&&i<s->length)s->items[i]=v;}
static void piton_seq_append(PitonSeq*s,PitonSlot v){if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*na=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(na,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=na;s->capacity=nc;}s->items[s->length++]=v;}
static PitonSlot piton_seq_get(PitonSeq*s,long i){if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError\n",11);piton_exit(1);}return s->items[i];}
static long piton_iterator_new(PitonSeq*s){if(!s||(s->kind!=PK_LIST&&s->kind!=PK_TUPLE)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->seq=s;i->index=0;return(long)i;}
static long piton_iterator_next(long raw){PitonIterator*i=(PitonIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}if(i->index>=i->seq->length){piton_write(2,"StopIteration\n",14);piton_exit(1);}return i->seq->items[i->index++].bits;}
static long piton_genexpr_new(PitonSeq*s){if(!s){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}PitonGenExpr*g=piton_alloc(sizeof(*g));g->source=s;g->index=0;return(long)g;}
static long piton_genexpr_iter(PitonGenExpr*g){return(long)g;}
static long piton_genexpr_next(PitonGenExpr*g){if(!g||!g->source){piton_write(2,"TypeError: invalid generator expression\n",41);piton_exit(1);}if(g->index>=g->source->length){piton_raise_set("StopIteration","");return 0;}return g->source->items[g->index++].bits;}
static PitonDict*piton_dict_new(long n){PitonDict*d=piton_alloc(sizeof(*d));d->length=n;d->capacity=n;d->items=n>0?piton_alloc((usize)n*sizeof(PitonDictEntry)):0;return d;}
static void piton_dict_put(PitonDict*d,long i,PitonSlot k,PitonSlot v){if(i>=0&&i<d->length){d->items[i].key=k;d->items[i].value=v;}}
static void piton_dict_append(PitonDict*d,PitonSlot k,PitonSlot v){if(d->length>=d->capacity){long nc=d->capacity?d->capacity*2:4;PitonDictEntry*ni=piton_alloc((usize)nc*sizeof(PitonDictEntry));if(d->items)piton_memcpy(ni,d->items,(usize)d->capacity*sizeof(PitonDictEntry));d->items=ni;d->capacity=nc;}d->items[d->length].key=k;d->items[d->length].value=v;++d->length;}
static PitonSlot piton_dict_get(PitonDict*d,PitonSlot key){for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,key))return d->items[i].value;piton_write(2,"KeyError\n",9);piton_exit(1);}
static int piton_unpack_seq4(PitonSlot obj,long capacity,long*out4){if(obj.kind!=PK_LIST&&obj.kind!=PK_TUPLE){piton_raise_set("TypeError","argument after * must be a list or tuple");return -1;}PitonSeq*s=(PitonSeq*)obj.bits;if(s->length>capacity){piton_raise_set("TypeError","too many positional arguments for call");return -1;}for(long i=0;i<s->length;++i)out4[i]=s->items[i].bits;return s->length;}
static int piton_dict_unpack4(PitonSlot obj,const char**names,long count,long*out4,long*mask){if(obj.kind!=PK_DICT){piton_raise_set("TypeError","argument after ** must be a dict");return -1;}PitonDict*d=(PitonDict*)obj.bits;for(long i=0;i<d->length;++i){PitonSlot k=d->items[i].key;if(k.kind!=PK_STR){piton_raise_set("TypeError","keywords must be strings");return -1;}const char*key=(const char*)k.bits;long matched=-1;for(long j=0;j<count;++j)if(piton_strcmp(key,names[j])==0){matched=j;break;}if(matched<0){piton_raise_set("TypeError","unexpected keyword argument in ** expansion");return -1;}if(*mask&(1LL<<matched)){piton_raise_set("TypeError","multiple values for argument");return -1;}*mask|=1LL<<matched;out4[matched]=d->items[i].value.bits;}return 0;}
static PitonSet*piton_set_new(long cap){PitonSet*s=piton_alloc(sizeof(*s));s->capacity=cap;s->items=cap>0?piton_alloc((usize)cap*sizeof(PitonSlot)):0;return s;}
static void piton_set_add(PitonSet*s,PitonSlot v){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return;if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;PitonSlot*ni=piton_alloc((usize)nc*sizeof(PitonSlot));if(s->items)piton_memcpy(ni,s->items,(usize)s->capacity*sizeof(PitonSlot));s->items=ni;s->capacity=nc;}s->items[s->length++]=v;}
typedef struct{long magic;long kind;void*raw;long index;}PitonAnyIterator;
static long piton_iterator_new_any(void*raw,long kind){if(!raw||(kind!=PK_LIST&&kind!=PK_TUPLE&&kind!=PK_DICT&&kind!=PK_SET)){piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}PitonAnyIterator*i=piton_alloc(sizeof(*i));i->magic=0x5049544E17E2LL;i->raw=raw;i->index=0;i->kind=kind;return(long)i;}
static long piton_iterator_next_any(long raw){PitonAnyIterator*i=(PitonAnyIterator*)raw;if(!i||i->magic!=0x5049544E17E2LL){piton_write(2,"TypeError: object is not an iterator\n",37);piton_exit(1);}long n=0;if(i->kind==PK_LIST||i->kind==PK_TUPLE)n=((PitonSeq*)i->raw)->length;else if(i->kind==PK_DICT)n=((PitonDict*)i->raw)->length;else if(i->kind==PK_SET)n=((PitonSet*)i->raw)->length;else{piton_write(2,"TypeError: object is not iterable\n",34);piton_exit(1);}if(i->index>=n){piton_raise_set("StopIteration","");return 0;}PitonSlot v;if(i->kind==PK_LIST||i->kind==PK_TUPLE)v=((PitonSeq*)i->raw)->items[i->index++];else if(i->kind==PK_DICT)v=((PitonDict*)i->raw)->items[i->index++].key;else v=((PitonSet*)i->raw)->items[i->index++];return v.bits;}
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
static PitonObject*piton_object_new(const char*name,const char*parent){PitonObject*o=piton_alloc(sizeof(*o));o->class_name=name;o->parent_name=parent;return o;}
static void piton_object_set(PitonObject*o,const char*name,PitonSlot v){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0){o->attrs[i].value=v;return;}if(o->length>=32){piton_write(2,"AttributeError\n",15);piton_exit(1);}o->attrs[o->length].name=name;o->attrs[o->length++].value=v;}
static PitonSlot piton_object_get(PitonObject*o,const char*name){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value;piton_write(2,"AttributeError\n",15);piton_exit(1);}
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
static void piton_print_slot(PitonSlot v){switch(v.kind){case PK_NONE:piton_write(1,"None",4);break;case PK_BOOL:piton_write(1,v.bits?"True":"False",v.bits?4:5);break;case PK_INT:piton_write_int(v.bits);break;case PK_FLOAT:{double d=piton_bits_double(v.bits);if(d<0){piton_write(1,"-",1);d=-d;}unsigned long whole=(unsigned long)d;double frac=d-(double)whole;unsigned long scaled=(unsigned long)(frac*1000000000000.0+0.5);if(scaled>=1000000000000UL){++whole;scaled=0;}piton_write_uint(whole);piton_write(1,".",1);if(!scaled){piton_write(1,"0",1);break;}char digits[12];for(int i=11;i>=0;--i){digits[i]=(char)('0'+scaled%10);scaled/=10;}int end=12;while(end>1&&digits[end-1]=='0')--end;piton_write(1,digits,(usize)end);break;}case PK_STR:piton_write(1,(const char*)v.bits,piton_strlen((const char*)v.bits));break;case PK_LIST:case PK_TUPLE:piton_print_seq((PitonSeq*)v.bits);break;case PK_DICT:piton_print_dict((PitonDict*)v.bits);break;case PK_SET:piton_print_set((PitonSet*)v.bits);break;default:piton_write(1,"<object>",8);}}
static long piton_sum_seq(PitonSeq*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static long piton_sum_dict(PitonDict*d){long r=0;for(long i=0;i<d->length;++i)r+=d->items[i].key.bits;return r;}
static long piton_sum_set(PitonSet*s){long r=0;for(long i=0;i<s->length;++i)r+=s->items[i].bits;return r;}
static const char*piton_type_repr(int kind){switch(kind){case PK_NONE:return"<class 'NoneType'>";case PK_BOOL:return"<class 'bool'>";case PK_INT:return"<class 'int'>";case PK_FLOAT:return"<class 'float'>";case PK_STR:return"<class 'str'>";case PK_LIST:return"<class 'list'>";case PK_TUPLE:return"<class 'tuple'>";case PK_DICT:return"<class 'dict'>";case PK_SET:return"<class 'set'>";default:return"<class 'object'>";}}
static int piton_exc_flag=0;static const char*piton_exc_type=0;static const char*piton_exc_message=0;
static const char*piton_exc_cause_type=0;static const char*piton_exc_cause_msg=0;
static void piton_raise_set(const char*type,const char*message){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=message;}
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
static void piton_bigint_print(void*a){PitonBigInt*x=(PitonBigInt*)a;if(!x||(!x->sign&&x->count==1&&!x->limbs[0])){piton_write(1,"0\n",2);return;}char buf[64];int pos=sizeof(buf);buf[--pos]=0;buf[--pos]='\n';unsigned long tmp[64];int tc=0;for(long i=0;i<x->count;++i)tmp[i]=x->limbs[i];tc=(int)x->count;while(tc>0){unsigned long carry=0;for(int i=tc-1;i>=0;--i){unsigned long cur=(carry<<32)|(tmp[i]>>32);unsigned long q1=cur/10;unsigned long r1=cur-q1*10;unsigned long mid=(r1<<32)|(tmp[i]&0xFFFFFFFF);unsigned long q2=mid/10;unsigned long r2=mid-q2*10;tmp[i]=(q1<<32)|q2;carry=r2;}buf[--pos]=(char)('0'+carry);while(tc>0&&tmp[tc-1]==0)--tc;}if(x->sign<0)buf[--pos]='-';piton_write(1,buf+pos,piton_strlen(buf+pos));}
static void*piton_bigint_add(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;if(x->sign==y->sign){r->sign=x->sign;bi_add_mag(r,x,y);}else{int c=bi_cmp_mag(x,y);if(c==0)return r;if(c>0){r->sign=x->sign;bi_sub_mag(r,x,y);}else{r->sign=y->sign;bi_sub_mag(r,y,x);}}bi_trim(r);return r;}
static void*piton_bigint_negate(void*a){PitonBigInt*x=(PitonBigInt*)a;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=-x->sign;r->count=x->count;r->capacity=x->capacity;r->limbs=x->limbs;return r;}
static void*piton_bigint_sub(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*negy=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));negy->sign=-y->sign;negy->count=y->count;negy->capacity=y->capacity;negy->limbs=y->limbs;return piton_bigint_add(x,negy);}
static void bi_mul_mag(PitonBigInt*r,PitonBigInt*a,PitonBigInt*b){if(a->count==0||b->count==0){r->count=0;return;}long max_c=a->count+b->count;bi_ensure(r,max_c);for(unsigned long i=0;i<r->capacity;++i)r->limbs[i]=0;for(long i=0;i<a->count;++i){u128 carry=0;for(long j=0;j<b->count||carry;++j){u128 cur=r->limbs[i+j]+(u128)a->limbs[i]*(j<b->count?b->limbs[j]:0)+carry;r->limbs[i+j]=(unsigned long)cur;carry=cur>>64;}r->count=i+b->count+1;}bi_trim(r);}
static void*piton_bigint_mul(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=x->sign^y->sign;r->count=0;r->capacity=0;r->limbs=0;bi_mul_mag(r,x,y);return r;}
static long bi_cmp_magnitude(PitonBigInt*a,PitonBigInt*b){long ac=a->count,bc=b->count;while(ac>0&&a->limbs[ac-1]==0)ac--;while(bc>0&&b->limbs[bc-1]==0)bc--;if(ac!=bc)return ac>bc?1:-1;for(long i=ac-1;i>=0;--i){if(a->limbs[i]!=b->limbs[i])return a->limbs[i]>b->limbs[i]?1:-1;}return 0;}
static void bi_div_mod_internal(PitonBigInt*quot,PitonBigInt*rem,PitonBigInt*dividend,PitonBigInt*divisor){long dc=dividend->count;long dvc=divisor->count;bi_ensure(quot,dc);for(long i=0;i<dc;++i)quot->limbs[i]=0;quot->count=dc;bi_ensure(rem,dvc);for(long i=0;i<dvc;++i)rem->limbs[i]=0;rem->count=0;for(long i=dc-1;i>=0;--i){for(int b=63;b>=0;--b){rem->count=(i+1>rem->count)?i+1:rem->count;for(long j=rem->count-1;j>0;--j)rem->limbs[j]=((rem->limbs[j]<<1)|((rem->limbs[j-1]>>63)&1));rem->limbs[0]=(rem->limbs[0]<<1)|((dividend->limbs[i]>>b)&1);if(bi_cmp_magnitude(rem,divisor)>=0){bi_sub_mag(rem,rem,divisor);quot->limbs[i]|=(1UL<<b);}}}bi_trim(quot);bi_trim(rem);}
static void*piton_bigint_floor_div(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);q->sign=x->sign^y->sign;bi_trim(q);return q;}
static void*piton_bigint_mod(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;PitonBigInt*q=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));q->sign=0;q->count=0;q->capacity=0;q->limbs=0;PitonBigInt*r=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));r->sign=0;r->count=0;r->capacity=0;r->limbs=0;bi_div_mod_internal(q,r,x,y);if(r->count!=0&&r->sign!=0){PitonBigInt*ys=(PitonBigInt*)piton_bump_alloc(sizeof(PitonBigInt));ys->sign=y->sign^1;ys->count=y->count;ys->capacity=y->capacity;ys->limbs=y->limbs;r= piton_bigint_add(r,ys);}bi_trim(r);return r;}
static long piton_bigint_cmp(void*a,void*b){PitonBigInt*x=(PitonBigInt*)a,*y=(PitonBigInt*)b;if(x->sign!=y->sign)return x->sign?-1:1;int c=bi_cmp_mag(x,y);return x->sign?-c:c;}
static void piton_bigint_free(void*a){(void)a;}
"""


def _name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"piton_{cleaned}" if cleaned and cleaned[0].isdigit() else cleaned


class LinuxCEmitter:
    def __init__(self) -> None:
        self.function_names: set[str] = set()
        self.generator_layouts: dict[str, dict[str, int]] = {}
        self.function_params: dict[str, list[str]] = {}
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
        )
        self._has_rich_runtime = True
        lines = [
            "typedef long i64; typedef unsigned long usize; typedef unsigned long long u64; typedef unsigned __int128 u128;",
            "static long piton_write(long fd,const void*buf,usize n){long r;__asm__ volatile(\"syscall\":\"=a\"(r):\"a\"(1L),\"D\"(fd),\"S\"(buf),\"d\"(n):\"rcx\",\"r11\",\"memory\");return r;}",
            "__attribute__((noreturn)) static void piton_exit(long code){__asm__ volatile(\"syscall\"::\"a\"(60L),\"D\"(code):\"rcx\",\"r11\",\"memory\");__builtin_unreachable();}",
            "static usize piton_strlen(const char*s){usize n=0;while(s[n])++n;return n;}",
            "static int piton_strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return (unsigned char)*a-(unsigned char)*b;}",
            "static void piton_print_str(const char*s){piton_write(1,s,piton_strlen(s));piton_write(1,\"\\n\",1);}",
            "static void piton_print_dynamic(long bits){if(!bits){piton_write(1,\"None\",4);}else{piton_write(1,(const char*)bits,piton_strlen((const char*)bits));}}",
            "static void piton_write_int(i64 number){char b[32];usize i=sizeof(b);unsigned long value;if(number<0){piton_write(1,\"-\",1);value=0-(unsigned long)number;}else value=(unsigned long)number;do{b[--i]=(char)(\'0\'+value%10);value/=10;}while(value);piton_write(1,b+i,sizeof(b)-i);}",
            "static void piton_print_int(i64 number){piton_write_int(number);piton_write(1,\"\\n\",1);}",
            "static i64 piton_floor_div(i64 a,i64 b){i64 q=a/b,r=a%b;if(r&&((r<0)!=(b<0)))--q;return q;}",
            "static i64 piton_mod(i64 a,i64 b){i64 r=a%b;if(r&&((r<0)!=(b<0)))r+=b;return r;}",
            "static char piton_concat_buf[65536];static char*piton_concat_ptr=0;",
            "static long piton_str_concat(const char*a,const char*b){if(!piton_concat_ptr)piton_concat_ptr=piton_concat_buf;usize la=piton_strlen(a),lb=piton_strlen(b);char*r=piton_concat_ptr;for(usize i=0;i<la;++i)r[i]=a[i];for(usize i=0;i<lb;++i)r[la+i]=b[i];r[la+lb]=0;piton_concat_ptr+=la+lb;return(long)r;}",
        ]
        if self._has_rich_runtime:
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
        for function in module.functions:
            lines.extend(self._emit_function(function))
        lines.append("void _start(void){__asm__(\"sub $8, %rsp\");piton_exit(piton_main());}")
        return "\n".join(lines) + "\n"

    def _emit_function(self, function: MIRFunction) -> list[str]:
        if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
            return self._emit_generator_function(function)
        is_main = function.name == "<module>"
        params = "long *frame" if function.frame_abi else ", ".join(f"long {_name(param)}" for param in function.params) or "void"
        signature = "static long piton_main(void)" if is_main else f"static long {_name(function.name)}({params})"
        slots = set(function.params)
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.result:
                    slots.add(instruction.result)
                if instruction.op == "store":
                    slots.add(instruction.args[0])
        locals_ = sorted(slots if function.frame_abi else slots - set(function.params))
        lines = [signature + " {"]
        if locals_:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot in locals_) + ";")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
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
            return f"piton_double_bits({value.hex()})"
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
            types[result] = awaited_type

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
            if iterator_type in {"genexpr", "iterator:genexpr"}:
                out.append(f"    {_name(result)}=piton_genexpr_next((PitonGenExpr*){self._value(iterator)});")
            elif iterator_type == "generator":
                out.append(f"    {_name(result)}=piton_gen_next({self._value(iterator)});")
            elif iterator_type.startswith("iterator:object:") or iterator_type.startswith("object:"):
                class_name = iterator_type.split(":", 2)[2] if iterator_type.startswith("iterator:") else iterator_type.split(":", 1)[1]
                method_class = self._resolve_method(class_name, "__next__")
                out.append(f"    {_name(result)}={_name(method_class+'__'+'__next__')}({self._value(iterator)});")
            else:
                next_helper = {"iterator:enumerate": "piton_enumerate_next", "iterator:reversed": "piton_reversed_next", "iterator:zip": "piton_zip_next", "iterator:map": "piton_callback_iterator_next", "iterator:filter": "piton_callback_iterator_next", "iterator:calliter": "piton_calliter_next"}.get(iterator_type, "piton_iterator_next_any")
                out.append(f"    {_name(result)}={next_helper}({self._value(iterator)});")
            types[result] = "tuple" if iterator_type in {"iterator:enumerate", "iterator:zip"} else "str" if iterator_type == "iterator:dict" else "int"
            out.append("    if(piton_exc_flag){")
            if handler_label:
                out.append(f"        goto {_name(function.name + '_' + handler_label)};")
            else:
                out.append("        piton_report_unhandled();piton_exit(1);")
            out.append("    }")
        elif op == "const":
            value = args[0]
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
                out.append(f"    {_name(result)}=piton_double_bits({value.hex()});")
                types[result] = "float"
            else:
                raise NativeBuildError(f"Linux backend cannot encode constant {value!r}")
        elif op == "load":
            source = args[0]
            aliases[result] = source
            types[result] = types.get(source, "int")
            if source in self.function_names:
                out.append(f"    {_name(result)}=(long)&{_name(source)};")
                return out
            if source in _BUILTINS and source not in function.params:
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
            out.append(f"    {_name(result)}={_name(source)};")
        elif op == "store":
            out.append(f"    {_name(args[0])}={self._value(args[1])};")
            types[args[0]] = types.get(args[1], "int")
        elif op == "unary":
            operator = {"no": "!", "not": "!"}.get(args[0], args[0])
            if operator not in {"+", "-", "~", "!"}:
                raise NativeBuildError(f"Linux unary operator not supported: {operator}")
            if operator == "-" and types.get(args[1]) == "bigint":
                out.append(f"    {_name(result)}=(long)piton_bigint_negate((void*){_name(args[1])});")
                types[result] = "bigint"
                bigint_slots.append(result)
                return out
            if types.get(args[1]) == "float":
                if operator == "-":
                    out.append(f"    {_name(result)}=piton_float_neg({self._value(args[1])});")
                    types[result] = "float"
                    return out
                if operator == "+":
                    out.append(f"    {_name(result)}={self._value(args[1])};")
                    types[result] = "float"
                    return out
                raise NativeBuildError(f"Linux float unary operator not supported: {operator}")
            out.append(f"    {_name(result)}={operator}{self._value(args[1])};")
            types[result] = "bool" if operator == "!" else "int"
        elif op == "binary":
            operator, left, right = args
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
            if "str" in {left_type, right_type}:
                if operator == "+" and left_type == right_type == "str":
                    out.append(f"    {_name(result)}=(long)piton_str_concat((const char*){_name(left)},(const char*){_name(right)});")
                    types[result] = "str"
                    return out
                raise NativeBuildError(f"Linux string binary operator not supported: {operator}")
            if "float" in {left_type, right_type}:
                if operator not in {"+", "-", "*"}:
                    raise NativeBuildError(f"Linux float binary operator not supported: {operator}")
                left_value = self._value(left)
                right_value = self._value(right)
                if left_type != "float":
                    left_value = f"piton_double_bits((double){left_value})"
                if right_type != "float":
                    right_value = f"piton_double_bits((double){right_value})"
                helper = {"+": "piton_float_add", "-": "piton_float_sub", "*": "piton_float_mul"}[operator]
                out.append(f"    {_name(result)}={helper}({left_value},{right_value});")
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
            if operator == "//":
                expression = f"piton_floor_div({self._value(left)},{self._value(right)})"
            elif operator == "%":
                expression = f"piton_mod({self._value(left)},{self._value(right)})"
            elif operator in {"+", "-", "*", "&", "|", "^", "<<", ">>"}:
                expression = f"({self._value(left)} {operator} {self._value(right)})"
            else:
                raise NativeBuildError(f"Linux binary operator not supported: {operator}")
            out.append(f"    {_name(result)}={expression};")
            types[result] = "int"
        elif op == "compare":
            operator, left, right = args
            left_type = types.get(left, "int")
            right_type = types.get(right, "int")
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
            if function_name in {"imprimir", "print"}:
                if not values:
                    out.append('    piton_write(1,"\\n",1);')
                elif types.get(values[0]) == "str":
                    out.append(f'    piton_print_str((char*){self._value(values[0])});')
                elif types.get(values[0]) == "bool":
                    out.append(f'    piton_print_str({self._value(values[0])}?"True":"False");')
                elif types.get(values[0]) == "none":
                    out.append('    piton_print_str("None");')
                elif types.get(values[0]) == "bigint":
                    out.append(f'    piton_bigint_print((void*){_name(values[0])});')
                elif types.get(values[0]) == "float":
                    out.append(f'    piton_print_float_bits({self._value(values[0])});')
                elif types.get(values[0]) == "module-pkg":
                    out.append(f'    piton_print_dynamic({self._value(values[0])});')
                    out.append('    piton_write(1,"\\n",1);')
                elif types.get(values[0]) in {"list", "tuple", "dict", "set"}:
                    out.append(f'    piton_print_slot({self._slot(values[0], types)});')
                    out.append('    piton_write(1,"\\n",1);')
                else:
                    out.append(f'    piton_print_int((long){self._value(values[0])});')
                out.append(f"    {_name(result)}=0;")
            elif function_name in {"longitud", "len"}:
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
            elif function_name in {"min", "max"}:
                if len(values) != 2:
                    raise NativeBuildError("Linux min/max requires two arguments")
                comparison = "<" if function_name == "min" else ">"
                if types.get(values[0]) == "float":
                    out.append(f"    {_name(result)}=piton_bits_double({self._value(values[0])}){comparison}piton_bits_double({self._value(values[1])})?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "float"
                else:
                    out.append(f"    {_name(result)}={self._value(values[0])}{comparison}{self._value(values[1])}?{self._value(values[0])}:{self._value(values[1])};")
                    types[result] = "int"
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
                values = self._complete_call_args(function_name, list(values))
                if function_name in self.function_names:
                    encoded_values = ",".join(self._value(value) for value in values)
                    out.append(f"    {_name(result)}={_name(function_name)}({encoded_values});")
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
            out.append(f"    return {self._value(args[0])};")
        elif op == "object_new":
            cls_name = args[0]
            parent = args[1] if len(args) > 1 else None
            parent_str = f'"{parent}"' if parent else "0"
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
                raise NativeBuildError(
                    f"native del on '{attr}' is not a property of a natively-typed object"
                )
        elif op == "method_call":
            cls_name, method, obj = args[0], args[1], args[2]
            call_args = args[3] if len(args) > 3 else ()
            if cls_name is None:
                owner_type = types.get(obj, "")
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
                out.append(f'    {_name(result)}={_name(target)}({values});')
            types[result] = "int"
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
        elif op == "math_sqrt":
            operand = self._value(args[0])
            if types.get(args[0]) != "float":
                operand = f"piton_double_bits((double){operand})"
            out.append(f'    {_name(result)}=piton_float_sqrt({operand});')
            types[result] = "float"
        elif op == "build_collection":
            kind, items = args[0], args[1]
            if kind in {"list", "tuple"}:
                out.append(f'    {_name(result)}=(long)piton_seq_new({self._kind(kind)},{len(items)});')
                for index, value in enumerate(items):
                    out.append(f'    piton_seq_put((PitonSeq*){_name(result)},{index},{self._slot(value, types)});')
            elif kind == "dict":
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
            elif collection_type in {"dict", "dict:module"}:
                out.append(f'    {_name(result)}=piton_dict_get((PitonDict*){self._value(coll)},{self._slot(idx, types)}).bits;')
            else:
                raise NativeBuildError(f"Linux subscription not supported for {collection_type}")
            types[result] = "object:module" if collection_type == "dict:module" else "int"
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
        elif op == "runtime_call":
            raise NativeBuildError(f"runtime operation not supported in Linux native subset: {args[0]}")
        else:
            raise NativeBuildError(f"Linux MIR operation not supported: {op}")
        return out


def windows_to_wsl_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise NativeBuildError(f"WSL path requires a Windows drive: {resolved}")
    tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{tail}"


def compile_native_linux(source: str, output: str | Path) -> Path:
    try:
        mir = lower_hir_to_mir(lower_cst_to_hir(parse(source)))
    except MIRLoweringError as error:
        raise NativeBuildError(str(error)) from error
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="piton-linux-") as directory:
        c_path = Path(directory) / "program.c"
        c_path.write_text(LinuxCEmitter().emit(mir), encoding="utf-8")
        completed = subprocess.run(
            [
                "wsl.exe", "gcc", "-std=c11", "-O2", "-ffreestanding",
                "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdlib", "-static",
                windows_to_wsl_path(c_path), "-o", windows_to_wsl_path(output_path),
            ],
            capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            raise NativeBuildError(completed.stderr or completed.stdout or f"Linux compiler exited {completed.returncode}")
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
    except MIRLoweringError as error:
        raise NativeBuildError(str(error)) from error
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="piton-linux-") as directory:
        c_path = Path(directory) / "program.c"
        c_path.write_text(LinuxCEmitter().emit(mir), encoding="utf-8")
        completed = subprocess.run(
            [
                "wsl.exe", "gcc", "-std=c11", "-O2", "-ffreestanding",
                "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdlib", "-static",
                windows_to_wsl_path(c_path), "-o", windows_to_wsl_path(output_path),
            ],
            capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            raise NativeBuildError(completed.stderr or completed.stdout)
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
