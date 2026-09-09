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
from .x86 import NativeBuildError, _BUILTINS, _scan_native_modules


_RICH_FREESTANDING_C = r"""
enum{PK_NONE,PK_BOOL,PK_INT,PK_FLOAT,PK_STR,PK_LIST,PK_TUPLE,PK_DICT,PK_SET,PK_OBJECT,PK_BIGINT};
typedef struct{long bits;int kind;}PitonSlot;
static unsigned char piton_arena[8*1024*1024];
static usize piton_arena_used=0;
static void piton_memzero(void*p,usize n){unsigned char*b=p;for(usize i=0;i<n;++i)b[i]=0;}
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
typedef struct{int kind;long length;PitonSlot*items;}PitonSeq;
typedef struct{PitonSlot key;PitonSlot value;}PitonDictEntry;
typedef struct{long length;PitonDictEntry*items;}PitonDict;
typedef struct{long length;PitonSlot*items;}PitonSet;
typedef struct{const char*name;PitonSlot value;}PitonAttr;
typedef struct{const char*class_name;const char*parent_name;long length;PitonAttr attrs[32];}PitonObject;
static int piton_slot_eq(PitonSlot a,PitonSlot b){if(a.kind!=b.kind)return 0;if(a.kind==PK_STR)return piton_strcmp((const char*)a.bits,(const char*)b.bits)==0;return a.bits==b.bits;}
static PitonSeq*piton_seq_new(int kind,long n){PitonSeq*s=piton_alloc(sizeof(*s));s->kind=kind;s->length=n;s->items=piton_alloc((usize)n*sizeof(PitonSlot));return s;}
static void piton_seq_put(PitonSeq*s,long i,PitonSlot v){if(i>=0&&i<s->length)s->items[i]=v;}
static PitonSlot piton_seq_get(PitonSeq*s,long i){if(i<0)i+=s->length;if(i<0||i>=s->length){piton_write(2,"IndexError\n",11);piton_exit(1);}return s->items[i];}
static PitonDict*piton_dict_new(long n){PitonDict*d=piton_alloc(sizeof(*d));d->length=n;d->items=piton_alloc((usize)n*sizeof(PitonDictEntry));return d;}
static void piton_dict_put(PitonDict*d,long i,PitonSlot k,PitonSlot v){if(i>=0&&i<d->length){d->items[i].key=k;d->items[i].value=v;}}
static PitonSlot piton_dict_get(PitonDict*d,PitonSlot key){for(long i=0;i<d->length;++i)if(piton_slot_eq(d->items[i].key,key))return d->items[i].value;piton_write(2,"KeyError\n",9);piton_exit(1);}
static PitonSet*piton_set_new(long cap){PitonSet*s=piton_alloc(sizeof(*s));s->items=piton_alloc((usize)cap*sizeof(PitonSlot));return s;}
static void piton_set_add(PitonSet*s,PitonSlot v){for(long i=0;i<s->length;++i)if(piton_slot_eq(s->items[i],v))return;s->items[s->length++]=v;}
static PitonObject*piton_object_new(const char*name,const char*parent){PitonObject*o=piton_alloc(sizeof(*o));o->class_name=name;o->parent_name=parent;return o;}
static void piton_object_set(PitonObject*o,const char*name,PitonSlot v){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0){o->attrs[i].value=v;return;}if(o->length>=32){piton_write(2,"AttributeError\n",15);piton_exit(1);}o->attrs[o->length].name=name;o->attrs[o->length++].value=v;}
static PitonSlot piton_object_get(PitonObject*o,const char*name){for(long i=0;i<o->length;++i)if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].value;piton_write(2,"AttributeError\n",15);piton_exit(1);}
#define PITON_CLOSURE_MAGIC 0x5049544EC10557LL
typedef struct{long magic;long addr;long n_args;long n_cells;long cells[4];}PitonClosure;
static long piton_closure_new8(long addr,long n_args,long n_cells,long c0,long c1,long c2,long c3){PitonClosure*c=(PitonClosure*)piton_alloc(sizeof(PitonClosure));c->magic=PITON_CLOSURE_MAGIC;c->addr=addr;c->n_args=n_args;c->n_cells=n_cells;long cs[4]={c0,c1,c2,c3};for(long i=0;i<n_cells&&i<4;++i)c->cells[i]=cs[i];return(long)c;}
static long piton_closure_call6(long callee,long argc,long a0,long a1,long a2,long a3){if(!callee||((long*)callee)[0]!=PITON_CLOSURE_MAGIC)return((long(*)(long,long,long,long))callee)(a0,a1,a2,a3);PitonClosure*c=(PitonClosure*)callee;if(argc!=c->n_args){piton_write(2,"TypeError: closure called with wrong number of arguments\n",56);piton_exit(2);}long total=c->n_cells+argc;if(total>4){piton_write(2,"TypeError: closure cell count plus arguments exceeds four\n",59);piton_exit(2);}long x[4]={a0,a1,a2,a3};for(long i=0;i<c->n_cells&&i<4;++i){for(long j=3;j>i;--j)x[j]=x[j-1];x[i]=c->cells[i];}return((long(*)(long,long,long,long))c->addr)(x[0],x[1],x[2],x[3]);}
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
static void piton_raise_set(const char*type,const char*message){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=message;}
static const char*piton_reraise_type=0;static const char*piton_reraise_message=0;
static void piton_reraise_save(void){piton_reraise_type=piton_exc_type;piton_reraise_message=piton_exc_message;}
static void piton_reraise_set(const char*type){piton_exc_flag=1;piton_exc_type=type;piton_exc_message=piton_reraise_message;}
static void piton_catch_clear(void){piton_exc_flag=0;piton_exc_type=0;piton_exc_message=0;}
static void piton_report_unhandled(void){piton_write(2,piton_exc_type,piton_strlen(piton_exc_type));piton_write(2,": ",2);if(piton_exc_message)piton_write(2,piton_exc_message,piton_strlen(piton_exc_message));piton_write(2,"\n",1);}
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

    def emit(self, module: MIRModule) -> str:
        self.module = module
        self.classes = getattr(module, "classes", {})
        self.class_parents = getattr(module, "class_parents", {})
        self.function_names = {function.name for function in module.functions}
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
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
                params = ", ".join(f"long {_name(param)}" for param in function.params) or "void"
                lines.append(f"static long {_name(function.name)}({params});")
        for function in module.functions:
            lines.extend(self._emit_function(function))
        lines.append("void _start(void){__asm__(\"sub $8, %rsp\");piton_exit(piton_main());}")
        return "\n".join(lines) + "\n"

    def _emit_function(self, function: MIRFunction) -> list[str]:
        is_main = function.name == "<module>"
        params = ", ".join(f"long {_name(param)}" for param in function.params) or "void"
        signature = "static long piton_main(void)" if is_main else f"static long {_name(function.name)}({params})"
        slots = set(function.params)
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.result:
                    slots.add(instruction.result)
                if instruction.op == "store":
                    slots.add(instruction.args[0])
        locals_ = sorted(slots - set(function.params))
        lines = [signature + " {"]
        if locals_:
            lines.append("    long " + ", ".join(f"{_name(slot)}=0" for slot in locals_) + ";")
        aliases: dict[str, str] = {}
        types: dict[str, str] = {}
        if function.vararg:
            types[function.vararg] = "tuple"
        if function.kwarg:
            types[function.kwarg] = "dict"
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
        current = class_name
        while current:
            if method in self.classes.get(current, set()):
                return current
            current = self.class_parents.get(current)
        raise NativeBuildError(f"native method not found: {class_name}.{method}")

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

    def _emit_instruction(
        self, instruction: MIRInstruction, function: MIRFunction,
        aliases: dict[str, str], types: dict[str, str], bigint_slots: list[str],
    ) -> list[str]:
        op, args, result = instruction.op, instruction.args, instruction.result
        out: list[str] = []
        if op == "const":
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
            if source in _BUILTINS:
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
            else:
                values = self._complete_call_args(function_name, list(values))
                if function_name in self.function_names:
                    encoded_values = ",".join(self._value(value) for value in values)
                    out.append(f"    {_name(result)}={_name(function_name)}({encoded_values});")
                else:
                    argc = len(values)
                    if argc > 4:
                        raise NativeBuildError("Linux calls with more than four arguments are not supported yet")
                    arg_values = [self._value(value) for value in values]
                    arg_values += ["0"] * (4 - len(arg_values))
                    out.append(
                        f"    {_name(result)}=piton_closure_call6({self._value(args[0])},{argc},{','.join(arg_values)});"
                    )
                types[result] = "int"
        elif op == "return":
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
            lifted_name, n_args, capture_ops = args
            if len(capture_ops) > 4:
                raise NativeBuildError("Linux closure escape with more than four captured cells is not supported yet")
            cell_values = [self._value(cap) for cap in capture_ops]
            cell_values += ["0"] * (4 - len(cell_values))
            out.append(
                f"    {_name(result)}=piton_closure_new8((long)&{_name(lifted_name)},{n_args},"
                f"{len(capture_ops)},{','.join(cell_values)});"
            )
            types[result] = "closure"
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
            out.append(f'    piton_object_set((PitonObject*){self._value(obj)},"{attr}",{self._slot(val, types)});')
        elif op == "get_attr":
            obj, attr = args
            out.append(f'    {_name(result)}=piton_object_get((PitonObject*){self._value(obj)},"{attr}").bits;')
            owner_type = types.get(obj, "")
            module_attr_types = {"__name__": "str", "__file__": "str", "__package__": "module-pkg", "modules": "dict:module"}
            if owner_type == "object:module":
                types[result] = module_attr_types.get(attr, "int")
            else:
                types[result] = "int"
        elif op == "method_call":
            cls_name, method, obj = args[0], args[1], args[2]
            call_args = args[3] if len(args) > 3 else ()
            if cls_name is None:
                owner_type = types.get(obj, "")
                if not owner_type.startswith("object:"):
                    raise NativeBuildError("Linux method receiver class is not statically known")
                cls_name = owner_type.split(":", 1)[1]
            cls_name = self._resolve_method(cls_name, method)
            values = ",".join(self._value(v) for v in ([obj] + list(call_args)))
            out.append(f'    {_name(result)}={_name(cls_name+"__"+method)}({values});')
            types[result] = "int"
        elif op == "raise_typed":
            exc_type, payload, handler_label = args
            message = f"(const char*){self._value(payload)}" if payload is not None else '""'
            out.append(f'    piton_raise_set("{exc_type}",{message});')
            if handler_label:
                out.append(f"    goto {_name(function.name + '_' + handler_label)};")
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
        elif op == "reraise_save":
            out.append('    piton_reraise_save();')
        elif op == "raise_active":
            exc_type, handler_label = args
            out.append(f'    piton_reraise_set("{exc_type}");')
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
            raise NativeBuildError(completed.stderr or completed.stdout)
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
