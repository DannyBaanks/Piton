"""Dashboard reproducible de cobertura de la Fase 14.

Los estados son declarativos y conservadores: una capacidad no se eleva a
PASS solo porque exista código bootstrap o un backend que emita instrucciones.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .native_evidence import EVIDENCE_GATES, VerifiedWindowsEvidence


STATES = {"PASS", "PARTIAL", "NOT_DEMONSTRATED", "INTENTIONALLY_UNSUPPORTED", "DESTROYED"}


@dataclass(frozen=True)
class Feature:
    name: str
    state: str
    evidence: str
    gap: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"invalid feature state: {self.state}")


@dataclass(frozen=True)
class Gate:
    name: str
    state: str
    reason: str


FEATURES = (
    Feature("syntax", "PASS", "tests/evidence.py", ""),
    Feature("expressions", "PASS", "tests/test_phase5.py", "native dynamic runtime supports all scalar + collection expressions"),
    Feature("statements", "PASS", "tests/test_phase5.py", "native branch, while, assign, augmented assign, return, if/elif/else"),
    Feature("functions", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "FUNCTION_ARGS_V1 + CALL_UNPACKING_DYNAMIC4_V1 + BOUND_METHODS_V1 + CALL_FRAME_ABI_V1 subset: defaults, kwargs, dynamic list/tuple/dict expansion, stored instance methods, and frame-backed functions/methods beyond four parameters; Win 6 + Linux 7 tests; decorators remain limited to plain-name module functions"),
    Feature("closures", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "CLOSURES_COMPLETE_V1 + VARIADIC_CLOSURE_V1 + initial frame ABI: closure-lifted functions use long* frame, dynamic captures, piton_closure_call_frame/piton_frame_call dispatch, 5 captures + 5 args, *args closures with runtime tuple packing; frame lifetime/GC full story + lambda unification remain open; Win 12 + Linux 11 tests"),
    Feature("exceptions", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "EXCEPTION_CUSTOM_V1 + EXCEPTION_RERAISE_V1 + EXCEPTION_BINDING_V1: custom exception classes with inheritance-chain matching + bare re-raise in exact-typed handlers + `excepto E como x` handler binding (bound to the caught message, Win 3 + Linux 2 tests); from/chains/BaseException/bare re-raise from catch-all open"),
    Feature("generators", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "GENERATOR_FRAME_V1 (real suspended frames, heap state machine, params, preserved locals, StopIteration on exhaustion) + GENERATOR_SEND_V1 + GENERATOR_THROW_V1 + GENERATOR_CLOSE_V1 + YIELD_FROM_V1 (delegation with send forwarding, sub return value stored; non-generator sources fail closed) + GENERATOR_RETURN_V1 (devolver v stored, exposed via gate; public attribute surface is a later milestone); defaults/vararg-closures pervasives"),
    Feature("classes", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "OBJECT_MODEL_RICH_V1: C3 MRO across multiple bases, method dispatch by MRO on both backends, super().metodo(...) zero-arg static resolution (method chains and constructors), self.metodo() via static self type; fail-closed: C3 conflict, super outside method, super().attr value, missing MRO attribute; cooperative diamond super with subtype self (differs from CPython), super(X,obj), metaclasses open; Win 16 + Linux 11 tests"),
    Feature("descriptors", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "DESCRIPTORS_V1: static @property data descriptor — @property/@x.setter/@x.deleter on native class methods; get_attr/set_attr/del_attr routed to getter/setter/deleter via MRO (inheritance + subclass shadow); symbols {Class}__{prop}(+__setter/__deleter); property names NOT registered as methods (obj.x() fail-closed); class_properties metadata on MIRModule; fail-closed mirrors CPython (property 'x' of 'P' object has no setter/has no deleter, not a method, requires the property getter, only @property, duplicate, is not a property); KNOWN_GAP: user descriptor classes (__get__/__set__/__set_name__) need runtime class objects; Win 12 + Linux 9 tests"),
    Feature("metaclasses", "NOT_DEMONSTRATED", "ROADMAP.md", "not implemented"),
    Feature("imports", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "IMPORT_PACKAGE_V1 + MODULE_METADATA_V1 + IMPORT_RELATIVE_V1 + IMPORT_STAR_V1 + IMPORT_CYCLIC_V1: paquetes con __init__.piton, pkg.fn(), desde pkg importar fn, submódulos desde pkg.sub importar fn + __name__/__package__/__file__/sys.modules; dotted importar pkg.sub (top bound, o modulo mas profundo con 'como P') + relative desde . importar x / desde .mod importar f en __init__ + cadenas pkg.sub.fn() + star desde pkg importar * (solo funciones publicas, excluye _privada) + ciclos de imports con orden de inicialización CPython (from-import vs módulo parcial -> ImportError espejo; importar X en ciclo = no-op) Win+Linux (desde .. a 2 niveles y cache de import siguen abiertos)"),
    Feature("async", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "COROUTINE_OBJECT_V1 + AWAIT_PROTOCOL_V1 + ASYNC_GENERATOR_V1 + ASYNC_FOR_V1 + TASK_SCHEDULER_V1: async def produces a real suspendible coroutine object (reuses the generator state machine + PitonGenerator layout); await suspends on the awaited coroutine; asyncio.run drives it via a depth-first scheduler (piton_coro_run) that runs each awaited coroutine to completion and feeds its return value back through sent_value; async generators callable without await (bare call OK, unlike coroutines), `asincrono para` drives them with agen_next/agen_done (+ for-else), awaits inside async-gen bodies work; nested await with params verified Win+Linux (3-deep chain + async-gen with await inside); TASK_SCHEDULER_V1: asyncio.create_task/await task/asyncio.gather (direct coroutine calls or task vars, done members immediate, cancelled member propagates CancelledError)/asyncio.sleep(0) cooperative yield/task.cancel() cascade with unhandled CancelledError at the root, deterministic FIFO byte-identical vs CPython, fail-closed on sleep(n>0)/non-awaits (Win 12 + Linux 10 tests); TASK_SCHEDULER_V2 adds real integer-second timers for sleep(n>0) on both backends (Win Sleep(ms), Linux nanosleep syscall 35, negative -> ValueError), blocking-inside-the-loop step semantics (interleave during a long sleep diverges from CPython, documented); ASYNC_WITH_V1 + ASYNC_EXCEPTION_V1 PASS (class coroutines for __aenter__/__aexit__ via gen_init+gen_yield; typed raise/catch inside coroutines); Win+Linux differential"),
    Feature("stdlib", "PASS", "tests/test_phase5.py", "native abs, min, max, sum, type, len, print, math.sqrt"),
    Feature("dynamic_code", "PARTIAL", "piton/runtime.py", "explicit bootstrap APIs; native execution absent"),
    Feature("introspection", "PARTIAL", "piton/runtime.py", "source contract only"),
    Feature("multiprocessing", "NOT_DEMONSTRATED", "ROADMAP.md", "Windows spawn not demonstrated"),
    Feature("ffi", "PARTIAL", "piton/stdlib_runtime.py", "explicit ctypes surface; native independence open"),
    Feature("dynamic_runtime", "PASS", "piton/native_runtime.c + tests/test_phase5.py + tests/test_phase10_linux.py", "PitonValue tagged union, refcounted heap, heterogeneous collections, dicts, sets; __del__ runs once on both backends; Windows has a registered shutdown cycle collector demonstrated on list self-cycles and object-list graphs; Linux arena reclamation remains open"),
)


FINAL_GATES = (
    Gate("GRAMMAR_PARITY", "PASS", "frontend/evidence corpus passes"),
    Gate("SEMANTIC_DIFFERENTIAL_CORPUS", "PASS", "73 differential tests: arithmetic, booleans, comparisons, strings, lists, dicts, sets, while, functions, if/elif/else, nested while, abs/min/max/sum, type, exceptions, classes, inheritance, bigint, floats, augmented assign"),
    Gate("NATIVE_RUNTIME", "PASS", "PitonValue tagged runtime with refcounted collections, dicts, sets and objects"),
    Gate("NATIVE_IMPORT_SYSTEM", "PASS", "importar + desde X importar Y, dotted importar pkg.sub, relative desde . importar x, star desde pkg importar *, ciclos de imports (orden de inicialización CPython), math.sqrt/asyncio.run builtins, fail-closed; >1-level relative and import cache remain open"),
    Gate("NATIVE_OBJECT_PROTOCOL", "PASS", "simple heap objects, fields, direct methods, single/multi-level inheritance"),
    Gate("NATIVE_EXCEPTION_MODEL", "PASS", "typed explicit raise/catch with flag-based unwind, finally support, differential-tested"),
    Gate("NATIVE_ASYNC", "PASS", "async def + await + asyncio.run differential-tested, coroutine escape detection; TASK_SCHEDULER_V1 covers create_task/gather/sleep(0)/cancel; TASK_SCHEDULER_V2 adds integer-second timers (Win Sleep / Linux nanosleep syscall, blocking step semantics); ASYNC_WITH_V1 + ASYNC_EXCEPTION_V1 closed"),
    Gate("ASYNC_WITH_V1", "PASS", "async with lowers __aenter__/__aexit__ as awaited class coroutines through gen_init + gen_yield; normal exit and exception unwind/suppression reuse the WITH_PROTOCOL_V1 handler path; Windows/Linux differential tests"),
    Gate("WITH_MULTIPLE_V1", "PASS", "con A() como a, B() como b: lowers as nested withs (innermost __exit__ first); suppression and propagation chain correctly through the synthetic try_handlers via RERAISE_COMPLETE_V1; Win+Linux differential + fail-closed message stays fail-closed only for unsupported context forms"),
    Gate("ATTRIBUTE_LOOKUP_V1", "PASS", "class-defined __getattr__/__setattr__/__delattr__ hooks routes normal stores/reads through the native method; the hook body itself bypasses the hook (no infinite recursion); method names win over the hook; Win+Linux differential"),
    Gate("IMPORT_RELATIVE_V2", "PASS", "N levels of relative imports (`desde N importar x` / `desde N.mod importar f`) — base walks (N-1) segments from the module's package, and the only fail-closed is escaping past the top package; Win+Linux native differential"),
    Gate("SPECIAL_METHOD_LOOKUP_V1", "PASS", "== over native class instances dispatches to the class-defined __eq__ (via MRO); `es` identity op works in both backends (compares scalar values and object pointers). Two-class equality chain: same MRO resolution pattern as method_call; parse keeps es as a distinct operator"),
    Gate("ASYNC_EXCEPTION_V1", "PASS", "typed exceptions raised and caught inside coroutine state machines; handler binding and return through asyncio.run verified on Windows/Linux"),
    Gate("COROUTINE_OBJECT_V1", "PASS", "async def lowers to a real suspendible coroutine (MIRFunction.is_coroutine -> the same heap state machine as generators); an awaited async call inner() becomes a coroutine object via gen_init instead of inlining its body; a bare coroutine call outside await/asyncio.run still fails closed ('must be awaited'); coroutine return values (devolver v) surface to the awaiter; Win+Linux tests"),
    Gate("AWAIT_PROTOCOL_V1", "PASS", "await suspends the enclosing coroutine on the awaited coroutine (gen_yield of the inner coroutine object); asyncio.run(coro()) emits coro_run which drives the coroutine depth-first via piton_coro_run: runs each awaited coroutine to completion and feeds its result back through sent_value at the resume point; nested await with params and multi-level chains verified byte-identical vs CPython Win+Linux (3-deep chain base/medio/cima); depth-first is a valid sequential scheduler; concurrent scheduling is TASK_SCHEDULER_V1"),
    Gate("ASYNC_GENERATOR_V1", "PASS", "an async function whose body contains produ (yield) anywhere is an async generator: calling it creates an object in ANY context (no 'must be awaited', unlike coroutines); data yields (producir) lower to agen_emit, awaits inside the body lower to gen_yield; both suspend via the same PitonGenerator state machine; a dedicated await-marker slot (PitonGenerator.slots[63], reserved by the layout guard) distinguishes a yield whose value is a coroutine-to-run from a data yield — piton_agen_next (Win) / inline C (Linux) drives the generator: runs awaited coroutines to completion via piton_coro_run, feeds results back through sent_value, returns data yields to the caller and signals StopAsyncIteration when finished; awaiting an async generator object fails closed ('cannot await an async generator'), mirroring CPython; Win 8 + Linux 3 differential/negative tests byte-identical vs CPython"),
    Gate("ASYNC_FOR_V1", "PASS", "`asincrono para x en <async-gen-call>:` lowers to gen_init + a loop of agen_next/agen_done/branch (async-gen only; any other iterable fails closed 'requires an async generator call'); the driver handles awaits inside the async-gen body automatically; orelse (`sino:`) mirrors sync for-else via a per-loop `@for_else_N` flag (runs iff no romper); `asincrono para` outside an async function fails closed ('only valid inside a native async function'); and _lower_forstmt was fixed to propagate is_async from CST to HIR (it was silently dropped); Win 8 + Linux 3 differential/negative tests byte-identical vs CPython"),
    Gate("TASK_SCHEDULER_V1", "PASS", "round-robin cooperative scheduler on both backends (Win native_runtime.c / Linux freestanding C): asyncio.run wraps the root coroutine in a PitonTask and drives a FIFO ready queue via piton_event_run/piton_step_task; asyncio.create_task(coro()) and asyncio.gather (args are direct coroutine calls OR existing task variables — already-finished members return their values immediately, a cancelled member aborts the gather with CancelledError) lower through task_new/gather_new/gather_add; awaiting a task or gather suspends the WHOLE inline await chain (PitonTask.chain[64], chain[0] = the task's own coroutine) so depth-first `esperar coro()` parent frames survive sleeps; asyncio.sleep(0) lowers to sleep0 -> PITON_SLEEP0_MAGIC, a cooperative re-queue (asyncio.sleep(n>0) fails closed TypeError 'native asyncio.sleep currently supports sleep(0) only'); task.cancel() (intercepted in MIR during attr-call lowering — no static receiver type, so a user class method named cancel() called on an instance is intercepted too, documented V1 limitation) sets cancel_requested: the task is dropped on its next pop, its direct waiters AND gather waiters are cancelled recursively, and a cancelled root surfaces as unhandled CancelledError (exit 1 Win / exit 2 Linux); pointer-floor guards on coro/task/gather dispatch mean awaiting a bare int, cancelling a non-task, or gathering a non-task fails closed with TypeError ('object is not awaitable' / 'object has no attribute cancel' / 'gather requires tasks') instead of crashing; gather result lists are owned by the awaiting coroutine (freed at gen exit — teardown live-count tripwire exits 0); deterministic FIFO interleave order byte-identical vs CPython for the tested subset; Win 12 + Linux 10 differential/negative tests"),
    Gate("TASK_SCHEDULER_V2", "PASS", "real integer-second timers for asyncio.sleep(n>0) on both backends: Win uses Sleep(delay*1000) (native_runtime.c), Linux uses the freestanding nanosleep syscall (nr=35, inline asm) — no libc in either; negative delay fails closed with ValueError; the sleep runs INSIDE piton_sleep0 before the task is re-queued (blocking step, deterministic with the single-threaded FIFO loop; concurrent interleave during a long sleep differs from CPython and is documented as divergent for now); regression: full phase5+phase10 green"),
    Gate("CLOSURES_COMPLETE_V1", "PASS", "closure objects with escape + mutable cells (no_local) Win+Linux, initial frame ABI with dynamic captures and long* lifted functions, frame dispatch for direct and escaped closures, callbacks, chained factories, 5 captures + 5 args; VARIADIC_CLOSURE_V1: *args closures escape with extras packed into a tuple at frame[n_cells+n_args] (int-element subset); lifetime of the pack tuple is runtime-managed; full lambda→closure migration and cell GC remain open"),
    Gate("EXCEPTION_CUSTOM_V1", "PASS", "custom exception classes subclassing builtin exception bases or other custom classes rooted at Exception; lanzar accepts any class whose heritage chain includes Exception (fail-closed otherwise); excepto matching by full ancestry (own class, custom ancestor, builtin ancestor, Exception); handler non-matching leaves the exception uncaught -> stderr type: msg + exit; uncaught custom no longer swallowed by spurious runtime stack frames; Win 11 + Linux 11 differential/negative tests"),
    Gate("EXCEPTION_RERAISE_V1", "PASS", "bare lanzar in exact-typed handlers: reraise_save at handler entry before catch_clear, raise_active dispatches to enclosing handler via static labels (nested tries work), fail-closed in MIR outside handlers or from catch-all excepto Exception; unhandled re-raise -> stderr type: msg + exit(1) via piton_reraise_unhandled (Win) / report_unhandled (Linux); helpers added to native_runtime.c and the Linux freestanding C"),
    Gate("EXCEPTION_BINDING_V1", "PASS", "`excepto E como x` binds the caught exception message to a handler-scoped name before catch_clear (piton_catch_message_safe on Win returns '' when NULL; Linux reads piton_exc_message with '' fallback); single-handler try only (except* fail-closed); Win 3 + Linux 2 differential tests byte-identical vs CPython"),
    Gate("EXCEPTION_CHAINING_V1", "PASS", "lanzar A(...) desde B(...) — cause recorded before the raise; unhandled prints both lines ('Type: msg -> causada por'); caught path unchanged; `desde Nada` suppresses; Win+Linux differential + unhandled-stderr test"),
    Gate("BASE_EXCEPTION_V1", "PASS", "BaseException added to _BUILTIN_EXCEPTIONS and the chain; every exception chain now terminates at BaseException, so `excepto BaseException` is a real catch-all (still distinguishable from Exception for reraise bookkeeping); Win+Linux differential"),
    Gate("RERAISE_COMPLETE_V1", "PASS", "bare lanzar from a catch-all (excepto Exception / excepto BaseException) lowers to raise_active_dynamic against the enclosing handler using the reraise slots (exception_handlers pop their current entry before the body lowers, so handlers[-1] IS the outer); Linux mirrors by reading piton_reraise_type instead of piton_exc_type after catch_clear; Win+Linux differential + unhandled-exit tests"),
    Gate("GENERATOR_SEND_V1", "PASS", "generator send/enviar: piton_gen_send sets sent_value and resumes the state machine; sending a non-None value to a just-started generator raises TypeError (mirrors CPython); StopIteration on the call that exhausts; Win+Linux differential tests"),
    Gate("GENERATOR_RETURN_V1", "PASS", "generator devolver v (int subset) stores the value in the generator struct (slot 62 Linux / appended field Win) BEFORE finished=1; observable through 'producir desde sub()' which reads it via gen_retval; StopIteration.value attribute surface remains a MIR2 item"),
    Gate("GENERATOR_THROW_V1", "PASS", "generator throw/arrojar: since native generators cannot catch (yield inside try is fail-closed), throw() marks the generator finished and raises the exception at the caller's enclosing handler — matching CPython when the generator does not catch; piton_gen_throw (Win) and piton_gen_throw (Linux freestanding) added; Win+Linux tests"),
    Gate("GENERATOR_CLOSE_V1", "PASS", "generator close/cerrar: marks the generator finished and returns None; idempotent (closing twice is a no-op); a subsequent next() raises StopIteration; piton_gen_close added to both runtimes; Win+Linux tests"),
    Gate("YIELD_FROM_V1", "PASS", "`producir desde sub()` delegates a sub-generator: lowers to iter_next + gen_send loop with the parent's try_push routing StopIteration to the post-block; send values forward through the sub's state machine; the sub's return value is captured via gen_retval (needed to close RETURN_V1); non-generator sources (lists/dicts) fail closed with 'yield from over non-generator values is not supported yet'; Win+Linux differential"),
    Gate("MODULE_METADATA_V1", "PASS", "entry metadata globals __name__/__package__/__file__ (source-mode mirrors python -c: no __file__; files-mode adds the abs entry path) + bootstrap sys module object with a modules catalog (dict name -> module object) holding sys, __main__ and every imported native module; __package__ = None for __main__, '' for sys, '' for top-level modules, pkg name for packages/submodules; chained sys.modules['x'].__name__/__package__ identity preserved via composite static type dict:module and module-attr type map in get_attr; dynamic __package__ printing (None vs text) via piton_print_value / piton_print_dynamic; differential-tested vs CPython Win 3 + Linux 3; extra fixes landed: native bool/none literal print dispatch (imprimir(Verdadero) previously printed 0) and Linux string-keyed dict slots typed PK_STR"),
    Gate("IMPORT_RELATIVE_V1", "PASS", "dotted import binds the top package (or the deepest module under `como` asname, mirroring CPython `import a.b.c as X`); relative from-imports (`desde . importar numeros`, `desde .operaciones importar resta`) resolve against the containing package's own dotted name and register the target submodules as graph nodes; BFS fixed-point scan pulls sibling imports transitively; pkg.sub.fn() attr-chain calls lower to qualified static symbols (pkg__numeros__suma); sys.modules catalogs intermediates; fail-closed: relative imports in the entry module (no parent package, like CPython scripts), >1 level (`desde ..`), module attribute value access, keyword args in module calls, missing intermediate packages; Win 8 + Linux 6 differential/negative tests"),
    Gate("IMPORT_STAR_V1", "PASS", "desde pkg importar * binds every public funcion of the target module/package/__init__ as a bare name (underscore-prefixed functions excluded, mirroring CPython without __all__); works for packages (init functions), standalone modules and dotted submodules, byte-identical vs CPython Win+Linux; fail-closed: relative star in the entry (no parent package), star inside imported modules (entry-only bindings), star from builtin modules (math), and any mixed/explicit list (`desde m importar a, *`, `importar *`) rejected at parse exactly like CPython's SyntaxError; Win 14 (incl. private-exclusion white-box) + Linux 6 tests"),
    Gate("IMPORT_CYCLIC_V1", "PASS", "cyclic imports between native modules with CPython initialization order: MIR gives every imported module its OWN name scope (same-module bare calls, from-import aliases incl. relative `desde .` / `desde .mod` against the module's package context, module aliases) pushed on a stack during lifting, each _Builder reading module_aliases + from_import_aliases from the active scope (entry <module> included); the x86 scan simulates CPython execution order over static bodies (NOT_STARTED -> IN_PROGRESS -> DONE + live namespace per module): a from-import fully imports the target first, and if the target is still IN_PROGRESS (a cycle) the name must already be bound or the build fails fail-closed mirroring `cannot import name ... from partially initialized module`; a plain importar X mid-cycle is a no-op like CPython; an import of a never-defined name also fails-closed instead of emitting a missing symbol; proven by a native heap-corruption crash (0xC0000374) that the per-module scope fixes; Win 4 + Linux 4 differential/negative tests"),
    Gate("OBJECT_MODEL_RICH_V1", "PASS", "C3 MRO computed in MIR (memoized, bases validated, builtin exceptions as leaf [name]) and closed into MIRModule.class_mro at the end of lower(); method_call dispatches by MRO then falls back to computed parent chain in both x86 and Linux backends; super() zero-arg lowers to method_call(first class after the current one in MRO, attr, receiver, args) with fail-closed guards (outside method, bare value, missing MRO attribute, nested closures do not inherit super_context); __init__ discovery and exception matching walk the MRO; MIRFunction.self_class types the first param as object:{Class} enabling self.metodo() inside methods; parser Name-call path now wraps _parse_call in _parse_postfix enabling foo().bar(); Win 10 + Linux 9 differential/negative tests, byte-identical vs CPython; cooperative diamond super with subtype self and super(X,obj) documented as open"),
    Gate("NATIVE_STDLIB_DECLARED_SCOPE", "PASS", "native abs, min, max, sum, type, len, print, math.sqrt demonstrated"),
    Gate("CPYTHON_EXECUTION_DEPENDENCY", "PASS", "PE/ELF executables proven free of python DLLs and binary markers; runs in empty env, chroot, QEMU"),
    Gate("X86_64_WINDOWS", "PASS", "20/20 programs compile+execute+correct output: hola, arithmetic, booleans, strings, lists, dicts, while, functions, if/elif/else, exceptions, classes, inheritance, bigint, floats, stdlib, augmented assign, nested while, ternary, multi-function, sets"),
    Gate("X86_64_LINUX", "PASS", "32 tests: static ELF scalar+rich differential vs CPython (floats, lists, dicts, sets, tuples, objects, methods, inheritance, multilevel inheritance, exceptions, stdlib abs/min/max/sum/type/len, math.sqrt, augmented assign, nested while, ternary, multifunction), empty env, chroot, QEMU sole userspace, no-python-marker"),
    Gate("CLEAN_MACHINE_EXECUTION", "PASS", "static ELF boots as /init and sole userspace in a QEMU VM"),
    Gate("DYNAMIC_RUNTIME_V1", "PASS", "PitonValue tagged union, refcounted heap strings, heterogeneous collections, dicts, sets, recursive print"),
    Gate("FULL_PARITY", "PASS", "declared native subset parity demonstrated on both backends: Windows x86-64 PE and Linux x86-64 ELF both compile+execute the rich subset (objects, inheritance, exceptions, collections, floats, stdlib, math.sqrt, from-import subset) byte-identical vs CPython, with no CPython/libc in the executables"),
    Gate("EFFECT_CLASSIFICATION_V1", "PASS", "ME: every MIR op classified PURE/READ/WRITE/IO/OPAQUE at the end of lowering (piton/mir.py MIR_OP_EFFECTS, 61 ops); unknown/invalid op fails closed with MIRLoweringError; tests/test_effect_lattice.py"),
    Gate("EFFECT_TOKEN_CHAIN_V1", "PASS", "ME: effect token chained in block-list order through every non-PURE instruction; verify_effect_chain recomputes and fails on tamper or unclassified nodes (tests/test_effect_lattice.py)"),
    Gate("EFFECT_NEUTRALITY_V1", "PASS", "ME is a pure annotator: differential corpus unchanged (test_effect_lattice.py small battery + full test_phase5.py and test_phase10_linux.py regressions identical pre/post)"),
    Gate("MIR_HASH_REBASELINE_V1", "PASS", "MIRInstruction.to_dict() schema now includes effects/token/token_prev; determinism gate preserved (same input -> identical to_json); optimizer const-fold rebuild keeps fields via dataclasses.replace"),
    Gate("BUILTINS_CORE_V2", "PASS", "M14 first slice: all/any (list|tuple, full per-tag/slot truthiness incl. str '' and float 0.0), bin (incl. negatives '-0b...'), chr/ord with FULL UTF-8 encode/decode (ord('ñ')=241, chr(128512)='😀'), pow (int base with exponent>=0 -> int; float base with int exponent incl. negatives -> float), round (int identity; float -> int with round-half-even, CPython semantics: round(2.5)=2, round(-3.5)=-4); out-of-matrix inputs fail closed with CAPTURABLE exceptions: the MIR `call` op now carries the innermost active handler label (_active_handler in mir.py), Win helpers raise via piton_raise + piton_catch_flag post-check, Linux via piton_raise_set + `if(piton_exc_flag) goto handler` (piton_report_unhandled+exit when no handler); declared divergences from CPython: pow(int, negative-int) raises TypeError instead of returning float, any/all require list|tuple (dict/set/iterators fail closed), round() rejects the ndigits second argument; differential byte-parity tests vs CPython Win 2 + Linux 2 (parity battery + intentar/excepto catchability battery)"),
    Gate("TYPE_CONVERSION_V1", "PASS", "M14 conversion matrix: int/entero from int, bool, float and decimal strings; float/decimal from int, bool, float and strings; str/texto from int, float, bool, None and str; bool/booleano from scalar, string and collection truthiness. Invalid literals raise capturable ValueError instead of the previous native access violation. Win+Linux differential and catchability tests."),
    Gate("MATH_TIER1_V1", "PASS", "M14 math matrix: sqrt, floor, ceil, trunc, fabs, gcd plus pi/e constants; Win CRT and Linux freestanding implementations produce byte-identical tested results vs CPython (including negative floor/ceil and gcd(0, 7))."),
    Gate("LIFETIME_CONTRACT_V1", "PASS", "M13 first slice: deterministic cleanup is the con/context-manager protocol, with __enter__/__exit__ exactly-once behavior on normal exit, exception propagation, suppression and unhandled exception paths; Win+Linux differential tests. Exact __del__ invocation time is intentionally not a contract; GC cycles and weakrefs remain open."),
    Gate("FINALIZERS_V1", "PASS", "M13 finalizers: class __del__ is attached at object construction through MRO and invoked exactly once before process exit. Windows runs it on refcount collection or the cycle collector; Linux registers heap objects and finalizes them before returning from module main. Scope: one-argument __del__(self), no frame-ABI finalizer, exceptions/resurrection inside __del__ are not yet specified; Win 2 + Linux 2 differential tests."),
    Gate("GC_CYCLES_V1", "PASS", "Windows native_runtime.c: gc_nodes[] + piton_gc_collect (snapshot/protect/detach/free) reclaims list self-cycles and object-list graphs. Linux linux_x86.py: freelist heap + refcounting + identical GC algorithm; self-contained C-harness test (CycleGCLinuxV1) proves list self-cycles and object-list mutual cycles reclaim to zero. Cross-platform C-evidence: Win64 CycleGCNativeV1 + Linux CycleGCLinuxV1 both PASS. Dict/set cycles, public gc.collect(), and mid-program collection remain open."),
)


def _native_subset_milestone(evidence: VerifiedWindowsEvidence | None) -> Gate:
    if evidence is None:
        return Gate("NATIVE_SUBSET_1_0", "NOT_DEMONSTRATED", "run `piton compilar --evidencia RECEIPT.json` and pass the receipt to this dashboard")
    if not isinstance(evidence, VerifiedWindowsEvidence):
        raise TypeError("native subset dashboard input must come from load_windows_evidence()")
    receipt = evidence.receipt
    valid = (
        receipt.get("schema") == "piton-native-subset-evidence-v1"
        and receipt.get("native_subset_1_0") == "PASS"
        and set(receipt.get("gates", {})) == EVIDENCE_GATES
        and all(state == "PASS" for state in receipt["gates"].values())
    )
    if valid:
        return Gate("NATIVE_SUBSET_1_0", "PASS", "x86-64 PE, empty-env execution, no Python import/marker and CPython differential match")
    return Gate("NATIVE_SUBSET_1_0", "NOT_DEMONSTRATED", "receipt is absent, invalid or contains a failing gate")


def build_dashboard(native_subset_receipt: VerifiedWindowsEvidence | None = None) -> dict[str, Any]:
    milestone = _native_subset_milestone(native_subset_receipt)
    release_ready = all(gate.state == "PASS" for gate in FINAL_GATES)
    full_parity = any(gate.name == "FULL_PARITY" and gate.state == "PASS" for gate in FINAL_GATES)
    return {
        "schema": "piton-phase14-dashboard-v1",
        "phase": 14,
        "features": [asdict(feature) for feature in FEATURES],
        "gates": [asdict(gate) for gate in FINAL_GATES],
        "milestones": [asdict(milestone)],
        "native_subset_ready": milestone.state == "PASS",
        "release_ready": release_ready,
        "full_parity": full_parity,
        "verdict": "PASS" if release_ready else "PARTIAL",
    }


def render_markdown(dashboard: dict[str, Any]) -> str:
    lines = [
        "# Pitón Fase 14 Dashboard", "",
        f"- Native Subset 1.0: **{'PASS' if dashboard['native_subset_ready'] else 'NOT DEMONSTRATED'}**",
        f"- Release x86 parity: **{'PASS' if dashboard['release_ready'] else 'NOT READY'}**",
        "", "## Features", "", "| Feature | Estado | Evidencia | Hueco |", "|---|---|---|---|",
    ]
    for feature in dashboard["features"]:
        lines.append(f"| {feature['name']} | {feature['state']} | `{feature['evidence']}` | {feature['gap']} |")
    lines.extend(["", "## Gates", "", "| Gate | Estado | Razón |", "|---|---|---|"])
    for gate in dashboard["gates"]:
        lines.append(f"| {gate['name']} | {gate['state']} | {gate['reason']} |")
    lines.extend(["", "## Milestones", "", "| Milestone | Estado | Razón |", "|---|---|---|"])
    for milestone in dashboard["milestones"]:
        lines.append(f"| {milestone['name']} | {milestone['state']} | {milestone['reason']} |")
    return "\n".join(lines) + "\n"


def render_summary(dashboard: dict[str, Any]) -> str:
    subset = "PASS" if dashboard["native_subset_ready"] else "NOT_DEMONSTRATED"
    parity = "PASS" if dashboard["release_ready"] else "NOT_READY"
    return f"NATIVE_SUBSET_1_0 = {subset}\nFULL_PARITY = {parity}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the Pitón Phase 14 dashboard")
    parser.add_argument("--format", choices=("json", "markdown", "summary"), default="json")
    parser.add_argument("--native-receipt", type=Path)
    args = parser.parse_args()
    receipt = None
    if args.native_receipt:
        from .native_evidence import load_windows_evidence
        from .x86 import NativeBuildError
        try:
            receipt = load_windows_evidence(args.native_receipt)
        except (OSError, ValueError, NativeBuildError) as error:
            print(f"PITON_NATIVE_EVIDENCE_ERROR\n{error}", file=sys.stderr)
            return 2
    dashboard = build_dashboard(receipt)
    if args.format == "markdown":
        print(render_markdown(dashboard), end="")
    elif args.format == "summary":
        print(render_summary(dashboard), end="")
    else:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if dashboard["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
