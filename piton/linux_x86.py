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
from .x86 import NativeBuildError, _BUILTINS


_TAGGED_RUNTIME_C = r"""
/* ── Tagged value runtime (same ABI as Windows) ─────────────────────── */
#define TAG_NONE 0
#define TAG_BOOL 1
#define TAG_INT 2
#define TAG_FLOAT 3
#define TAG_OBJECT 4
#define SUB_STR 5
#define SUB_LIST 6
#define SUB_TUPLE 7
#define SUB_DICT 8
#define SUB_SET 9
#define SUB_BIGINT 10
#define TAG_SHIFT 61
#define PV_MASK ((1LL<<TAG_SHIFT)-1)
static inline long pv_encode(int t,long p){return((long)t<<TAG_SHIFT)|(p&PV_MASK);}
static inline int pv_tag(long v){return(int)((v>>TAG_SHIFT)&7);}
static inline long pv_payload(long v){return v&PV_MASK;}
static inline long pv_none(void){return pv_encode(TAG_NONE,0);}
static inline long pv_bool(int b){return pv_encode(TAG_BOOL,b?1:0);}
static inline long pv_int(long i){return pv_encode(TAG_INT,i);}
static inline long pv_float(double d){long*p=malloc(sizeof(long));*p=0;memcpy(p,&d,8);return pv_encode(TAG_FLOAT,(long)p);}
static inline double pv_as_float(long v){double d;long p=pv_payload(v);memcpy(&d,&p,8);return d;}
typedef struct{const char*name;long val;}PitonAttr;
typedef struct{int tag;long refcount;const char*class_name;const char*parent;int attr_count;PitonAttr attrs[32];}PitonObj;
static long pv_object_new(const char*cls,const char*par){PitonObj*o=calloc(1,sizeof(*o));o->tag=TAG_OBJECT;o->refcount=1;o->class_name=cls;o->parent=par;return pv_encode(TAG_OBJECT,(long)o);}
static void pv_set_attr(long obj,const char*name,long val){PitonObj*o=(PitonObj*)pv_payload(obj);for(int i=0;i<o->attr_count;i++){if(piton_strcmp(o->attrs[i].name,name)==0){o->attrs[i].val=val;return;}}if(o->attr_count<32){o->attrs[o->attr_count].name=name;o->attrs[o->attr_count].val=val;o->attr_count++;}}
static long pv_get_attr(long obj,const char*name){PitonObj*o=(PitonObj*)pv_payload(obj);for(int i=0;i<o->attr_count;i++){if(piton_strcmp(o->attrs[i].name,name)==0)return o->attrs[i].val;}if(o->parent){/* walk up - simplified: just return 0*/}return pv_none();}
static const char*pv_type_name(long v){switch(pv_tag(v)){case TAG_NONE:return"NoneType";case TAG_BOOL:return"bool";case TAG_INT:return"int";case TAG_FLOAT:return"float";case TAG_OBJECT:{PitonObj*o=(PitonObj*)pv_payload(v);return o->class_name?o->class_name:"object";}default:return"unknown";}}
static void piton_print_value(long v){char buf[64];switch(pv_tag(v)){case TAG_NONE:piton_print_str("None");break;case TAG_BOOL:piton_print_str(pv_payload(v)?"True":"False");break;case TAG_INT:piton_print_int(pv_payload(v));break;case TAG_FLOAT:{double d=pv_as_float(v);int n=sprintf(buf,"%.17g",d);piton_write(1,buf,n);piton_write(1,"\n",1);break;}case TAG_OBJECT:piton_print_str(pv_type_name(v));break;default:piton_print_int(pv_payload(v));break;}}
/* ── Exception state ────────────────────────────────────────────────── */
static int piton_exc_flag=0;
static const char*piton_exc_type=0;
static void piton_raise(const char*type){piton_exc_flag=1;piton_exc_type=type;piton_print_str("Traceback (most recent call last):");piton_write(1,"  ",2);piton_print_str(type);piton_exit(1);}
static void piton_catch_clear(void){piton_exc_flag=0;piton_exc_type=0;}
/* ── Math/stdlib ────────────────────────────────────────────────────── */
static long piton_abs(long v){return pv_payload(v)<0?-pv_payload(v):pv_payload(v);}
static long piton_min(long a,long b){return pv_payload(a)<pv_payload(b)?a:b;}
static long piton_max(long a,long b){return pv_payload(a)>pv_payload(b)?a:b;}
static long piton_type_from_raw(long v){return pv_int(pv_tag(v));}
static long piton_sqrt(long v){return pv_float(__builtin_sqrt((double)pv_payload(v)));}
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
        self.function_names = {function.name for function in module.functions}
        self._has_bigint = any(
            isinstance(instruction.args[0], int) and abs(instruction.args[0]) > 9223372036854775807
            for function in module.functions
            for block in function.blocks
            for instruction in block.instructions
        )
        self._has_objects = any(
            instruction.op in {"object_new", "set_attr", "get_attr", "method_call", "raise_typed", "try_push", "try_pop", "catch_flag", "catch_clear", "math_sqrt", "build_collection", "get_item", "collection_len", "runtime_call"}
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
            "static void piton_print_str(const char*s){piton_write(1,s,piton_strlen(s));piton_write(1,\"\\n\",1);}",
            "static void piton_print_int(i64 number){char b[32];usize i=sizeof(b);unsigned long value;if(number<0){piton_write(1,\"-\",1);value=0-(unsigned long)number;}else value=(unsigned long)number;do{b[--i]=(char)(\'0\'+value%10);value/=10;}while(value);piton_write(1,b+i,sizeof(b)-i);piton_write(1,\"\\n\",1);}",
            "static i64 piton_floor_div(i64 a,i64 b){i64 q=a/b,r=a%b;if(r&&((r<0)!=(b<0)))--q;return q;}",
            "static i64 piton_mod(i64 a,i64 b){i64 r=a%b;if(r&&((r<0)!=(b<0)))r+=b;return r;}",
            "static char piton_concat_buf[65536];static char*piton_concat_ptr=0;",
            "static long piton_str_concat(const char*a,const char*b){if(!piton_concat_ptr)piton_concat_ptr=piton_concat_buf;usize la=piton_strlen(a),lb=piton_strlen(b);char*r=piton_concat_ptr;for(usize i=0;i<la;++i)r[i]=a[i];for(usize i=0;i<lb;++i)r[la+i]=b[i];r[la+lb]=0;piton_concat_ptr+=la+lb;return(long)r;}",
        ]
        if self._has_objects:
            lines.append(_TAGGED_RUNTIME_C)
        if self._has_bigint:
            bigint_code = _BIGINT_FREESTANDING_C
            lines.extend(bigint_code.split("\n"))
        for function in module.functions:
            if function.name != "<module>":
                params = ", ".join(f"long {_name(param)}" for param in function.params) or "void"
                lines.append(f"static long {_name(function.name)}({params});")
        for function in module.functions:
            lines.extend(self._emit_function(function))
        lines.append("void _start(void){piton_exit(piton_main());}")
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
        bigint_slots: list[str] = []
        for block in function.blocks:
            lines.append(f"{_name(function.name + '_' + block.label)}:")
            for instruction in block.instructions:
                lines.extend(self._emit_instruction(instruction, function, aliases, types, bigint_slots))
        for slot in bigint_slots:
            lines.append(f"    piton_bigint_free((void*){_name(slot)});")
            lines.append(f"    {_name(slot)}=0;")
        lines.append("    return 0;")
        lines.append("}")
        return lines

    def _value(self, value: Any) -> str:
        if isinstance(value, str) and value.startswith("%"):
            return _name(value)
        if value is None:
            return "0"
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        raise NativeBuildError(f"Linux scalar backend cannot encode {value!r}")

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
            else:
                raise NativeBuildError("Linux scalar backend does not support float constants yet")
        elif op == "load":
            source = args[0]
            aliases[result] = source
            types[result] = types.get(source, "int")
            if source in _BUILTINS or source in self.function_names:
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
            if function_name in {"imprimir", "print"}:
                if not args[1]:
                    out.append('    piton_write(1,"\\n",1);')
                elif types.get(args[1][0]) == "str":
                    out.append(f'    piton_print_str((char*){self._value(args[1][0])});')
                elif types.get(args[1][0]) == "bool":
                    out.append(f'    piton_print_str({self._value(args[1][0])}?"True":"False");')
                elif types.get(args[1][0]) == "none":
                    out.append('    piton_print_str("None");')
                elif types.get(args[1][0]) == "bigint":
                    out.append(f'    piton_bigint_print((void*){_name(args[1][0])});')
                else:
                    out.append(f'    piton_print_int((long){self._value(args[1][0])});')
                out.append(f"    {_name(result)}=0;")
            else:
                values = ",".join(self._value(value) for value in args[1])
                out.append(f"    {_name(result)}={_name(function_name)}({values});")
                types[result] = "int"
        elif op == "return":
            out.append(f"    return {self._value(args[0])};")
        elif op == "object_new":
            cls_name = args[0]
            parent = args[1] if len(args) > 1 else None
            parent_str = f'"{parent}"' if parent else "0"
            out.append(f'    {_name(result)}=pv_object_new("{cls_name}",{parent_str});')
            types[result] = "object"
        elif op == "set_attr":
            obj, attr, val = args
            out.append(f'    pv_set_attr({self._value(obj)},"{attr}",{self._value(val)});')
        elif op == "get_attr":
            obj, attr = args
            out.append(f'    {_name(result)}=pv_get_attr({self._value(obj)},"{attr}");')
            types[result] = "int"
        elif op == "method_call":
            cls_name, method, obj = args[0], args[1], args[2]
            call_args = args[3] if len(args) > 3 else ()
            values = ",".join(self._value(v) for v in ([obj] + list(call_args)))
            out.append(f'    {_name(result)}={_name(cls_name+"__"+method)}({values});')
            types[result] = "int"
        elif op == "raise_typed":
            exc_type = args[0]
            out.append(f'    piton_raise("{exc_type}");')
        elif op == "try_push":
            pass  # no-op in flag-based model
        elif op == "try_pop":
            pass  # no-op in flag-based model
        elif op == "catch_flag":
            out.append(f'    {_name(result)}=piton_exc_flag;')
            types[result] = "bool"
        elif op == "catch_clear":
            out.append('    piton_catch_clear();')
        elif op == "math_sqrt":
            out.append(f'    {_name(result)}=pv_float(__builtin_sqrt((double)pv_payload({self._value(args[0])})));')
            types[result] = "float"
        elif op == "build_collection":
            kind, items = args[0], args[1]
            out.append(f'    {_name(result)}=(long)piton_collection_{kind}({len(items)});')
            types[result] = kind
        elif op == "get_item":
            coll, idx = args
            out.append(f'    {_name(result)}=piton_get_item({self._value(coll)},{self._value(idx)});')
            types[result] = "int"
        elif op == "collection_len":
            coll = args[0]
            out.append(f'    {_name(result)}=piton_collection_len({self._value(coll)});')
            types[result] = "int"
        elif op == "runtime_call":
            func_name = args[0]
            call_args = args[1] if len(args) > 1 else []
            values = ",".join(self._value(v) for v in call_args)
            out.append(f'    {_name(result)}={_name(func_name)}({values});')
            types[result] = "int"
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
