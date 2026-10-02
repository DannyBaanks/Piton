"""Backend x86-64 Win64 mínimo desde MIR.

El alcance inicial es int/string, variables locales, aritmética, comparaciones,
branch/jump, funciones simples y ``imprimir`` mediante el CRT de Windows.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Any

from piton.lower import LoweringError, lower_cst_to_hir
from piton.hir import HIRKind
from piton.mir import MIRBlock, MIRFunction, MIRInstruction, MIRLoweringError, MIRModule, lower_hir_to_mir
from piton.parser import parse


class NativeBuildError(RuntimeError):
    pass


def _ordering_pair_ok(left_type: str, right_type: str) -> bool:
    """ORDER_MIXED_TYPES_V1: can these two static types be ordered?

    Mirrors the Linux backend: numeric against numeric, same collection type,
    or two strings. Everything else (None with anything, int with str, list
    with str, ...) is unorderable and must be refused instead of comparing the
    raw representations.
    """
    numeric = {"int", "bool", "float", "bigint"}
    if left_type in numeric and right_type in numeric:
        return True
    if left_type in {"list", "tuple", "dict", "set"} and left_type == right_type:
        return True
    return left_type == right_type and left_type not in {"none", ""}


_BUILTINS = {"imprimir", "print", "rango", "range", "longitud", "len", "enumerar", "enumerate", "abs", "max", "min", "sum", "tipo", "type", "texto", "str", "entero", "int", "decimal", "float", "booleano", "bool", "lista", "list", "tupla", "tuple", "conjunto", "set", "diccionario", "dict", "entrada", "input", "abrir", "open", "ordenar", "sorted", "all", "any", "bin", "chr", "ord", "pow", "round", "redondear"}

_math_fn_map = {
    "math_sqrt": "piton_float_sqrt",
    "math_sin": "piton_float_sin",
    "math_cos": "piton_float_cos",
    "math_log": "piton_float_log",
}

PITON_GEN_MAX_SLOTS = 64
PITON_GEN_LOCAL_BASE = 48
# ASYNC_GENERATOR_V1: the LAST persisted slot of every PitonGenerator object is
# reserved as the "await marker" flag for async-generator driving. A data yield
# (producir / agen_emit) clears it; an await yield (esperar / gen_yield inside an
# async generator) sets it, so piton_agen_next can distinguish "yielded data"
# from "yielded a coroutine to run" without dereferencing arbitrary data values.
# Layout indices 0..62 are user data; index 63 is the flag.
PITON_GEN_AWAIT_FLAG_SLOT = 63
PITON_GEN_AWAIT_FLAG_OFFSET = PITON_GEN_LOCAL_BASE + PITON_GEN_AWAIT_FLAG_SLOT * 8


def generator_slot_layout(function: MIRFunction) -> dict[str, int]:
    """Shared heap-persisted slot order for generator bodies (Win64 and Linux).

    Every mutable slot (params, temps, stored names) must survive across
    suspension points, so both backends mirror it into the generator object
    at the same index. Scratch slots (``@scratch*``/``%d*``) are transient
    within one instruction and are never live across a yield.
    """
    if function.frame_abi:
        raise NativeBuildError(f"native generator '{function.name}' with frame ABI is not supported yet")
    if function.cell_vars:
        raise NativeBuildError(f"native generator '{function.name}' with closures is not supported yet")
    if function.vararg or function.kwarg:
        raise NativeBuildError(f"native generator '{function.name}' with *args/**kwargs is not supported yet")
    order: list[str] = []
    seen: set[str] = set()

    def add(name: Any) -> None:
        if not isinstance(name, str) or name in seen:
            return
        if name in ("@scratch0", "@scratch1", "@scratch2", "@scratch3",
                    "%d0", "%d1", "%d2", "%d3", "@gen_ptr", "@gen_result"):
            return
        seen.add(name)
        order.append(name)

    for param in function.params:
        add(param)
    for block in function.blocks:
        for instruction in block.instructions:
            add(instruction.result)
            if instruction.op == "store" and instruction.args:
                add(instruction.args[0])
    if len(order) >= PITON_GEN_MAX_SLOTS:
        raise NativeBuildError(
            f"native generator '{function.name}' needs {len(order)} persisted slots "
            f"(max {PITON_GEN_MAX_SLOTS - 1}; slot {PITON_GEN_AWAIT_FLAG_SLOT} is reserved for the async-generator await marker)"
        )
    return {name: index for index, name in enumerate(order)}


class Win64NasmEmitter:
    def __init__(self):
        self.lines: list[str] = []
        self.slots: dict[str, int] = {}
        self.next_slot = 8
        self.strings: dict[str, str] = {}
        self.next_string = 0
        self.aliases: dict[str, str] = {}
        self.types: dict[str, str] = {}
        self._pushed_iters: set[str] = set()
        self._popped_iters: set[str] = set()
        self._loop_exit_pops: dict[str, int] = {}
        self.tuple_etypes: dict[str, tuple] = {}
        self.function_return_types: dict[str, str] = {}
        self.dict_elems: dict[str, dict[str, tuple]] = {}
        self._func_globals: dict[str, set[str]] = {}
        self._func_stores: dict[str, set[str]] = {}
        self._module_stored: set[str] = set()
        self._module_types: dict[str, str] = {}
        self._shared_globals: set[str] = set()
        self.next_internal_label = 0
        self.owned_slots: list[tuple[str, str]] = []
        self.bigint_slots: list[str] = []
        self.constants: dict[str, Any] = {}
        self.cell_types: dict[str, str] = {}
        self.generator_layouts: dict[str, dict[str, int]] = {}
        self._gen_resume_labels: list[str] = []
        self._gen_yield_counter = 0

    def _generator_layout(self, function: MIRFunction) -> dict[str, int]:
        """Compute the heap-persisted slot order for a generator body.

        Every mutable slot (params, temps, stored names) must survive across
        ``ret`` suspension points, so it is mirrored into ``PitonGenerator.locals``.
        Scratch slots (``@scratch*``/``%d*``) are transient within one
        instruction and are never live across a yield.
        """
        return generator_slot_layout(function)

    def emit(self, module: MIRModule) -> str:
        self.mir_module = module
        self.mir_module_classes = getattr(module, 'classes', {})
        self.mir_module_class_mro = getattr(module, 'class_mro', {})
        self.mir_module_class_properties = getattr(module, 'class_properties', {})
        self.function_names = {function.name for function in module.functions}
        self.function_return_types = self._infer_return_types(module)
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
        self.function_param_map = {function.name: list(function.params) for function in module.functions}
        self.function_frame_abi = {function.name: bool(function.frame_abi) for function in module.functions}
        self._scan_module_globals(module)
        # CALL_UNPACKING_DYNAMIC4_V1: temp/local names of dicts proven to be
        # built from constant-string keys (the only **-unpackable dicts).
        self.strkey_dict_temps: set[str] = set()
        # DICT_KEY_TYPE_V1: static type of a dict's KEYS, so iterating a dict
        # yields the right static type. The iterator used to hardcode "str",
        # which made `para k en {1: 'a'}` print the int key as a string pointer.
        self._dict_key_types: dict[str, str] = {}
        # Per-call-site `dq` tables of parameter names for dict unpacking,
        # emitted into .rdata at the end of the module.
        self.unpack_tables: list[tuple[str, list[str]]] = []
        self.generator_layouts = {}
        for function in module.functions:
            if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
                self.generator_layouts[function.name] = self._generator_layout(function)
        self.lines = [
            "default rel", "extern printf", "extern strcmp", "extern strlen",
            "extern malloc", "extern memcpy", "section .text",
        ]
        self.lines[0:0] = [
            "extern piton_collection_new", "extern piton_collection_put",
            "extern piton_collection_put_tagged", "extern pv_int",
            "extern piton_collection_len", "extern piton_collection_get",
            "extern piton_list_append",
            "extern piton_genexpr_new", "extern piton_genexpr_iter", "extern piton_genexpr_next", "extern piton_genexpr_free",
            "extern piton_gen_new", "extern piton_gen_next", "extern piton_gen_send", "extern piton_gen_throw", "extern piton_gen_close", "extern piton_gen_free", "extern piton_gen_collect", "extern piton_gen_return_set", "extern piton_gen_return_value",
            "extern piton_coro_run", "extern piton_agen_next",
            "extern piton_event_run", "extern piton_task_new", "extern piton_task_cancel", "extern piton_sleep0", "extern piton_gather_new", "extern piton_gather_add",
            "extern piton_raise_chain",
            "extern piton_sorted_new",
            "extern piton_iterator_new_any", "extern piton_iterator_next_any",
            "extern piton_calliter_new", "extern piton_calliter_next",
            "extern piton_enumerate_new", "extern piton_enumerate_next",
            "extern piton_reversed_new", "extern piton_reversed_next",
            "extern piton_zip_new", "extern piton_zip_next",
            "extern piton_callback_iterator_new", "extern piton_callback_iterator_next",
            "extern piton_collection_print", "extern piton_collection_print_raw", "extern piton_collection_free",
            "extern piton_gc_collect",
            "extern piton_collection_live_count",
            "extern piton_dict_new", "extern piton_dict_put",
            "extern piton_dict_len", "extern piton_dict_get",
            "extern piton_dict_print", "extern piton_dict_print_raw", "extern piton_dict_free",
            "extern piton_dict_live_count",
            "extern piton_set_new", "extern piton_set_add",
            "extern piton_set_len", "extern piton_set_print", "extern piton_set_print_raw",
            "extern piton_set_free", "extern piton_set_live_count",
            "extern piton_raise",
            "extern piton_raise_unhandled",
            "extern piton_exit",
            "extern piton_argv_new",
            "extern piton_try_push", "extern piton_try_pop", "extern piton_try_set_accepted",
            "extern piton_catch_flag", "extern piton_catch_type", "extern piton_catch_message", "extern piton_catch_message_safe", "extern piton_catch_clear",
            "extern piton_reraise_save", "extern piton_reraise", "extern piton_reraise_unhandled",
            "extern piton_abs_int", "extern piton_abs_float",
            "extern piton_min_int", "extern piton_max_int", "extern piton_min_float", "extern piton_max_float",
            "extern piton_sum_collection", "extern piton_sum_dict", "extern piton_sum_set",
            "extern piton_all_iterable", "extern piton_any_iterable",
            "extern piton_pow_int", "extern piton_pow_float",
            "extern piton_ord", "extern piton_chr", "extern piton_bin", "extern piton_round_float",
            "extern piton_percent_chr", "extern piton_str_from_int_base", "extern piton_str_pad",
            "extern piton_str_apply_spec",
            "extern piton_int_from_str", "extern piton_float_from_str",
            "extern piton_str_from_int", "extern piton_str_from_bool",
            "extern piton_str_from_none", "extern piton_str_from_float",
            "extern piton_str_truthy",
            "extern piton_math_floor", "extern piton_math_ceil", "extern piton_math_trunc",
            "extern piton_math_fabs", "extern piton_math_gcd",
            "extern piton_float_sqrt",
            "extern piton_float_sin", "extern piton_float_cos", "extern piton_float_log",
            "extern piton_type_name", "extern piton_type_from_raw",
            "extern piton_object_new", "extern piton_object_new_with_parent", "extern piton_object_new_with_finalizer", "extern piton_object_set", "extern piton_object_set_tagged", "extern piton_object_get", "extern piton_object_lookup",

            "extern piton_object_free", "extern piton_object_live_count",
            "extern piton_print_float", "extern piton_print_float_raw",
            "extern piton_print_value", "extern piton_print_value_raw",
            "extern piton_bigint_from_str", "extern piton_bigint_from_i64", "extern piton_bigint_free",
            "extern piton_bigint_add", "extern piton_bigint_sub", "extern piton_bigint_mul",
            "extern piton_bigint_neg", "extern piton_bigint_cmp", "extern piton_bigint_cmp_int",
            "extern piton_bigint_floor_div", "extern piton_bigint_mod",
            "extern piton_bigint_print", "extern piton_bigint_print_raw",
            "extern piton_bigint_pow_small",
            "extern piton_seq_concat",
            "extern piton_seq_repeat_n",
            "extern piton_int_truediv",
            "extern piton_float_div", "extern piton_float_floor_div", "extern piton_float_mod",
            "extern piton_float_pow_v1",
            "extern piton_str_contains", "extern piton_seq_contains",
            "extern piton_str_index", "extern piton_str_slice", "extern piton_seq_slice",
            "extern piton_str_slice_step", "extern piton_seq_slice_step",
            "extern piton_str_iterator_new", "extern piton_str_iterator_next",
            "extern piton_str_len", "extern piton_str_repeat", "extern piton_str_cmp",
            "extern piton_seq_pop", "extern piton_seq_reverse", "extern piton_seq_insert",
            "extern piton_seq_count", "extern piton_seq_sort",
            "extern piton_dict_get_d", "extern piton_dict_get_1",
            "extern piton_str_case", "extern piton_str_find", "extern piton_str_startswith",
            "extern piton_str_quote", "extern piton_str_single_char",
            "extern piton_str_endswith", "extern piton_str_replace", "extern piton_str_split",
            "extern piton_str_strip", "extern piton_str_join", "extern piton_str_format",
            "extern piton_dict_contains", "extern piton_set_contains",
            "extern piton_closure_new8", "extern piton_closure_call6",
            "extern piton_closure_new_frame", "extern piton_closure_call_frame", "extern piton_bound_method_new", "extern piton_bound_method_self",
            "extern piton_frame_call",
            "extern piton_unpack_seq4", "extern piton_dict_unpack4",
        ]
        for function in module.functions:
            self._emit_function(function)
        self.lines.append("section .rdata")
        # Intern param-name strings BEFORE dumping the string table, so the
        # call_unpack name tables can reference them.
        for _table_label, table_names in self.unpack_tables:
            for name in table_names:
                self._string(name)
        for value, label in self.strings.items():
            self.lines.append(f"{label}: {self._nasm_db(value)}")
        for table_label, table_names in self.unpack_tables:
            refs = ", ".join(self._string(name) for name in table_names)
            self.lines.append(f"{table_label}: dq {refs}")
        self.lines.extend([
            'fmt_int: db "%lld", 10, 0',
            'fmt_float: db "%.17g", 10, 0',
            'fmt_str: db "%s", 10, 0',
            'fmt_int_raw: db "%lld", 0',
            'fmt_str_raw: db "%s", 0',
            'fmt_nl: db 10, 0',
            'lit_space: db " ", 0',
            'lit_true: db "True", 0',
            'lit_false: db "False", 0',
            'lit_none: db "None", 0',
        ])
        if self._shared_globals:
            # GLOBAL_DECL_V1: writable file-scope cells (the fmt block above
            # lives in .rdata).
            self.lines.append("section .data")
            for name in sorted(self._shared_globals):
                self.lines.append(f"{self._global_label(name)}: dq 0")
        return "\n".join(self.lines) + "\n"

    def _emit_function(self, function: MIRFunction) -> None:
        self.function = function
        if getattr(function, "is_generator", False) or getattr(function, "is_coroutine", False):
            self._emit_generator_function(function)
            return
        self.slots = {}
        self.next_slot = 8
        self.aliases = {}
        self.types = {}
        self.owned_slots = []
        self._pushed_iters = set()
        self._popped_iters = set()
        self._loop_exit_pops = {}
        self.tuple_etypes = {k: v for k, v in self.tuple_etypes.items() if not k.startswith('%')}
        self.dict_elems = {k: v for k, v in self.dict_elems.items() if not k.startswith('%')}
        # NOTE: the GLOBAL_DECL_V1 tables are set once by _scan_module_globals
        # in emit() and must NOT be reset per function (that wiped them before
        # any instruction was emitted).
        self.bigint_slots = []
        self.constants = {}
        self._boolh_types: dict[str, str] = {}
        if function.vararg:
            self.types[function.vararg] = "tuple"
        if function.kwarg:
            self.types[function.kwarg] = "dict"
        for block in function.blocks:
            for instruction in block.instructions:
                self._reserve(instruction.result)
                if instruction.op == "build_collection" and instruction.result:
                    coll_kind = instruction.args[0] if instruction.args else ""
                    free_fn = {"dict": "piton_dict_free", "set": "piton_set_free"}.get(coll_kind, "piton_collection_free")
                    self.owned_slots.append((instruction.result, free_fn))
                if instruction.op == "genexpr_new" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_genexpr_free"))
                if instruction.op == "gen_init" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_gen_free"))
                if instruction.op == "object_new" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_object_free"))
                if instruction.op == "store":
                    self._reserve(instruction.args[0])
                if instruction.op == "call_unpack":
                    for unpack_slot in ("@unpack_buf0", "@unpack_buf1", "@unpack_buf2", "@unpack_buf3", "@unpack_filled", "@unpack_mask"):
                        self._reserve(unpack_slot)
        for scratch in ("@scratch0", "@scratch1", "@scratch2", "@scratch3"):
            self._reserve(scratch)
        for default_slot in ("%d0", "%d1", "%d2", "%d3"):
            self._reserve(default_slot)
        # Reserve parameter slots BEFORE computing the frame: the Win64 ABI
        # shadow area starts at rsp, so params stored deeper than next_slot
        # (created later by _store_slot) would land inside [rsp..rsp+31] and
        # get clobbered by every callee, particularly printf's varargs spill.
        for name in function.params:
            self._reserve(name)
        # Keep the Win64 32-byte shadow area below every local slot.
        frame = max(48, ((self.next_slot + 32 + 15) // 16) * 16)
        label = "main" if function.name == "<module>" else function.name
        self.lines.extend([f"global {label}", f"{label}:", "    push rbp", "    mov rbp, rsp", f"    sub rsp, {frame}"])
        for slot, _ in self.owned_slots:
            self.lines.append(f"    mov qword {self._address(slot)}, 0")
        if not function.frame_abi and len(function.params) > 4:
            raise NativeBuildError("native calls with more than four parameters are not supported yet")
        if function.frame_abi:
            for index, name in enumerate(function.params):
                self.lines.extend([
                    f"    mov rax, [rcx+{index * 8}]",
                    f"    mov {self._address(name)}, rax",
                ])
        elif function.params:
            for register, name in zip(("rcx", "rdx", "r8", "r9"), function.params):
                self._store_slot(name, register)
        if function.self_class and function.params:
            self.types[function.params[0]] = f"object:{function.self_class}"
        # WITH_PROTOCOL_V1: __exit__(self, tipo, mensaje, tb) receives the
        # exception type-name and message as strings (V1: type NAME, not the
        # exception object; traceback is passed as None).
        if function.name.endswith("__exit__") and len(function.params) >= 3:
            self.types[function.params[1]] = "str"
            self.types[function.params[2]] = "str"
        labels = {block.label: f"{label}_{block.label}" for block in function.blocks}
        labels["__exit"] = f"{label}__exit"
        for block in function.blocks:
            self.lines.append(f"{labels[block.label]}:")
            for _ in range(self._loop_exit_pops.get(block.label, 0)):
                # consume this loop's StopIteration signal (a nested loop's
                # flag would otherwise trip the outer loop's jne check) and
                # then restore the handler stack.
                self.lines.append("    call piton_catch_clear")
                self.lines.append("    call piton_try_pop")
            for instruction in block.instructions:
                self._emit_instruction(instruction, labels)
            if not block.instructions or block.instructions[-1].op not in {"jump", "branch", "return"}:
                self.lines.append(f"    jmp {labels['__exit']}")
        self.lines.append(f"{labels['__exit']}:")
        self._emit_cleanup()
        if function.name == "<module>":
            self.lines.extend([
                "    call piton_gc_collect",
                "    call piton_collection_live_count", f"    mov {self._address('@scratch0')}, rax",
                "    call piton_object_live_count", f"    or rax, {self._address('@scratch0')}",
                f"    mov {self._address('@scratch1')}, rax",
                "    call piton_dict_live_count", f"    or rax, {self._address('@scratch1')}",
                f"    mov {self._address('@scratch0')}, rax",
                "    call piton_set_live_count", f"    or rax, {self._address('@scratch0')}",
                "    test rax, rax", "    setne al", "    movzx eax, al",
            ])
        else:
            self.lines.append("    xor eax, eax")
        self.lines.extend(["    leave", "    ret"])

    def _emit_generator_function(self, function: MIRFunction) -> None:
        """Emit a suspendible generator body as a state machine.

        Calling convention (Win64): ``RCX = PitonGenerator*``, ``RAX = yielded value``.
        All mutable slots are mirrored into ``gen->locals`` so they survive ``ret``.
        ``gen->state`` (offset 8) selects the resume point; ``gen->finished``
        (offset 16) is set once the body completes. Locals start at offset 32.
        """
        layout = self.generator_layouts.get(function.name)
        if layout is None:
            raise NativeBuildError(f"native generator '{function.name}' has no persisted-slot layout")
        self.slots = {}
        self.next_slot = 8
        self.aliases = {}
        self.types = {}
        self.owned_slots = []
        self._pushed_iters = set()
        self._popped_iters = set()
        self._loop_exit_pops = {}
        self.tuple_etypes = {k: v for k, v in self.tuple_etypes.items() if not k.startswith('%')}
        self.dict_elems = {k: v for k, v in self.dict_elems.items() if not k.startswith('%')}
        # NOTE: the GLOBAL_DECL_V1 tables are set once by _scan_module_globals
        # in emit() and must NOT be reset per function (that wiped them before
        # any instruction was emitted).
        self.bigint_slots = []
        self.constants = {}
        self._boolh_types: dict[str, str] = {}
        if function.vararg:
            self.types[function.vararg] = "tuple"
        if function.kwarg:
            self.types[function.kwarg] = "dict"
        for block in function.blocks:
            for instruction in block.instructions:
                self._reserve(instruction.result)
                if instruction.op == "build_collection" and instruction.result:
                    coll_kind = instruction.args[0] if instruction.args else ""
                    free_fn = {"dict": "piton_dict_free", "set": "piton_set_free"}.get(coll_kind, "piton_collection_free")
                    self.owned_slots.append((instruction.result, free_fn))
                if instruction.op == "genexpr_new" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_genexpr_free"))
                if instruction.op == "gen_init" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_gen_free"))
                if instruction.op == "object_new" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_object_free"))
                if instruction.op == "store":
                    self._reserve(instruction.args[0])
        self._reserve("@gen_ptr")
        for scratch in ("@scratch0", "@scratch1", "@scratch2", "@scratch3"):
            self._reserve(scratch)
        for default_slot in ("%d0", "%d1", "%d2", "%d3"):
            self._reserve(default_slot)
        # Reserve parameter slots before computing the frame (see comment in
        # _emit_function: params must not land inside the Win64 shadow area).
        for name in function.params:
            self._reserve(name)
        frame = max(48, ((self.next_slot + 32 + 15) // 16) * 16)
        label = function.name
        self.lines.extend([f"global {label}", f"{label}:", "    push rbp", "    mov rbp, rsp", f"    sub rsp, {frame}"])
        self.lines.append(f"    mov {self._address('@gen_ptr')}, rcx")
        # Restore persisted slots from the heap generator object.
        ordered = sorted(layout.items(), key=lambda item: item[1])
        for slot, index in ordered:
            self.lines.extend([
                f"    mov rcx, {self._address('@gen_ptr')}",
                f"    mov rax, [rcx+{PITON_GEN_LOCAL_BASE + index * 8}]",
                f"    mov {self._address(slot)}, rax",
            ])
        # Dispatch on the saved resume state (0 = first entry).
        yield_count = sum(
            1 for block in function.blocks for instruction in block.instructions if instruction.op in {"gen_yield", "agen_emit"}
        )
        self._gen_resume_labels = [f"{label}_genresume_{i}" for i in range(1, yield_count + 1)]
        self._gen_yield_counter = 0
        self._pushed_iters = set()
        self._popped_iters = set()
        self._loop_exit_pops = {}
        self.tuple_etypes = {k: v for k, v in self.tuple_etypes.items() if not k.startswith('%')}
        self.dict_elems = {k: v for k, v in self.dict_elems.items() if not k.startswith('%')}
        # NOTE: the GLOBAL_DECL_V1 tables are set once by _scan_module_globals
        # in emit() and must NOT be reset per function (that wiped them before
        # any instruction was emitted).
        if yield_count:
            self.lines.extend([
                f"    mov rcx, {self._address('@gen_ptr')}",
                "    mov rax, [rcx+8]",
                "    test rax, rax",
                f"    jz {label}_{function.blocks[0].label}",
            ])
            for resume_id, resume_label in enumerate(self._gen_resume_labels, start=1):
                self.lines.extend([
                    f"    cmp rax, {resume_id}",
                    f"    je {resume_label}",
                ])
            self.lines.append(f"    jmp {label}__exit")
        labels = {block.label: f"{label}_{block.label}" for block in function.blocks}
        labels["__exit"] = f"{label}__exit"
        for block in function.blocks:
            self.lines.append(f"{labels[block.label]}:")
            for _ in range(self._loop_exit_pops.get(block.label, 0)):
                # consume this loop's StopIteration signal (a nested loop's
                # flag would otherwise trip the outer loop's jne check) and
                # then restore the handler stack.
                self.lines.append("    call piton_catch_clear")
                self.lines.append("    call piton_try_pop")
            for instruction in block.instructions:
                self._emit_instruction(instruction, labels)
            if not block.instructions or block.instructions[-1].op not in {"jump", "branch", "return"}:
                self.lines.append(f"    jmp {labels['__exit']}")
        self.lines.append(f"{labels['__exit']}:")
        self._emit_cleanup()
        self.lines.extend([
            f"    mov rcx, {self._address('@gen_ptr')}",
            "    mov qword [rcx+16], 1",
            "    xor eax, eax",
            "    leave", "    ret",
        ])

    def _emit_gen_save(self, layout: dict[str, int]) -> None:
        ordered = sorted(layout.items(), key=lambda item: item[1])
        self.lines.append(f"    mov rcx, {self._address('@gen_ptr')}")
        for slot, index in ordered:
            self.lines.extend([
                f"    mov rax, {self._address(slot)}",
                f"    mov [rcx+{PITON_GEN_LOCAL_BASE + index * 8}], rax",
            ])

    def _emit_gen_suspend(self, value: Any, result: Optional[str], await_flag: bool) -> None:
        """Emit one suspension point for a generator / coroutine / async-generator body.

        Saves all persisted slots, stores the resume id in ``state``, writes the
        async-generator await marker into reserved slot 63 (1 = the yielded value
        is a coroutine to run — used by ``esperar``; 0 = plain data — used by
        ``producir``), returns the yielded value, then emits the resume label that
        reloads ``sent_value`` into the yield's result slot.
        """
        layout = self.generator_layouts.get(self.function.name, {})
        self._load_operand(value, "rax")
        # _emit_gen_save clobbers rax: stash the yielded value aside first.
        self.lines.append(f"    mov {self._address('@scratch0')}, rax")
        self._emit_gen_save(layout)
        self.lines.append(f"    mov rax, {self._address('@scratch0')}")
        resume_id = self._gen_yield_counter + 1
        self._gen_yield_counter += 1
        self.lines.extend([
            f"    mov rcx, {self._address('@gen_ptr')}",
            f"    mov qword [rcx+8], {resume_id}",
            f"    mov qword [rcx+{PITON_GEN_AWAIT_FLAG_OFFSET}], {1 if await_flag else 0}",
            "    leave", "    ret",
        ])
        resume_label = (
            self._gen_resume_labels[resume_id - 1]
            if resume_id - 1 < len(self._gen_resume_labels)
            else f"{self.function.name}_genresume_{resume_id}"
        )
        self.lines.append(f"{resume_label}:")
        # Load sent_value from generator struct (offset 32) into yield result slot
        self.lines.extend([
            f"    mov rcx, {self._address('@gen_ptr')}",
            "    mov rax, [rcx+32]",          # sent_value
            "    mov qword [rcx+32], 0",       # clear sent_value
        ])
        if result:
            result_type = self.types.get(value, "int") if isinstance(value, str) else "int"
            if result_type == "gather":
                # TASK_SCHEDULER_V1: awaiting a gather yields a real list, so the
                # result is statically a list (print/index dispatch). The coroutine
                # body owns it: register so _emit_cleanup frees it at gen exit,
                # keeping the teardown live-count tripwire quiet.
                awaited_type = "list"
                self.owned_slots.append((result, "piton_collection_free"))
                result_type = "gather"
            elif result_type == "task":
                # TASK_SCHEDULER_V1: awaiting a task yields the task's result
                # value (an int in the native subset) — never the task itself.
                awaited_type = "int"
            elif str(result_type).startswith("iterator:") or result_type in {"generator", "genexpr"}:
                # PARITY_P0_V1 regression guard (mirrors the Linux backend):
                # the awaited operand is a coroutine/generator OBJECT;
                # `esperar` yields its return value, whose static type is not
                # tracked here. Fall back to the historical default instead of
                # propagating the iterator marker into the print lowering,
                # which now fails closed on such markers.
                awaited_type = "int"
            else:
                awaited_type = result_type
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = awaited_type

    def _reserve(self, name: str | None) -> None:
        if name is not None and name not in self.slots:
            self.slots[name] = self.next_slot
            self.next_slot += 8

    def _address(self, name: str) -> str:
        self._reserve(name)
        return f"[rbp-{self.slots[name]}]"

    def _resolve_property_class(self, owner_type: str, name: str) -> str | None:
        """Returns the MRO class that declares property ``name`` for an owner
        statically typed ``object:<Class>``; None if the owner type is not a
        native class or the attribute is not a property."""
        if not owner_type.startswith("object:"):
            return None
        class_name = owner_type.split(":", 1)[1]
        for candidate in self.mir_module_class_mro.get(class_name, []):
            if name in self.mir_module_class_properties.get(candidate, {}):
                return candidate
        return None

    def _store_slot(self, name: str, register: str) -> None:
        self.lines.append(f"    mov {self._address(name)}, {register}")

    def _load_operand(self, operand: Any, register: str = "rax") -> None:
        if isinstance(operand, str) and operand.startswith("%"):
            self.lines.append(f"    mov {register}, {self._address(operand)}")
        elif isinstance(operand, bool):
            self.lines.append(f"    mov {register}, {int(operand)}")
        elif isinstance(operand, int):
            self.lines.append(f"    mov {register}, {operand}")
        elif isinstance(operand, float):
            if math.isfinite(operand):
                # repr() is shortest round-trip, and NASM's __float64__ parses it
                # back to the identical double (verified 19/19, subnormals included).
                self.lines.append(f"    mov {register}, __float64__({operand!r})")
            else:
                # repr() yields 'inf'/'-inf'/'nan', which NASM rejects outright
                # ("expecting floating-point number"), so emit the IEEE-754 bits.
                bits, = struct.unpack("<Q", struct.pack("<d", operand))
                self.lines.append(f"    mov {register}, 0x{bits:016X}")
        elif operand is None:
            self.lines.append(f"    xor {register}, {register}")
        else:
            label = self._string(str(operand))
            self.lines.append(f"    lea {register}, [{label}]")

    def _emit_instruction(self, instruction: MIRInstruction, labels: dict[str, str]) -> None:
        op, args, result = instruction.op, instruction.args, instruction.result
        if op == "iter_new":
            source = args[0]
            source_type = self.types.get(source, "")
            if source_type in {"genexpr", "iterator:genexpr"}:
                self._load_operand(source, "rcx")
                self.lines.append("    call piton_genexpr_iter")
                self.types[result] = "iterator:genexpr"
            elif source_type == "generator":
                self._load_operand(source, "rcx")
                self.lines.append(f"    mov {self._address(result)}, rcx")
                self.types[result] = "generator"
            elif source_type.startswith("iterator:"):
                # ITER_PASSTHROUGH_V1: iter(x) on an iterator returns x itself
                # (mirrors the Linux backend).
                self._load_operand(source, "rcx")
                self.lines.append(f"    mov {self._address(result)}, rcx")
                self.types[result] = source_type
            elif source_type.startswith("object:"):
                class_name = source_type.split(":", 1)[1]
                resolved_class = next(
                    (candidate for candidate in self.mir_module_class_mro.get(class_name, [])
                     if "__iter__" in self.mir_module_classes.get(candidate, set())),
                    None,
                )
                if resolved_class is None:
                    raise NativeBuildError(f"native iter requires '{class_name}.__iter__'")
                self._load_operand(source, "rcx")
                self.lines.append(f"    call {resolved_class}____iter__")
                self.types[result] = f"iterator:object:{class_name}"
            elif source_type == "str":
                self._load_operand(source, "rcx")
                self.lines.append("    call piton_str_iterator_new")
                self.types[result] = "iterator:str"
            else:
                self._load_operand(source, "rcx")
                self.lines.append("    call piton_iterator_new_any")
                self.types[result] = f"iterator:{source_type or 'unknown'}"
                # DICT_KEY_TYPE_V1: carry the key type onto the iterator.
                if result and source_type == 'dict' and source in self._dict_key_types:
                    self._dict_key_types[result] = self._dict_key_types[source]
            self.lines.append(f"    mov {self._address(result)}, rax")
            if result and self.types.get(result, "").startswith("iterator:") and self.types[result] != "iterator:genexpr":
                # ITER_LOOP_GUARD_V1: for-loop exhaustion raises StopIteration
                # through piton_raise, which prints and exits when no handler
                # accepts it. Push a StopIteration-only frame for the loop's
                # lifetime so the static jne-handler in iter_next routes the
                # exit; the matching pop is emitted at the loop-exit block.
                # (genexpr iterators use the flag-only setter and generator
                # bodies run under the coro frame, so they are excluded.)
                self.lines.append("    call piton_try_push")
                stop_label = self._string("StopIteration")
                self.lines.append(f"    lea rcx, [{stop_label}]")
                self.lines.append("    call piton_try_set_accepted")
                self._pushed_iters.add(result)
        elif op == "builtin_iter_new":
            builtin, source, start = args
            if builtin == "calliter":
                # ITER_PROTOCOL_V2: iter(callable, sentinel); element type of
                # the callable is the raw 0-arg call result (int subset).
                callable_src, sentinel_src = source
                self._load_operand(callable_src, "rcx")
                self._load_operand(sentinel_src, "rdx")
                self.lines.append("    call piton_calliter_new")
            elif builtin != "enumerate":
                if builtin == "reversed":
                    if self.types.get(source) not in {"list", "tuple"}:
                        raise NativeBuildError("native reversed currently requires a list or tuple")
                    self._load_operand(source, "rcx")
                    self.lines.append("    call piton_reversed_new")
                elif builtin == "zip":
                    left, right = source
                    if self.types.get(left) not in {"list", "tuple"} or self.types.get(right) not in {"list", "tuple"}:
                        raise NativeBuildError("native zip currently requires two lists or tuples")
                    self._load_operand(left, "rcx"); self._load_operand(right, "rdx")
                    self.lines.append("    call piton_zip_new")
                elif builtin in {"map", "filter"}:
                    if self.types.get(source) not in {"list", "tuple"}:
                        raise NativeBuildError("native map/filter require a list or tuple")
                    self._load_operand(source, "rcx")
                    if self.types.get(start) == "closure":
                        self._load_operand(start, "rdx")
                    else:
                        callback_name = self.aliases.get(start)
                        if callback_name not in self.function_names:
                            raise NativeBuildError("native map/filter require a known unary callback")
                        self.lines.append(f"    lea rdx, [{callback_name}]")
                    self.lines.append(f"    mov r8, {1 if builtin == 'filter' else 0}")
                    self.lines.append("    call piton_callback_iterator_new")
                else:
                    raise NativeBuildError(f"native builtin iterator not supported: {builtin}")
            else:
                if self.types.get(source) not in {"list", "tuple"}:
                    raise NativeBuildError("native enumerate currently requires a list or tuple")
                self._load_operand(source, "rcx")
                self._load_operand(start if start is not None else 0, "rdx")
                self.lines.append("    call piton_enumerate_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = f"iterator:{builtin}"
        elif op == "iter_next":
            iterator, handler_label = args[0], args[1]
            iterator_type = self.types.get(iterator, "")
            if iterator in self._pushed_iters and iterator not in self._popped_iters and handler_label:
                # first loop consuming this iterator: its exit block pops the
                # frame pushed in iter_new (covers normal exhaustion AND
                # break, which both converge on the loop-exit label).
                self._loop_exit_pops[handler_label] = self._loop_exit_pops.get(handler_label, 0) + 1
                self._popped_iters.add(iterator)
            if iterator_type in {"genexpr", "iterator:genexpr"}:
                self._load_operand(iterator, "rcx")
                self.lines.append("    call piton_genexpr_next")
            elif iterator_type == "generator":
                self._load_operand(iterator, "rcx")
                self.lines.append("    call piton_gen_next")
            elif iterator_type.startswith("iterator:object:") or iterator_type.startswith("object:"):
                class_name = iterator_type.split(":", 2)[2] if iterator_type.startswith("iterator:") else iterator_type.split(":", 1)[1]
                resolved_class = next(
                    (candidate for candidate in self.mir_module_class_mro.get(class_name, [])
                     if "__next__" in self.mir_module_classes.get(candidate, set())),
                    None,
                )
                if resolved_class is None:
                    raise NativeBuildError(f"native next requires '{class_name}.__next__'")
                self._load_operand(iterator, "rcx")
                self.lines.append(f"    call {resolved_class}____next__")
            else:
                self._load_operand(iterator, "rcx")
                if iterator_type == "iterator:str":
                    self.lines.append("    call piton_str_iterator_next")
                elif iterator_type == "iterator:enumerate":
                    self.lines.append("    call piton_enumerate_next")
                elif iterator_type == "iterator:reversed":
                    self.lines.append("    call piton_reversed_next")
                elif iterator_type == "iterator:zip":
                    self.lines.append("    call piton_zip_next")
                elif iterator_type in {"iterator:map", "iterator:filter"}:
                    self.lines.append("    call piton_callback_iterator_next")
                elif iterator_type == "iterator:calliter":
                    self.lines.append("    call piton_calliter_next")
                else:
                    self.lines.append("    call piton_iterator_next_any")
            self.lines.append(f"    mov {self._address(result)}, rax")
            # DICT_KEY_TYPE_V1: a dict iterator yields its KEYS; the static type
            # comes from the dict, and an unknown key type fails closed
            # instead of printing a pointer as a string.
            if iterator_type == "iterator:dict":
                key_type = self._dict_key_types.get(args[0])
                if key_type is None:
                    raise NativeBuildError(
                        "native dict iteration requires a statically known key type "
                        "(CPython iterates keys of any type)"
                    )
                self.types[result] = key_type
            else:
                self.types[result] = "tuple" if iterator_type in {"iterator:enumerate", "iterator:zip"} else "str" if iterator_type == "iterator:str" else "int"
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            if handler_label:
                self.lines.append(f"    jne {labels[handler_label]}")
        elif op == "const":
            value = args[0]
            self.constants[result] = value
            if value is None:
                self.types[result] = "none"
            elif isinstance(value, bool):
                self.types[result] = "bool"
            elif isinstance(value, str):
                self.types[result] = "str"
            elif isinstance(value, float):
                self.types[result] = "float"
            elif isinstance(value, int) and not (-(1 << 60) <= value < (1 << 60)):
                self.types[result] = "bigint"
            else:
                self.types[result] = "int"
            if self.types[result] == "bigint":
                self.lines.extend([
                    f"    lea rcx, [{self._string(str(value))}]",
                    "    call piton_bigint_from_str",
                    f"    mov {self._address(result)}, rax",
                ])
                self.bigint_slots.append(result)
            else:
                self._load_operand(value)
                self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "load":
            name = args[0]
            self.aliases[result] = name
            self.types[result] = self.types.get(name, "int")
            # FMT_SPEC_V1: a load of a tracked string constant keeps the
            # literal, so `'{:>4}'.format(x)` stays provably a template.
            if self.types.get(name) == "str" and name in self.constants:
                self.constants[result] = self.constants[name]
            if name in self.tuple_etypes:
                self.tuple_etypes[result] = self.tuple_etypes[name]
            if name in self.dict_elems:
                self.dict_elems[result] = self.dict_elems[name]
            if name in self.function_names:
                self.lines.append(f"    lea rax, [{name}]")
                self.lines.append(f"    mov {self._address(result)}, rax")
                return
            if name in _BUILTINS and name not in self.slots:
                # BUILTIN_MARKER_V1: no code is emitted — the builtin is only
                # resolved by the call dispatch. Mark the operand so any other
                # consumer fails closed instead of reading an uninitialized
                # slot (previously: garbage / SIGSEGV).
                self.types[result] = "builtin"
                return
            fname = getattr(self.function, "name", "<module>")
            # GLOBAL_DECL_V1: the declaration wins over local stores.
            if name in self._shared_globals and (
                fname == "<module>" or name in self._func_globals.get(fname, ())
            ):
                if fname != "<module>" and self._is_gen_function():
                    raise NativeBuildError(f"native global '{name}' inside generators is not supported yet")
                self.lines.append(f"    mov rax, [{self._global_label(name)}]")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = self._module_types.get(name, self.types.get(name, "int"))
                if name in self.tuple_etypes:
                    self.tuple_etypes[result] = self.tuple_etypes[name]
                if name in self.dict_elems:
                    self.dict_elems[result] = self.dict_elems[name]
                return
            if (
                name in self._module_stored
                and fname != "<module>"
                and name not in getattr(self.function, "params", ())
                and name not in self._func_stores.get(fname, ())
            ):
                raise NativeBuildError(
                    f"native '{name}' is assigned at module level; declare it global to read it inside '{self.function.name}'"
                )
            if name in self._shared_globals and getattr(self.function, "name", "<module>") == "<module>":
                # module-level access to the shared cell (the module store
                # went to the data label, so the load must too — otherwise
                # it would read an unreserved stack slot).
                self.lines.append(f"    mov rax, [{self._global_label(name)}]")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = self._module_types.get(name, self.types.get(name, "int"))
                return
            self.lines.append(f"    mov rax, {self._address(name)}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            if name in self.strkey_dict_temps:
                self.strkey_dict_temps.add(result)
            if name in self._dict_key_types:
                self._dict_key_types[result] = self._dict_key_types[name]

        elif op == "store":
            if isinstance(args[1], str) and self.types.get(args[1]) == "builtin":
                # BUILTIN_MARKER_V1: storing a builtin marker would copy an
                # uninitialized slot — fail closed.
                raise NativeBuildError(
                    f"native store of builtin '{self.aliases.get(args[1], args[1])}' as a value is not supported"
                )
            if (
                args[0] in self._shared_globals
                and (getattr(self.function, "name", "<module>") == "<module>"
                     or args[0] in self._func_globals.get(getattr(self.function, "name", ""), ()))
            ):
                if getattr(self.function, "name", "<module>") != "<module>" and self._is_gen_function():
                    raise NativeBuildError(f"native global '{args[0]}' inside generators is not supported yet")
                self.lines.append(
                    f"    mov rax, {self._address(args[1]) if isinstance(args[1], str) and args[1].startswith('%') else self._immediate(args[1])}"
                )
                self.lines.append(f"    mov [{self._global_label(args[0])}], rax")
                if isinstance(args[1], str):
                    self.types[args[0]] = self.types.get(args[1], "int")
                    if args[1] in self.tuple_etypes:
                        self.tuple_etypes[args[0]] = self.tuple_etypes[args[1]]
                    if args[1] in self.dict_elems:
                        self.dict_elems[args[0]] = self.dict_elems[args[1]]
                return
            self.lines.append(f"    mov rax, {self._address(args[1]) if isinstance(args[1], str) and args[1].startswith('%') else self._immediate(args[1])}")
            self.lines.append(f"    mov {self._address(args[0])}, rax")
            if isinstance(args[0], str) and args[0].startswith("@boolh_"):
                # BOOL_SHORT_V1: both arms of y/o must share one static
                # type — the untagged model cannot print a mixed result
                # (CPython returns the winning operand).
                seen = self._boolh_types.get(args[0])
                if isinstance(args[1], str):
                    current = self.types.get(args[1], "int")
                elif isinstance(args[1], bool):
                    current = "bool"
                elif args[1] is None:
                    current = "none"
                else:
                    current = "int"
                if seen is None:
                    self._boolh_types[args[0]] = current
                elif current != seen:
                    raise NativeBuildError(
                        f"native y/o with mixed operand types ({seen} vs {current}) "
                        "is not supported (the winner type is not statically knowable)"
                    )
            if isinstance(args[1], str):
                self.types[args[0]] = self.types.get(args[1], "int")
                if args[1] in self.tuple_etypes:
                    self.tuple_etypes[args[0]] = self.tuple_etypes[args[1]]
                if args[1] in self.dict_elems:
                    self.dict_elems[args[0]] = self.dict_elems[args[1]]
                if args[1] in self.strkey_dict_temps:
                    self.strkey_dict_temps.add(args[0])
                if args[1] in self._dict_key_types:
                    self._dict_key_types[args[0]] = self._dict_key_types[args[1]]
        elif op == "binary":
            operator, left, right = args[0], args[1], args[2]
            # ZDIV_GUARD_V1: mir attaches the innermost try handler as an
            # optional 4th argument on '//' and '%' so the raise below routes
            # to intentar/excepto instead of trapping the process.
            handler_label = args[3] if len(args) > 3 else None
            left_type = self.types.get(left, "int")
            right_type = self.types.get(right, "int")
            if operator == "in":
                # CONTAINS_V1: membership. Must run before the str/collection
                # branches: they treat 'in' as an unsupported operator.
                self._emit_contains(result, left, right, negate=False)
                return
            if "str" in {left_type, right_type}:
                if operator == "+" and left_type == right_type == "str":
                    self._emit_string_concat(left, right, result)
                    return
                if operator == "*":
                    # STR_REPEAT_V1: 'ab' * 3 (either order); '' for
                    # non-positive counts, like CPython.
                    if left_type == "str" and right_type in {"int", "bool"}:
                        str_side, times_side = left, right
                    elif right_type == "str" and left_type in {"int", "bool"}:
                        str_side, times_side = right, left
                    else:
                        str_side, times_side = None, None
                    if str_side is not None:
                        self._load_operand(str_side, "rcx")
                        self._load_operand(times_side, "rdx")
                        self.lines.append("    call piton_str_repeat")
                        self.lines.append(f"    mov {self._address(result)}, rax")
                        self.types[result] = "str"
                        return
                if operator == "%":
                    # PCT_FORMAT_V1 (+P14 width/precision/mapping/hex-octal):
                    # "%..." % args lowers through the {} engine after static
                    # translation (mirrors the Linux backend). The template
                    # must be a literal; tuple arguments must have statically
                    # known elements (extracted with collection_get, which
                    # already decodes INT/BOOL payloads); mapping arguments
                    # must be inline dict literals with literal str keys,
                    # resolved at build time. Element types resolve at USE
                    # time (a variable can be reassigned after the tuple/dict
                    # is built); anything flowing from a parameter fails
                    # closed, like the single-value path.
                    template = self.constants.get(left)
                    if not isinstance(template, str):
                        raise NativeBuildError("native str % formatting requires a literal template")
                    translated, fields = self._parse_percent_template(template)
                    has_map = any(field[0] is not None for field in fields)
                    has_pos = any(field[0] is None for field in fields)
                    if has_map and has_pos:
                        raise NativeBuildError("native str % formatting cannot mix positional and mapping conversions")
                    params = getattr(self.function, "params", ())

                    def _resolve_etype(item, recorded):
                        if isinstance(item, str) and item.startswith("%"):
                            source = self.aliases.get(item, item)
                            if source in params or item in params:
                                raise NativeBuildError(
                                    "native str % formatting a bare parameter is not supported (type unknown)"
                                )
                            return self.types.get(item, recorded)
                        return recorded

                    arg_values: list[Any] = []
                    arg_etypes: list[Any] = []
                    if has_map:
                        if self.types.get(right) != "dict":
                            raise NativeBuildError("native str % mapping requires a dict argument")
                        record = self.dict_elems.get(right)
                        if record is None:
                            record = self.dict_elems.get(self.aliases.get(right, right))
                        if record is None:
                            raise NativeBuildError("native str % mapping requires a dict of statically known keys")
                        for name, _c, _f, _w, _pr in fields:
                            hit = record.get(name)
                            if hit is None:
                                raise NativeBuildError(f"native str % mapping: key {name!r} is not statically known")
                            arg_values.append(hit[0])
                            arg_etypes.append(_resolve_etype(hit[0], hit[1]))
                    elif self.types.get(right) == "tuple":
                        known = self.tuple_etypes.get(right)
                        if known is None:
                            known = self.tuple_etypes.get(self.aliases.get(right, right))
                        if known is None:
                            raise NativeBuildError(
                                "native str % formatting requires a tuple of statically known length"
                            )
                        for index, (item, etype0) in enumerate(known):
                            etype = _resolve_etype(item, etype0)
                            if etype not in {"int", "bool", "str", "none"}:
                                raise NativeBuildError(
                                    "native str % formatting requires a tuple of statically known element types"
                                )
                            tmp = f"{right}_e{index}"
                            self._load_operand(right, "rcx")
                            self.lines.append(f"    mov rdx, {index}")
                            self.lines.append("    call piton_collection_get")
                            self.lines.append(f"    mov {self._address(tmp)}, rax")
                            self.types[tmp] = etype
                            arg_values.append(tmp)
                            arg_etypes.append(None)
                    else:
                        if self.aliases.get(right, right) in params:
                            raise NativeBuildError(
                                "native str % formatting a bare parameter is not supported (type unknown)"
                            )
                        arg_values = [right]
                        arg_etypes = [None]
                    self._emit_percent(result, translated, fields, arg_values, arg_etypes, handler_label, labels)
                    return
                raise NativeBuildError(f"native string operator not supported yet: {operator}")
            if {left_type, right_type} & {"list", "tuple", "dict", "set"}:
                # SEQ_CONCAT_V1: list+list / tuple+tuple concatenate; any other
                # collection arithmetic is a CPython TypeError — fail closed at
                # build time instead of doing raw pointer arithmetic.
                if operator == "+" and left_type == right_type and left_type in {"list", "tuple"}:
                    self._load_operand(left, "rcx")
                    self._load_operand(right, "rdx")
                    self.lines.append("    call piton_seq_concat")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = left_type
                    return
                if operator == "*" and {left_type, right_type} == {"list", "int"}:
                    # COLL_REPEAT_V1: list * n (either order).
                    seq_side, times = (left, right) if left_type == "list" else (right, left)
                    self._load_operand(seq_side, "rcx")
                    self._load_operand(times, "rdx")
                    self.lines.append("    call piton_seq_repeat_n")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "list"
                    return
                if operator == "*" and {left_type, right_type} == {"tuple", "int"}:
                    seq_side, times = (left, right) if left_type == "tuple" else (right, left)
                    self._load_operand(seq_side, "rcx")
                    self._load_operand(times, "rdx")
                    self.lines.append("    call piton_seq_repeat_n")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "tuple"
                    return
                raise NativeBuildError(
                    f"native collection binary operator not supported: {operator} on {left_type}/{right_type}"
                )
            if "none" in {left_type, right_type}:
                # WRETURNTYPE_V1: None-typed operands (e.g. a call result
                # of a bare function) used to fall through to raw pointer
                # arithmetic; CPython raises TypeError instead.
                raise NativeBuildError(
                    f"native {operator} with None is not supported (CPython raises TypeError)"
                )
            if "bigint" in {left_type, right_type}:
                self._emit_bigint_binary(operator, left, right, result)
                return
            if "float" in {left_type, right_type}:
                # TRUEDIV_V1 + FLOAT_MOD_V1 + FLOAT_POW_V1: / // % and **
                # join the supported set. CPython raises ZeroDivisionError on
                # float division by zero instead of IEEE infinities, so the
                # dividing ops go through raising helpers followed by the
                # catch_flag routing. ** is exact for y in
                # {-2,-1,-0.5,0,0.5,1,2}; constants fold exactly in Python.
                if operator not in {"+", "-", "*", "/", "//", "%", "**"}:
                    raise NativeBuildError(f"native float operator not supported yet: {operator}")
                if operator == "**":
                    left_const = self.constants.get(left) if isinstance(left, str) else None
                    right_const = self.constants.get(right) if isinstance(right, str) else None
                    if (
                        isinstance(left_const, (int, float)) and not isinstance(left_const, bool)
                        and isinstance(right_const, (int, float)) and not isinstance(right_const, bool)
                    ):
                        try:
                            folded = left_const ** right_const
                        except (OverflowError, ZeroDivisionError):
                            folded = None
                        if isinstance(folded, complex):
                            raise NativeBuildError("native float ** with negative base and fractional exponent yields complex (not supported)")
                        if isinstance(folded, float):
                            self.constants[result] = folded
                            self._load_operand(folded)
                            self.lines.append(f"    mov {self._address(result)}, rax")
                            self.types[result] = "float"
                            return
                    self._load_float_operand(left, "xmm0")
                    self._load_float_operand(right, "xmm1")
                    self.lines.append("    call piton_float_pow_v1")
                    self._emit_exc_routing(handler_label, labels)
                    self.lines.append("    movq rax, xmm0")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "float"
                    return
                self._load_float_operand(left, "xmm0")
                self._load_float_operand(right, "xmm1")
                if operator in {"/", "//", "%"}:
                    helper = {"/": "piton_float_div", "//": "piton_float_floor_div", "%": "piton_float_mod"}[operator]
                    self.lines.append(f"    call {helper}")
                    self._emit_exc_routing(handler_label, labels)
                    self.lines.append("    movq rax, xmm0")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                else:
                    instruction_name = {"+": "addsd", "-": "subsd", "*": "mulsd"}[operator]
                    self.lines.append(f"    {instruction_name} xmm0, xmm1")
                    self.lines.extend(["    movq rax, xmm0", f"    mov {self._address(result)}, rax"])
                self.types[result] = "float"
                return
            if operator == "/" and {left_type, right_type} <= {"int", "bool"}:
                # TRUEDIV_V1: int / int yields the double conversion, exactly
                # like CPython's long_true_divide for the i64 subset. Constant
                # operands fold exactly (Python true division IS the oracle).
                left_const = self.constants.get(left) if isinstance(left, str) else None
                right_const = self.constants.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                    and right_const != 0
                ):
                    folded = left_const / right_const
                    self.constants[result] = folded
                    self._load_operand(folded)
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "float"
                    return
                self._load_operand(left, "rcx")
                self._load_operand(right, "rdx")
                self.lines.append("    call piton_int_truediv")
                self._emit_exc_routing(handler_label, labels)
                self.lines.append("    movq rax, xmm0")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "float"
                return
            if operator == "**" and {left_type, right_type} <= {"int", "bool"}:
                # INT_POW_V1: CPython int ** int is exact arbitrary precision
                # for non-negative exponents (mirrors the Linux backend:
                # bounded constant folds, square-and-multiply at runtime, a
                # catchable ValueError for runtime negative exponents).
                left_const = self.constants.get(left) if isinstance(left, str) else None
                right_const = self.constants.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                ):
                    if right_const < 0:
                        if left_const == 0:
                            raise NativeBuildError("native int ** with zero base and negative exponent is not supported")
                        folded = left_const ** right_const
                        self.constants[result] = folded
                        self._load_operand(folded)
                        self.lines.append(f"    mov {self._address(result)}, rax")
                        self.types[result] = "float"
                        return
                    if right_const <= 1000000:
                        folded = left_const ** right_const
                        self.constants[result] = folded
                        if -(1 << 63) <= folded < (1 << 63):
                            self._load_operand(folded)
                            self.lines.append(f"    mov {self._address(result)}, rax")
                            self.types[result] = "int"
                        else:
                            self.lines.extend([
                                f"    lea rcx, [{self._string(str(folded))}]",
                                "    call piton_bigint_from_str",
                                f"    mov {self._address(result)}, rax",
                            ])
                            self.bigint_slots.append(result)
                            self.types[result] = "bigint"
                        return
                self._load_operand(left, "rcx")
                self._load_operand(right, "rdx")
                # INT_POW_V1: pre-zero the slot (same uninitialized-free
                # hazard as the Linux backend on the raise path).
                self.lines.append(f"    mov qword {self._address(result)}, 0")
                self.lines.append("    test rdx, rdx")
                neg_label = self._internal_label("pow_neg")
                done_label = self._internal_label("pow_done")
                self.lines.append(f"    js {neg_label}")
                self.lines.append("    call piton_bigint_pow_small")
                self.lines.append(f"    jmp {done_label}")
                self.lines.append(f"{neg_label}:")
                vtype = self._string("ValueError")
                vmsg = self._string("negative exponent requires a float result (out of the int subset)")
                self.lines.append(f"    lea rcx, [{vtype}]")
                self.lines.append(f"    lea rdx, [{vmsg}]")
                if handler_label:
                    self.lines.append("    call piton_raise")
                    self.lines.append("    call piton_catch_flag")
                    self.lines.append("    test rax, rax")
                    target = labels.get(handler_label, handler_label)
                    self.lines.append(f"    jne {target}")
                else:
                    self.lines.append("    call piton_raise_unhandled")
                self.lines.append(f"{done_label}:")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "bigint"
                self.bigint_slots.append(result)
                return
            if operator in {"+", "-", "*"} and {left_type, right_type} <= {"int", "bool"}:
                # INTOVF_GUARD_V1: constant int operands fold exactly (Python
                # bignum arithmetic IS the oracle); results beyond i64 promote
                # to a bigint literal. Runtime operands keep the historical
                # code path plus a jo-checked OverflowError below.
                left_const = self.constants.get(left) if isinstance(left, str) else None
                right_const = self.constants.get(right) if isinstance(right, str) else None
                if (
                    isinstance(left_const, int) and not isinstance(left_const, bool)
                    and isinstance(right_const, int) and not isinstance(right_const, bool)
                ):
                    folded = {"+": left_const + right_const, "-": left_const - right_const, "*": left_const * right_const}[operator]
                    # record the folded value so chained folds keep propagating
                    self.constants[result] = folded
                    if -(1 << 63) <= folded < (1 << 63):
                        self._load_operand(folded)
                        self.lines.append(f"    mov {self._address(result)}, rax")
                        self.types[result] = "int"
                    else:
                        self.lines.extend([
                            f"    lea rcx, [{self._string(str(folded))}]",
                            "    call piton_bigint_from_str",
                            f"    mov {self._address(result)}, rax",
                        ])
                        self.bigint_slots.append(result)
                        self.types[result] = "bigint"
                    return
            self._load_operand(left, "rax")
            self._load_operand(right, "rcx")
            if operator == "+":
                self.lines.append("    add rax, rcx")
                self._emit_int_overflow_guard(handler_label, labels)
            elif operator == "-":
                self.lines.append("    sub rax, rcx")
                self._emit_int_overflow_guard(handler_label, labels)
            elif operator == "*":
                self.lines.append("    imul rax, rcx")
                self._emit_int_overflow_guard(handler_label, labels)
            elif operator == "&":
                self.lines.append("    and rax, rcx")
            elif operator == "|":
                self.lines.append("    or rax, rcx")
            elif operator == "^":
                self.lines.append("    xor rax, rcx")
            elif operator == "<<":
                self.lines.append("    shl rax, cl")
            elif operator == ">>":
                self.lines.append("    sar rax, cl")
            elif operator == "/":
                raise NativeBuildError("native true division requires float support")
            elif operator in {"//", "%"}:
                # ZDIV_GUARD_V1: CPython raises ZeroDivisionError; a bare idiv
                # traps the process. Raise through the native exception
                # machinery and route to the enclosing try handler.
                zero_ok = self._internal_label("zdiv_ok")
                ztype = self._string("ZeroDivisionError")
                zmsg = self._string("integer division or modulo by zero")
                self.lines.append("    test rcx, rcx")
                self.lines.append(f"    jnz {zero_ok}")
                self.lines.append(f"    lea rcx, [{ztype}]")
                self.lines.append(f"    lea rdx, [{zmsg}]")
                if handler_label:
                    self.lines.append("    call piton_raise")
                    self.lines.append("    call piton_catch_flag")
                    self.lines.append("    test rax, rax")
                    target = labels.get(handler_label, handler_label)
                    self.lines.append(f"    jne {target}")
                else:
                    self.lines.append("    call piton_raise_unhandled")
                self.lines.append(f"{zero_ok}:")
                correction = self._internal_label("floor_done")
                self.lines.extend([
                    "    mov r8, rcx",
                    "    cqo",
                    "    idiv rcx",
                    "    test rdx, rdx",
                    f"    jz {correction}",
                    "    mov r9, rdx",
                    "    xor r9, r8",
                    f"    jns {correction}",
                    "    dec rax",
                    "    add rdx, r8",
                    f"{correction}:",
                ])
                if operator == "%":
                    self.lines.append("    mov rax, rdx")
            else:
                raise NativeBuildError(f"unsupported binary operator: {operator}")
            self.types[result] = "int"
            self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "unary":
            operator, operand = args
            if self.types.get(operand) == "bigint":
                if operator == "-":
                    self.lines.extend([
                        f"    mov rcx, {self._address(operand)}",
                        "    call piton_bigint_neg",
                        f"    mov {self._address(result)}, rax",
                    ])
                    self.types[result] = "bigint"
                    self.bigint_slots.append(result)
                    return
                if operator == "+":
                    self.lines.extend([
                        f"    mov rcx, {self._address(operand)}",
                        "    call piton_bigint_neg",
                        "    mov rcx, rax",
                        "    call piton_bigint_neg",
                        f"    mov {self._address(result)}, rax",
                    ])
                    self.types[result] = "bigint"
                    self.bigint_slots.append(result)
                    return
                raise NativeBuildError(f"native bigint unary operator not supported: {operator}")
            if self.types.get(operand) == "none" and operator in {"+", "-", "~"}:
                raise NativeBuildError(
                    f"native unary {operator} with None is not supported (CPython raises TypeError)"
                )
            self._load_operand(operand, "rax")
            if self.types.get(operand) == "float" and operator in {"+", "-"}:
                if operator == "-":
                    self.lines.append("    btc rax, 63")
                self.types[result] = "float"
                operand_const = self.constants.get(operand) if isinstance(operand, str) else None
                if isinstance(operand_const, float):
                    self.constants[result] = -operand_const if operator == "-" else operand_const
            elif operator == "-":
                self.lines.append("    neg rax")
                self.types[result] = "int"
                # INT_POW_V1 / FLOAT_POW_V1: record trivially-foldable unary
                # int/float results so downstream folds see through `-1` and
                # `-2.0`.
                operand_const = self.constants.get(operand) if isinstance(operand, str) else None
                if isinstance(operand_const, bool):
                    pass
                elif isinstance(operand_const, (int, float)):
                    self.constants[result] = -operand_const
            elif operator == "+":
                self.types[result] = self.types.get(operand, "int")
                operand_const = self.constants.get(operand) if isinstance(operand, str) else None
                if isinstance(operand_const, bool):
                    pass
                elif isinstance(operand_const, (int, float)):
                    self.constants[result] = operand_const
            elif operator == "~":
                self.lines.append("    not rax")
                self.types[result] = "int"
            elif operator in {"not", "no"}:
                self._emit_truth_test(operand)
                self.lines.extend(["    sete al", "    movzx rax, al"])
                self.types[result] = "bool"
            else:
                raise NativeBuildError(f"unsupported unary operator: {operator}")
            self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "compare":
            operator, left, right = args
            left_type = self.types.get(left, "int")
            right_type = self.types.get(right, "int")
            if operator in {"en", "no en"}:
                # CONTAINS_V1: `a en b` and `a no en b` share the membership
                # lowering. The POSITIVE form was missing here and emitted the
                # operator name into the asm (invalid); the parity gate only
                # covered `no en`, so the hole survived.
                self._emit_contains(result, left, right, negate=(operator == "no en"))
                return
            # SPECIAL_METHOD_LOOKUP_V1: `==` over native class instances
            # dispatches to the class-defined __eq__ (MRO resolved), same as
            # CPython. Fallback: object identity (cmp on *values*, documented).
            if operator == "==" and (
                left_type.startswith("object:") or right_type.startswith("object:")
            ):
                owner_side = left_type if left_type.startswith("object:") else right_type
                cls_name = owner_side.split(":", 1)[1]
                eq_cls = None
                for candidate in self.mir_module_class_mro.get(cls_name, []):
                    if "__eq__" in self.mir_module_classes.get(candidate, set()):
                        eq_cls = candidate
                        break
                if eq_cls is None and "__eq__" in self.mir_module_classes.get(cls_name, set()):
                    eq_cls = cls_name
                if eq_cls is not None:
                    target = f"{eq_cls}____eq__"
                    frame_vals = [left, right]
                    if self.function_frame_abi.get(target, False):
                        frame_size = ((len(frame_vals) * 8 + 32 + 15) // 16) * 16
                        self.lines.append(f"    sub rsp, {frame_size}")
                        for index, value in enumerate(frame_vals):
                            self._load_operand(value, "r10")
                            self.lines.append(f"    mov qword [rsp+32+{index * 8}], r10")
                        self.lines.extend([
                            f"    lea rcx, [{target}]",
                            f"    mov edx, {len(frame_vals)}",
                            "    lea r8, [rsp+32]",
                            "    call piton_frame_call",
                            f"    add rsp, {frame_size}",
                        ])
                    else:
                        for register, value in zip(("rcx", "rdx", "r8", "r9"), frame_vals):
                            self._load_operand(value, register)
                        self.lines.append(f"    call {target}")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "bool"
                    return
            if operator == "es":
                # Identidad / no-igualdad equivalente para el subset:
                # compara punteros (objetos) o valores (escalares).
                self._load_operand(left, "rax")
                self._load_operand(right, "rcx")
                self.lines.append("    cmp rax, rcx")
                self.lines.extend(["    sete al", "    movzx rax, al"])
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "bool"
                return
            numeric_types = {"int", "bool"}
            if "bigint" in {left_type, right_type}:
                self._emit_bigint_compare(operator, left, right, result)
                return
            float_compare = "float" in {left_type, right_type} and {left_type, right_type} <= {"float", "int", "bool"}
            if float_compare:
                self._load_float_operand(left, "xmm0")
                self._load_float_operand(right, "xmm1")
                self.lines.append("    ucomisd xmm0, xmm1")
                condition = {"==": "e", "!=": "ne", "<": "b", "<=": "be", ">": "a", ">=": "ae"}[operator]
                self.lines.extend([f"    set{condition} al", "    movzx rax, al"])
                mixed_non_numeric = False
            else:
                mixed_non_numeric = left_type != right_type and not {left_type, right_type} <= numeric_types
            if "float" in {left_type, right_type}:
                pass
            elif mixed_non_numeric:
                if operator not in {"==", "!="}:
                    raise NativeBuildError(
                        f"native ordering not supported between {left_type} and {right_type}"
                    )
                self.lines.append(f"    mov eax, {int(operator == '!=')}")
            elif left_type == right_type == "str":
                self._load_operand(left, "rcx")
                self._load_operand(right, "rdx")
                self.lines.extend(["    call strcmp", "    cmp eax, 0"])
            else:
                # ORDER_MIXED_TYPES_V1: CPython raises TypeError for `<`, `>`,
                # `<=`, `>=` between operands it cannot order. `None < None`
                # compared two zero slots and answered False. `==`/`!=` stay
                # legal across types, as in Python.
                if operator in {"<", ">", "<=", ">="} and not _ordering_pair_ok(left_type, right_type):
                    raise NativeBuildError(
                        f"native ordering comparison '{operator}' between '{left_type}' and "
                        f"'{right_type}' is not supported (CPython raises TypeError: "
                        f"'{operator}' not supported between instances of these types)"
                    )
                self._load_operand(left, "rax")
                self._load_operand(right, "rcx")
                self.lines.append("    cmp rax, rcx")
            if not mixed_non_numeric and not float_compare:
                condition = {"==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge"}[operator]
                self.lines.append(f"    set{condition} al")
                self.lines.append("    movzx rax, al")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "bool"
        elif op == "build_collection":
            kind, raw_items = args
            if kind == "dict":
                self.lines.extend([
                    f"    mov rcx, {self._address(result)}",
                    "    call piton_dict_free",
                    f"    mov rcx, {len(raw_items)}",
                    "    call piton_dict_new",
                    f"    mov {self._address(result)}, rax",
                ])
                for index, item in enumerate(raw_items):
                    key, value = item
                    self.lines.extend([
                        f"    mov rcx, {self._address(result)}",
                    ])
                    self._load_operand(key, "rdx")
                    self._load_operand(value, "r8")
                    self.lines.append("    call piton_dict_put")
                if all(self.types.get(key) == "str" for key, _ in raw_items):
                    self.strkey_dict_temps.add(result)
                if result and raw_items:
                    # DICT_KEY_TYPE_V1: only a single key kind is typed;
                    # a mixed-key dict stays untyped and fails closed.
                    kinds = {"str" if isinstance(key, str) and not key.startswith("%")
                             else self.types.get(key, "int") for key, _ in raw_items}
                    if len(kinds) == 1:
                        key_kind = next(iter(kinds))
                        # The Windows dict stores keys as piton_value (int
                        # auto-encoded) or a tracked string constant. Any other
                        # key kind (e.g. float) is NOT supported by the runtime,
                        # so it stays untyped and iteration fails closed
                        # instead of yielding a mis-encoded key.
                        if key_kind in {"int", "bool", "str"}:
                            self._dict_key_types[result] = key_kind
                if result:
                    # P14 mapping: resolve %(name)s statically for inline
                    # literals with literal str keys. dict_put only accepts
                    # int keys/values (anything else fails closed at build),
                    # and dict methods other than get fail closed too, so a
                    # recorded dict cannot be mutated in-subset: no
                    # invalidation is needed.
                    _drec = {}
                    for _k, _v in raw_items:
                        _ks = _k if (isinstance(_k, str) and not _k.startswith("%")) else self.constants.get(_k)
                        if not isinstance(_ks, str):
                            _drec = None
                            break
                        if isinstance(_v, str) and _v.startswith("%"):
                            _drec[_ks] = (_v, self.types.get(_v, "int"))
                        else:
                            try:
                                _drec[_ks] = (_v, self._percent_literal_type(_v))
                            except NativeBuildError:
                                _drec = None
                                break
                    if _drec is not None:
                        self.dict_elems[result] = _drec
            elif kind == "set":
                self.lines.extend([
                    f"    mov rcx, {self._address(result)}",
                    "    call piton_set_free",
                    f"    mov rcx, {len(raw_items)}",
                    "    call piton_set_new",
                    f"    mov {self._address(result)}, rax",
                ])
                for item in raw_items:
                    self.lines.extend([
                        f"    mov rcx, {self._address(result)}",
                    ])
                    self._load_operand(item, "rdx")
                    self.lines.append("    call piton_set_add")
            else:
                kind_id = {"list": 1, "tuple": 2}[kind]
                if kind == "tuple" and result:
                    # PCT_FORMAT_V1: record tuple elements (direct temps,
                    # valid same-function) and element types (valid
                    # cross-function: types outlive SSA temps).
                    _pairs = []
                    for item in raw_items:
                        if isinstance(item, str) and item.startswith("%"):
                            _pairs.append((item, self.types.get(item, "int")))
                        else:
                            _pairs.append((item, self._percent_literal_type(item)))
                    self.tuple_etypes[result] = tuple(_pairs)
                self.lines.extend([
                    f"    mov rcx, {self._address(result)}",
                    "    call piton_collection_free",
                    f"    mov rcx, {kind_id}",
                    f"    mov rdx, {len(raw_items)}",
                    "    call piton_collection_new",
                    f"    mov {self._address(result)}, rax",
                ])
                for index, item in enumerate(raw_items):
                    self.lines.extend([
                        f"    mov rcx, {self._address(result)}",
                        f"    mov rdx, {index}",
                    ])
                    item_type = self.types.get(item, "int")
                    if item_type in {"list", "tuple", "dict", "set"} or item_type.startswith("object:"):
                        self._load_operand(item, "r8")
                        self.lines.append("    call piton_collection_put_tagged")
                    else:
                        self._load_operand(item, "r8")
                        self._load_operand(item, "r9")
                        self.lines.append("    call piton_collection_put")
            self.types[result] = kind
        elif op == "unpack_check":
            # UNPACK_ARITY_V1: CPython verifies the element count on
            # unpacking (ValueError: too many / not enough values).
            # list/tuple read their length; str counts bytes (str get_item
            # is byte-based, so units agree); anything else fails closed
            # at build (dict/set unpacking needs key iteration).
            value, expected = args[0], args[1]
            handler_label = args[2] if len(args) > 2 else None
            vtype = self.types.get(value, "int")
            if vtype not in {"list", "tuple", "str"}:
                raise NativeBuildError(f"native unpack requires a list, tuple or str, not {vtype}")
            self._load_operand(value, "rcx")
            if vtype == "str":
                self.lines.append("    call piton_str_len")
            else:
                self.lines.append("    call piton_collection_len")
            # rax = actual length
            ok_label = self._internal_label("unpack_ok")
            many_label = self._internal_label("unpack_many")
            self.lines.append(f"    cmp rax, {expected}")
            self.lines.append(f"    je {ok_label}")
            self.lines.append(f"    jg {many_label}")
            # not enough: compose "not enough values to unpack
            # (expected N, got M)" through the {} engine.
            self.lines.append(f"    mov {self._address('@scratch0')}, rax")
            self.lines.append(f"    mov rcx, {expected}")
            self.lines.append("    call piton_str_from_int")
            self.lines.append(f"    mov {self._address('@scratch1')}, rax")
            self.lines.append(f"    mov rcx, {self._address('@scratch0')}")
            self.lines.append("    call piton_str_from_int")
            self.lines.append(f"    mov rdx, {self._address('@scratch1')}")
            self.lines.append(f"    mov {self._address('@scratch0')}, rdx")
            self.lines.append(f"    mov {self._address('@scratch1')}, rax")
            self.lines.append(f"    sub rsp, 48")
            self.lines.append(f"    mov rcx, {self._address('@scratch0')}")
            self.lines.append(f"    mov qword [rsp+32], rcx")
            self.lines.append(f"    mov rcx, {self._address('@scratch1')}")
            self.lines.append(f"    mov qword [rsp+40], rcx")
            tmpl = self._string("not enough values to unpack (expected {0}, got {1})")
            self.lines.append(f"    lea rcx, [{tmpl}]")
            self.lines.append(f"    mov edx, 2")
            self.lines.append(f"    lea r8, [rsp+32]")
            self.lines.append(f"    call piton_str_format")
            self.lines.append(f"    add rsp, 48")
            self.lines.append(f"    mov {self._address('@scratch0')}, rax")
            vtype_label = self._string("ValueError")
            self.lines.append(f"    lea rcx, [{vtype_label}]")
            self.lines.append(f"    mov rdx, {self._address('@scratch0')}")
            if handler_label:
                self.lines.append("    call piton_raise")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                self.lines.append("    call piton_raise_unhandled")
            self.lines.append(f"    jmp {ok_label}")
            self.lines.append(f"{many_label}:")
            vmsg = self._string(f"too many values to unpack (expected {expected})")
            self.lines.append(f"    lea rcx, [{vtype_label}]")
            self.lines.append(f"    lea rdx, [{vmsg}]")
            if handler_label:
                self.lines.append("    call piton_raise")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                self.lines.append("    call piton_raise_unhandled")
            self.lines.append(f"{ok_label}:")
        elif op == "get_item":
            container, key = args
            container_type = self.types.get(container)
            if container_type == "str":
                # PARITY_P2_V1: str[s] yields the one-character string.
                self._load_operand(container, "rcx")
                self._load_operand(key, "rdx")
                self.lines.append("    call piton_str_index")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "str"
                return
            if container_type not in {"list", "tuple", "dict", "dict:module"}:
                raise NativeBuildError(f"native subscription not supported for {container_type}")
            if container_type in {"dict", "dict:module"}:
                self._load_operand(container, "rcx")
                self._load_operand(key, "rdx")
                self.lines.append("    call piton_dict_get")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "object:module" if container_type == "dict:module" else "int"
            else:
                self._load_operand(container, "rcx")
                self._load_operand(key, "rdx")
                self.lines.append("    call piton_collection_get")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
        elif op == "get_slice":
            # PARITY_P2_V1: [a:b] slices. Missing bounds arrive as None; the
            # emitter substitutes 0 / INT64_MAX and the runtime helpers
            # normalize negative indices and clamp, matching CPython.
            # SLICE_STEP_V1: with a step operand (possibly runtime), missing
            # bounds become INT64_MIN / INT64_MAX sentinels for
            # direction-aware defaults inside the step helpers.
            container, lower, upper = args[0], args[1], args[2]
            step = args[3] if len(args) > 3 else None
            container_type = self.types.get(container)
            if container_type == "str":
                self._load_operand(container, "rcx")
                if lower is not None:
                    self._load_operand(lower, "rdx")
                else:
                    self.lines.append("    xor edx, edx" if step is None else "    mov rdx, 0x8000000000000000")
                if upper is not None:
                    self._load_operand(upper, "r8")
                else:
                    self.lines.append("    mov r8, 0x7fffffffffffffff")
                if step is None:
                    self.lines.append("    call piton_str_slice")
                else:
                    self._load_operand(step, "r9")
                    self.lines.append("    call piton_str_slice_step")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "str"
            elif container_type in {"list", "tuple"}:
                self._load_operand(container, "rcx")
                if lower is not None:
                    self._load_operand(lower, "rdx")
                else:
                    self.lines.append("    xor edx, edx" if step is None else "    mov rdx, 0x8000000000000000")
                if upper is not None:
                    self._load_operand(upper, "r8")
                else:
                    self.lines.append("    mov r8, 0x7fffffffffffffff")
                if step is None:
                    self.lines.append("    call piton_seq_slice")
                else:
                    self._load_operand(step, "r9")
                    self.lines.append("    call piton_seq_slice_step")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = container_type
            else:
                raise NativeBuildError(f"native slice not supported for {container_type}")
        elif op == "collection_len":
            collection = args[0]
            ctype = self.types.get(collection)
            if ctype == "str":
                # STR_LEN_V1: len('hola') is the C string length.
                self._load_operand(collection, "rcx")
                self.lines.append("    call piton_str_len")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
                return
            if ctype not in {"list", "tuple", "dict", "set"}:
                raise NativeBuildError("native collection_len requires a collection")
            self._load_operand(collection, "rcx")
            if ctype == "dict":
                self.lines.append("    call piton_dict_len")
            elif ctype == "set":
                self.lines.append("    call piton_set_len")
            else:
                self.lines.append("    call piton_collection_len")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "list_append":
            collection, value = args
            ctype = self.types.get(collection)
            if ctype != "list":
                raise NativeBuildError("native list_append requires a list")
            vtype = self.types.get(value, "int")
            type_tag = 0 if vtype == "int" else 1
            self._load_operand(collection, "rcx")
            self._load_operand(value, "rdx")
            self.lines.append(f"    mov r8, {type_tag}")
            self.lines.append("    call piton_list_append")
        elif op == "genexpr_new":
            source = args[0]
            if self.types.get(source) != "list":
                raise NativeBuildError("native genexpr requires a list-backed sequence")
            self._load_operand(source, "rcx")
            self.lines.append("    call piton_genexpr_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "genexpr"
        elif op == "gen_init":
            func_name = args[0]
            gen_args = tuple(args[1]) if len(args) > 1 and args[1] else ()
            layout = self.generator_layouts.get(func_name)
            if layout is None:
                raise NativeBuildError(f"native generator '{func_name}' has no persisted-slot layout")
            params = next(
                (function.params for function in self.mir_module.functions if function.name == func_name),
                [],
            )
            if len(gen_args) != len(params):
                raise NativeBuildError(
                    f"native generator '{func_name}' called with wrong number of arguments "
                    "(defaults not supported yet)"
                )
            if len(gen_args) > 4:
                raise NativeBuildError("native generator calls with more than four arguments are not supported yet")
            self.lines.append(f"    lea rcx, [{func_name}]")
            self.lines.append(f"    mov rdx, {len(layout)}")
            self.lines.append("    call piton_gen_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            for arg, param in zip(gen_args, params):
                index = layout[param]
                self._load_operand(arg, "rax")
                self.lines.append(f"    mov rcx, {self._address(result)}")
                self.lines.append(f"    mov [rcx+{PITON_GEN_LOCAL_BASE + index * 8}], rax")
            self.types[result] = "generator"
        elif op == "gen_collect":
            gen_ref = args[0]
            self._load_operand(gen_ref, "rcx")
            self.lines.append("    call piton_gen_collect")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "list"
        elif op == "gen_next":
            gen_ref = args[0]
            self._load_operand(gen_ref, "rcx")
            self.lines.append("    call piton_gen_next")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "gen_retval":
            gen_ref = args[0]
            self._load_operand(gen_ref, "rcx")
            self.lines.append("    call piton_gen_return_value")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "gen_send":
            gen_ref, send_value, handler_label = args
            self._load_operand(gen_ref, "rcx")
            self._load_operand(send_value, "rdx")
            self.lines.append("    call piton_gen_send")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            target = labels.get(handler_label, handler_label) if handler_label else labels['__exit']
            self.lines.append(f"    jne {target}")
        elif op == "gen_throw":
            gen_ref, exc_type, handler_label = args
            self._load_operand(gen_ref, "rcx")
            self.lines.append(f"    lea rdx, [{self._string(exc_type)}]")
            self.lines.append("    call piton_gen_throw")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            target = labels.get(handler_label, handler_label) if handler_label else labels['__exit']
            self.lines.append(f"    jne {target}")
        elif op == "gen_close":
            gen_ref = args[0]
            self._load_operand(gen_ref, "rcx")
            self.lines.append("    call piton_gen_close")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "coro_run":
            coro_ref = args[0]
            self._load_operand(coro_ref, "rcx")
            self.lines.append("    call piton_coro_run")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "event_run":
            coro_ref = args[0]
            self._load_operand(coro_ref, "rcx")
            self.lines.append("    call piton_event_run")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "task_new":
            coro_ref = args[0]
            self._load_operand(coro_ref, "rcx")
            self.lines.append("    call piton_task_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "task"
        elif op == "task_cancel":
            task_ref = args[0]
            self._load_operand(task_ref, "rcx")
            self.lines.append("    call piton_task_cancel")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "sleep0":
            delay = args[0]
            self._load_operand(delay, "rcx")
            self.lines.append("    call piton_sleep0")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "gather_new":
            n = args[0]
            self.lines.append(f"    mov rcx, {int(n)}")
            self.lines.append("    call piton_gather_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "gather"
        elif op == "gather_add":
            gather_ref, index, task_ref = args
            self._load_operand(gather_ref, "rcx")
            self.lines.append(f"    mov rdx, {int(index)}")
            self._load_operand(task_ref, "r8")
            self.lines.append("    call piton_gather_add")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "set_add":
            collection, value = args
            if self.types.get(collection) != "set":
                raise NativeBuildError("native set_add requires a set")
            if self.types.get(value, "int") != "int":
                raise NativeBuildError("native set comprehensions currently require integer elements")
            self._load_operand(collection, "rcx")
            self._load_operand(value, "rdx")
            self.lines.append("    call piton_set_add")
        elif op == "dict_put":
            collection, key, value = args
            if self.types.get(collection) != "dict":
                raise NativeBuildError("native dict_put requires a dict")
            if self.types.get(key, "int") != "int" or self.types.get(value, "int") != "int":
                raise NativeBuildError("native dict comprehensions currently require integer keys and values")
            self._load_operand(collection, "rcx")
            self._load_operand(key, "rdx")
            self._load_operand(value, "r8")
            self.lines.append("    call piton_dict_put")
        elif op == "object_new":
            class_name, parent_name = args
            finalizer_class = None
            for candidate in self.mir_module_class_mro.get(class_name, []):
                if "__del__" in self.mir_module_classes.get(candidate, set()):
                    finalizer_class = candidate
                    break
            if finalizer_class is None and "__del__" in self.mir_module_classes.get(class_name, set()):
                finalizer_class = class_name
            finalizer_name = f"{finalizer_class}____del__" if finalizer_class else None
            if finalizer_name:
                finalizer_params = self.function_param_map.get(finalizer_name) or []
                if len(finalizer_params) != 1:
                    raise NativeBuildError("native __del__ must take exactly self (FINALIZERS_V1)")
                if self.function_frame_abi.get(finalizer_name, False):
                    raise NativeBuildError("native __del__ cannot use the frame ABI yet (FINALIZERS_V1)")
            self.lines.extend([
                f"    mov rcx, {self._address(result)}", "    call piton_object_free",
            ])
            self.lines.append(f"    lea rcx, [{self._string(class_name)}]")
            if parent_name:
                self.lines.append(f"    lea rdx, [{self._string(parent_name)}]")
            else:
                self.lines.append("    xor edx, edx")
            if finalizer_name:
                self.lines.append(f"    lea r8, [{finalizer_name}]")
            else:
                self.lines.append("    xor r8d, r8d")
            self.lines.append("    call piton_object_new_with_finalizer")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = f"object:{class_name}"
        elif op == "set_attr":
            owner, name, value = args
            owner_type = self.types.get(owner, "")
            prop_class = self._resolve_property_class(owner_type, name)
            if prop_class is not None:
                setter = self.mir_module_class_properties[prop_class][name].get("setter")
                if not setter:
                    raise NativeBuildError(f"property '{name}' of '{owner_type.split(':', 1)[1]}' object has no setter")
                self._load_operand(owner, "rcx")
                self._load_operand(value, "rdx")
                self.lines.append(f"    call {setter}")
            else:
                # ATTRIBUTE_LOOKUP_V2: __setattr__ hook routes normal stores
                # through the hook; the hook's own body bypasses itself
                # (documented V1: inside __setattr__ body, self.x = stores hit
                # the raw object directly, same as super().__setattr__).
                hook = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.mir_module_class_mro.get(class_name, []):
                        if "__setattr__" in self.mir_module_classes.get(candidate, set()):
                            hook = candidate
                            break
                    if hook is None and "__setattr__" in self.mir_module_classes.get(class_name, set()):
                        hook = class_name
                # Bypass when we ARE inside the hook of that class (self-recurse guard)
                current_is_hook = self.function.name.endswith("__setattr__") and hook is not None
                if hook is not None and not current_is_hook:
                    target = f"{hook}____setattr__"
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self._load_operand(value, "r8")
                    self.lines.append(f"    call {target}")
                else:
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self._load_operand(value, "r8")
                    value_type = self.types.get(value, "int")
                    if value_type.startswith("object:"):
                        self.lines.append("    call piton_object_set_tagged")
                    else:
                        self.lines.append("    call piton_object_set")
        elif op == "get_attr":
            owner, name = args
            owner_type = self.types.get(owner, "")
            prop_class = self._resolve_property_class(owner_type, name)
            module_attr_types = {"__name__": "str", "__file__": "str", "__package__": "module-pkg", "modules": "dict:module"}
            if prop_class is not None:
                getter = self.mir_module_class_properties[prop_class][name]["getter"]
                self._load_operand(owner, "rcx")
                self.lines.append(f"    call {getter}")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
            else:
                if owner_type == "closure" and name == "__self__":
                    self._load_operand(owner, "rcx")
                    self.lines.append("    call piton_bound_method_self")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "int"
                    return
                resolved_method = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.mir_module_class_mro.get(class_name, []):
                        if name in self.mir_module_classes.get(candidate, set()):
                            resolved_method = candidate
                            break
                    if resolved_method is None and name in self.mir_module_classes.get(class_name, set()):
                        resolved_method = class_name
                if resolved_method is not None:
                    params = self.function_param_map.get(f"{resolved_method}__{name}") or []
                    if not params:
                        raise NativeBuildError(f"bound method '{name}' has no native signature")
                    target = f"{resolved_method}__{name}"
                    if self.function_frame_abi.get(target, False):
                        frame_size = ((8 + 32 + 15) // 16) * 16
                        self.lines.append(f"    sub rsp, {frame_size}")
                        self._load_operand(owner, "r10")
                        self.lines.append("    mov qword [rsp+32], r10")
                        self.lines.extend([
                            f"    lea rcx, [{target}]", f"    mov edx, {len(params)-1}",
                            "    mov r8d, 1", "    lea r9, [rsp+32]",
                            "    call piton_closure_new_frame", f"    add rsp, {frame_size}",
                            f"    mov {self._address(result)}, rax",
                        ])
                    else:
                        self._load_operand(owner, "r8")
                        self.lines.extend([
                            f"    lea rcx, [{target}]", f"    mov edx, {len(params) - 1}",
                            "    call piton_bound_method_new", f"    mov {self._address(result)}, rax",
                        ])
                    self.types[result] = "closure"
                    return
                # ATTRIBUTE_LOOKUP_V2: __getattr__ hook — only when the class
                # defines it AND the name is NOT a known method (methods must
                # always win, they are resolved above); fields lookup first at
                # runtime, missing → hook.
                getattr_class = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.mir_module_class_mro.get(class_name, []):
                        if "__getattr__" in self.mir_module_classes.get(candidate, set()):
                            getattr_class = candidate
                            break
                    if getattr_class is None and "__getattr__" in self.mir_module_classes.get(class_name, set()):
                        getattr_class = class_name
                if getattr_class is not None:
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self.lines.append(f"    lea r8, [{getattr_class}____getattr__]")
                    self.lines.append("    call piton_object_lookup")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "int"
                    return
                self._load_operand(owner, "rcx")
                self.lines.append(f"    lea rdx, [{self._string(name)}]")
                self.lines.append("    call piton_object_get")
                self.lines.append(f"    mov {self._address(result)}, rax")
                if owner_type == "object:module":
                    self.types[result] = module_attr_types.get(name, "int")
                else:
                    self.types[result] = "int"
        elif op == "del_attr":
            owner, name = args
            owner_type = self.types.get(owner, "")
            prop_class = self._resolve_property_class(owner_type, name)
            if prop_class is not None:
                deleter = self.mir_module_class_properties[prop_class][name].get("deleter")
                if not deleter:
                    raise NativeBuildError(f"property '{name}' of '{owner_type.split(':', 1)[1]}' object has no deleter")
                self._load_operand(owner, "rcx")
                self.lines.append(f"    call {deleter}")
            else:
                # ATTRIBUTE_LOOKUP_V2: __delattr__ hook, with the same
                # inside-the-hook bypass as __setattr__.
                hook = None
                if owner_type.startswith("object:"):
                    class_name = owner_type.split(":", 1)[1]
                    for candidate in self.mir_module_class_mro.get(class_name, []):
                        if "__delattr__" in self.mir_module_classes.get(candidate, set()):
                            hook = candidate
                            break
                    if hook is None and "__delattr__" in self.mir_module_classes.get(class_name, set()):
                        hook = class_name
                current_is_hook = self.function.name.endswith("__delattr__") and hook is not None
                if hook is not None and not current_is_hook:
                    target = f"{hook}____delattr__"
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self.lines.append(f"    call {target}")
                else:
                    raise NativeBuildError(
                        f"native del on '{name}' is not a property of a natively-typed object"
                    )
        elif op == "method_call":
            explicit_class, method_name, owner, raw_values = args
            owner_type = self.types.get(owner, "")
            if owner_type == "str":
                # STR_METHODS_V1: builtin str methods bind statically here;
                # anything not in the table fails closed.
                self._emit_str_method(result, method_name, owner, list(raw_values))
                return
            coll_type = self.types.get(owner, "")
            if coll_type in {"list", "tuple", "dict", "set"}:
                # COLL_METHODS_V1: builtin collection methods bind statically
                # here, mirroring the Linux backend's table.
                self._emit_collection_method(result, method_name, owner, list(raw_values), coll_type)
                return
            class_name = explicit_class or (owner_type.split(":", 1)[1] if owner_type.startswith("object:") else None)
            if not class_name:
                raise NativeBuildError("native method receiver class is not statically known")
            # Resolve method through MRO (C3), fallback to parent chain
            resolved_class = None
            for candidate in self.mir_module_class_mro.get(class_name, []):
                if method_name in self.mir_module_classes.get(candidate, set()):
                    resolved_class = candidate
                    break
            if resolved_class is None:
                class_parents = getattr(self.mir_module, 'class_parents', {})
                resolved_class = class_name
                while resolved_class and method_name not in self.mir_module_classes.get(resolved_class, set()):
                    resolved_class = class_parents.get(resolved_class)
            if not resolved_class:
                resolved_class = class_name  # fallback to original
            if self._resolve_property_class(owner_type, method_name) is not None:
                raise NativeBuildError(
                    f"native property '{method_name}' of '{class_name}' object is not a method (calling a property is unsupported)"
                )
            values = [owner, *raw_values]
            target = f"{resolved_class}__{method_name}"
            if self.function_frame_abi.get(target, False):
                frame_size = ((len(values) * 8 + 32 + 15) // 16) * 16
                self.lines.append(f"    sub rsp, {frame_size}")
                for index, value in enumerate(values):
                    self._load_operand(value, "r10")
                    self.lines.append(f"    mov qword [rsp+32+{index * 8}], r10")
                self.lines.extend(["    lea rcx, [" + target + "]", f"    mov edx, {len(values)}", "    lea r8, [rsp+32]", "    call piton_frame_call", f"    add rsp, {frame_size}"])
            else:
                if len(values) > 4:
                    raise NativeBuildError("native method calls support at most four total arguments")
                for register, value in zip(("rcx", "rdx", "r8", "r9"), values):
                    self._load_operand(value, register)
                self.lines.append(f"    call {target}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = self.function_return_types.get(target, "int")
        elif op in {"math_sqrt", "math_sin", "math_cos", "math_log"}:
            self._load_float_operand(args[0], "xmm0")
            # The Windows runtime helpers use the tagged float wire format:
            # pass the IEEE-754 bits in rcx and receive bits/int in rax.
            self.lines.extend(["    movq rax, xmm0", "    mov rcx, rax", f"    call {_math_fn_map[op]}"])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "float"
        elif op in {"math_floor", "math_ceil", "math_trunc", "math_fabs"}:
            handler_label = args[1] if len(args) > 1 else None
            helper = f"piton_{op}"
            operand_type = self.types.get(args[0])
            if operand_type == "float":
                self._load_operand(args[0], "rcx")
                self.lines.append("    movq xmm0, rcx")
            else:
                self._load_operand(args[0], "rax")
                self.lines.append("    cvtsi2sd xmm0, rax")
            self.lines.append(f"    call {helper}")
            if handler_label is not None:
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                self.lines.append(f"    jne {labels.get(handler_label, handler_label)}")
            if op == "math_fabs":
                self.lines.append("    movq rax, xmm0")
                self.types[result] = "float"
            else:
                self.types[result] = "int"
            self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "math_gcd":
            handler_label = args[2] if len(args) > 2 else None
            if self.types.get(args[0]) not in {"int", "bool"} or self.types.get(args[1]) not in {"int", "bool"}:
                raise NativeBuildError("native math.gcd requires int arguments")
            self._load_operand(args[0], "rcx")
            self._load_operand(args[1], "rdx")
            self.lines.append("    call piton_math_gcd")
            if handler_label is not None:
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                self.lines.append(f"    jne {labels.get(handler_label, handler_label)}")
            self.types[result] = "int"
            self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "cell_new":
            value_arg = args[0]
            self.lines.extend(["    mov rcx, 16", "    call malloc"])
            if value_arg is None:
                self.lines.extend(["    mov qword [rax], 0", "    mov qword [rax+8], 0"])
            else:
                self._load_operand(value_arg, "r10")
                self.lines.extend([f"    mov [rax], r10", f"    mov qword [rax+8], 0"])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "cell"
            if value_arg is not None and isinstance(value_arg, str) and value_arg.startswith("%"):
                self.cell_types[result] = self.types.get(value_arg, "int")
        elif op == "cell_load":
            cell_ptr_name = args[0]
            self.lines.append(f"    mov rax, {self._address(cell_ptr_name)}")
            self.lines.append("    mov rax, [rax]")
            self.lines.append(f"    mov {self._address(result)}, rax")
            cell_type = self.cell_types.get(cell_ptr_name, "int")
            self.types[result] = cell_type
        elif op == "cell_store":
            cell_ptr_name, value_arg = args
            self.lines.append(f"    mov r10, {self._address(cell_ptr_name)}")
            self._load_operand(value_arg, "r11")
            self.lines.extend(["    mov [r10], r11", "    mov qword [r10+8], 0"])
        elif op == "closure_new":
            lifted_name, n_args, capture_ops, has_vararg = args
            frame_size = ((len(capture_ops) * 8 + 48 + 15) // 16) * 16
            self.lines.append(f"    sub rsp, {frame_size}")
            for i, cell in enumerate(capture_ops):
                self._load_operand(cell, "r10")
                self.lines.append(f"    mov qword [rsp+40+{i * 8}], r10")
            self.lines.append(f"    mov qword [rsp+32], {int(has_vararg)}")
            self.lines.extend([
                f"    lea rcx, [{lifted_name}]",
                f"    mov edx, {n_args}",
                f"    mov r8d, {len(capture_ops)}",
                "    lea r9, [rsp+40]",
                "    call piton_closure_new_frame",
                f"    add rsp, {frame_size}",
            ])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "closure"
        elif op == "frame_call":
            callee, call_args = args
            frame_size = ((len(call_args) * 8 + 32 + 15) // 16) * 16
            self.lines.append(f"    sub rsp, {frame_size}")
            for i, value in enumerate(call_args):
                self._load_operand(value, "r10")
                self.lines.append(f"    mov qword [rsp+32+{i * 8}], r10")
            self.lines.extend([
                f"    mov rcx, {self._address(callee)}",
                f"    mov edx, {len(call_args)}",
                "    lea r8, [rsp+32]",
                "    call piton_frame_call",
                f"    add rsp, {frame_size}",
                f"    mov {self._address(result)}, rax",
            ])
            self.types[result] = "int"
        elif op == "closure_call":
            callee, packed = args
            argc, *call_args = packed
            frame_size = ((len(call_args) * 8 + 32 + 15) // 16) * 16
            self.lines.append(f"    sub rsp, {frame_size}")
            for i, value in enumerate(call_args):
                self._load_operand(value, "r10")
                self.lines.append(f"    mov qword [rsp+32+{i * 8}], r10")
            self.lines.extend([
                f"    mov rcx, {self._address(callee)}",
                f"    mov edx, {argc}",
                "    lea r8, [rsp+32]",
                "    call piton_closure_call_frame",
                f"    add rsp, {frame_size}",
            ])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "raise_chain":
            # EXCEPTION_CHAINING_V1: raise with a recorded cause (both must be
            # exception constructors). The cause persists until catch_clear
            # consumes it; the raise then moves through the standard flag path
            # ('with' body, direct handler, or the unhandled printer).
            exception_type, payload, cause_type, cause_payload, handler_label = args
            self.lines.append(f"    lea rcx, [{self._string(exception_type)}]")
            if payload is None:
                self.lines.append("    xor edx, edx")
            elif self.types.get(payload) == "str":
                self._load_operand(payload, "rdx")
            else:
                raise NativeBuildError("native raise-chain payload must be a string")
            self.lines.append(f"    lea r8, [{self._string(cause_type)}]")
            if cause_payload is None:
                self.lines.append("    xor r9d, r9d")
            elif self.types.get(cause_payload) == "str":
                self._load_operand(cause_payload, "r9")
            else:
                raise NativeBuildError("native raise-chain cause payload must be a string")
            if handler_label:
                self.lines.append("    call piton_raise_chain")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                # handler=None per MIR: the runtime stack is empty here, so
                # piton_raise_chain falls through to the unhandled printer.
                self.lines.append("    call piton_raise_chain")
        elif op == "raise_typed":
            exception_type, payload, handler_label = args
            self.lines.append(f"    lea rcx, [{self._string(exception_type)}]")
            if payload is None:
                self.lines.append("    xor edx, edx")
            elif self.types.get(payload) in {"str", "bigint"}:
                self._load_operand(payload, "rdx")
            else:
                raise NativeBuildError("native exception payload must be a string")
            if handler_label:
                # Check if an exception was caught and branch to handler
                self.lines.append("    call piton_raise")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                if exception_type == "StopIteration":
                    # Let the caller's next() operation route the signal to its handler.
                    self.lines.append("    call piton_raise")
                    self.lines.append(f"    jmp {labels['__exit']}")
                else:
                    # No statically-matching handler: report and exit (no stack search)
                    self.lines.append("    call piton_raise_unhandled")
        elif op == "try_push":
            self.lines.append("    call piton_try_push")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
        elif op == "try_pop":
            self.lines.append("    call piton_try_pop")
        elif op == "sys_exit":
            # STDLIB_TIER1_V1: sys.exit([code]) terminates the process.
            # None -> 0, int/bool -> that code. sys.exit(str) is CPython's
            # "print to stderr and exit 1"; fail closed until implemented.
            (code,) = args
            if code is None:
                self.lines.append("    xor ecx, ecx")
            elif self.types.get(code) == "str":
                raise NativeBuildError("native sys.exit(str) is not supported yet")
            else:
                self._load_operand(code, "rcx")
            if result:
                self.lines.append(f"    mov {self._address(result)}, 0")
                self.types[result] = "int"
            self.lines.append("    call piton_exit")
        elif op == "sys_argv":
            # SYS_ARGV_V1: sys.argv as a list of process argument strings.
            self.lines.append("    call piton_argv_new")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "list"
        elif op == "catch_flag":
            self.lines.append("    call piton_catch_flag")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
        elif op == "catch_clear":
            self.lines.append("    call piton_catch_clear")
        elif op == "catch_bind":
            self.lines.append("    call piton_catch_message_safe")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "str"
        elif op == "catch_type":
            # Raw pointer to the statically-interned exception type name.
            self.lines.append("    call piton_catch_type")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "str"
        elif op == "catch_message":
            self.lines.append("    call piton_catch_message_safe")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "str"
        elif op == "reraise_save":
            self.lines.append("    call piton_reraise_save")
        elif op == "raise_active":
            exception_type, handler_label = args
            if handler_label:
                self.lines.append("    call piton_reraise")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                self.lines.append("    call piton_reraise_unhandled")
        elif op == "raise_active_dynamic":
            # Dynamic re-raise: type comes from the saved reraise globals
            # (piton_reraise), label is the statically-chosen handler.
            (handler_label,) = args
            if handler_label:
                self.lines.append("    call piton_reraise")
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            else:
                self.lines.append("    call piton_reraise_unhandled")
        elif op == "branch":
            condition, yes, no = args
            self._emit_truth_test(condition)
            self.lines.extend([f"    jne {labels[yes]}", f"    jmp {labels[no]}"])
        elif op == "jump":
            self.lines.append(f"    jmp {labels[args[0]]}")
        elif op == "call":
            function_operand, call_args = args[0], args[1]
            call_handler = args[2] if len(args) > 2 else None
            function_name = self.aliases.get(function_operand, function_operand)
            values = list(call_args)
            if function_name in {"imprimir", "print"}:
                # PRINT_ARGS_V1: CPython print(a, b, ...) str()s every
                # positional argument and joins them with single spaces. Every
                # operand is printed via the *_raw (no newline) variant and one
                # newline is written after the last argument.
                for index, value in enumerate(values):
                    value_type = self.types.get(value, "int")
                    if index:
                        self.lines.append("    lea rcx, [lit_space]")
                        self.lines.append("    call printf")
                    # PRINT_UNPRINTABLE_V1: iterators, generators, closures,
                    # module markers and builtin markers can never match
                    # CPython (addresses / uninitialized slots) — fail closed
                    # instead of printing garbage.
                    if (
                        str(value_type).startswith("iterator:")
                        or value_type in {"generator", "genexpr", "builtin", "closure", "module", "cell"}
                    ):
                        raise NativeBuildError(
                            f"native print of a {value_type} value is not supported "
                            "(can never match CPython output)"
                        )
                    # SPECIAL_METHOD_LOOKUP_V1: print(obj) despacha a __str__
                    # cuando existe (MRO); el método devuelve una str.
                    if str(value_type).startswith("object:"):
                        cls_name = value_type.split(":", 1)[1]
                        str_cls = None
                        for candidate in self.mir_module_class_mro.get(cls_name, []):
                            if "__str__" in self.mir_module_classes.get(candidate, set()):
                                str_cls = candidate
                                break
                        if str_cls is None and "__str__" in self.mir_module_classes.get(cls_name, set()):
                            str_cls = cls_name
                        if str_cls is not None:
                            self._load_operand(value, "rcx")
                            self.lines.append(f"    call {str_cls}____str__")
                            self.lines.append(f"    mov {self._address(result)}, rax")
                            self._load_operand(result, "rdx")
                            self.lines.append("    lea rcx, [fmt_str_raw]")
                            self.lines.append("    call printf")
                            continue
                        raise NativeBuildError(
                            f"native print of a '{cls_name}' instance without __str__ is not supported "
                            "(can never match CPython object repr)"
                        )
                    if value_type in {"list", "tuple", "dict", "set"}:
                        self._load_operand(value, "rcx")
                        if value_type == "dict":
                            self.lines.append("    call piton_dict_print_raw")
                        elif value_type == "set":
                            self.lines.append("    call piton_set_print_raw")
                        else:
                            self.lines.append("    call piton_collection_print_raw")
                        continue
                    if value_type == "bigint":
                        self.lines.extend([
                            f"    mov rcx, {self._address(value)}",
                            "    call piton_bigint_print_raw",
                        ])
                        continue
                    if value_type == "bool":
                        false_label = self._internal_label("bool_false")
                        ready_label = self._internal_label("bool_ready")
                        self._load_operand(value, "rax")
                        self.lines.extend([
                            "    test rax, rax",
                            f"    jz {false_label}",
                            "    lea rdx, [lit_true]",
                            f"    jmp {ready_label}",
                            f"{false_label}:",
                            "    lea rdx, [lit_false]",
                            f"{ready_label}:",
                        ])
                        fmt = "fmt_str_raw"
                    elif value_type == "none":
                        self.lines.append("    lea rdx, [lit_none]")
                        fmt = "fmt_str_raw"
                    elif value_type == "float":
                        self._load_float_operand(value, "xmm0")
                        self.lines.append("    call piton_print_float_raw")
                        continue
                    elif value_type == "module-pkg":
                        self._load_operand(value, "rcx")
                        self.lines.append("    call piton_print_value_raw")
                        continue
                    else:
                        self._load_operand(value, "rdx")
                        fmt = "fmt_str_raw" if value_type == "str" else "fmt_int_raw"
                    self.lines.append(f"    lea rcx, [{fmt}]")
                    self.lines.append("    call printf")
                self.lines.append("    lea rcx, [fmt_nl]")
                self.lines.extend(["    call printf", "    xor eax, eax"])
                if result:
                    self.lines.append(f"    mov qword {self._address(result)}, 0")
            elif function_name in {"longitud", "len"}:
                if values:
                    v0_type = self.types.get(values[0], "")
                    if v0_type.startswith("object:"):
                        cls_name = v0_type.split(":", 1)[1]
                        len_cls = None
                        for candidate in self.mir_module_class_mro.get(cls_name, []):
                            if "__len__" in self.mir_module_classes.get(candidate, set()):
                                len_cls = candidate
                                break
                        if len_cls is None and "__len__" in self.mir_module_classes.get(cls_name, set()):
                            len_cls = cls_name
                        if len_cls is not None:
                            target = f"{len_cls}____len__"
                            self._load_operand(values[0], "rcx")
                            self.lines.append(f"    call {target}")
                            self.lines.append(f"    mov {self._address(result)}, rax")
                            self.types[result] = "int"
                            return
                if len(values) != 1 or self.types.get(values[0]) not in {"list", "tuple", "dict", "set", "str"}:
                    raise NativeBuildError("native len currently requires one collection")
                self._load_operand(values[0], "rcx")
                ctype = self.types.get(values[0])
                if ctype == "str":
                    # STR_LEN_V1: len('hola') is the C string length.
                    self.lines.append("    call piton_str_len")
                elif ctype == "dict":
                    self.lines.append("    call piton_dict_len")
                elif ctype == "set":
                    self.lines.append("    call piton_set_len")
                else:
                    self.lines.append("    call piton_collection_len")
                self.types[result] = "int"
            elif function_name == "abs":
                if len(values) != 1:
                    raise NativeBuildError("native abs requires one argument")
                vtype = self.types.get(values[0])
                if vtype == "float":
                    self._load_operand(values[0], "rcx")
                    self.lines.extend(["    movq xmm0, rcx", "    call piton_abs_float"])
                    self.lines.append("    movq rax, xmm0")
                    self.types[result] = "float"
                else:
                    self._load_operand(values[0], "rax")
                    self.lines.append("    mov rcx, rax")
                    self.lines.append("    call piton_abs_int")
                    self.types[result] = "int"
            elif function_name in {"all", "any"}:
                if len(values) != 1 or self.types.get(values[0]) not in {"list", "tuple"}:
                    raise NativeBuildError(f"native {function_name} requires one list or tuple (M14 v1)")
                helper = "piton_all_iterable" if function_name == "all" else "piton_any_iterable"
                self._load_operand(values[0], "rcx")
                self.lines.append(f"    call {helper}")
                if call_handler is not None:
                    self.lines.append("    call piton_catch_flag")
                    self.lines.append("    test rax, rax")
                    self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                self.types[result] = "bool"
            elif function_name == "pow":
                if len(values) != 2:
                    raise NativeBuildError("native pow requires exactly two arguments (M14 v1)")
                base_type = self.types.get(values[0])
                if base_type == "float":
                    if self.types.get(values[1]) not in {"int", "bool"}:
                        raise NativeBuildError("native pow(float, e) requires an int exponent (M14 v1)")
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    movq xmm0, rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append("    call piton_pow_float")
                    if call_handler is not None:
                        self.lines.append("    call piton_catch_flag")
                        self.lines.append("    test rax, rax")
                        self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                    self.lines.append("    movq rax, xmm0")
                    self.types[result] = "float"
                elif base_type in {"int", "bool"}:
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append("    call piton_pow_int")
                    if call_handler is not None:
                        self.lines.append("    call piton_catch_flag")
                        self.lines.append("    test rax, rax")
                        self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                    self.types[result] = "int"
                else:
                    raise NativeBuildError("native pow requires int or float base (M14 v1)")
            elif function_name in {"ord", "chr", "bin"}:
                if len(values) != 1:
                    raise NativeBuildError(f"native {function_name} requires exactly one argument")
                arg_type = self.types.get(values[0])
                result_type = "int" if function_name == "ord" else "str"
                if function_name in {"chr", "bin"} and arg_type not in {"int", "bool"}:
                    raise NativeBuildError(f"native {function_name} requires an int argument")
                if function_name == "ord" and arg_type != "str":
                    raise NativeBuildError("native ord requires a str argument")
                helper = f"piton_{function_name}"
                self._load_operand(values[0], "rcx")
                self.lines.append(f"    call {helper}")
                if call_handler is not None:
                    self.lines.append("    call piton_catch_flag")
                    self.lines.append("    test rax, rax")
                    self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                self.types[result] = result_type
            elif function_name in {"round", "redondear"}:
                if len(values) != 1:
                    raise NativeBuildError("native round requires exactly one argument (M14 v1; ndigits not supported)")
                vtype = self.types.get(values[0])
                if vtype == "float":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    movq xmm0, rcx")
                    self.lines.append("    call piton_round_float")
                    if call_handler is not None:
                        self.lines.append("    call piton_catch_flag")
                        self.lines.append("    test rax, rax")
                        self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                elif vtype in {"int", "bool"}:
                    self._load_operand(values[0], "rax")
                else:
                    raise NativeBuildError("native round requires int or float")
                self.types[result] = "int"
            elif function_name in {"entero", "int"}:
                if len(values) != 1:
                    raise NativeBuildError("native int requires exactly one argument")
                vtype = self.types.get(values[0])
                if vtype in {"int", "bool"}:
                    self._load_operand(values[0], "rax")
                elif vtype == "float":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    movq xmm0, rcx")
                    self.lines.append("    cvttsd2si rax, xmm0")
                elif vtype == "str":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_int_from_str")
                    if call_handler is not None:
                        self.lines.append("    call piton_catch_flag")
                        self.lines.append("    test rax, rax")
                        self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                else:
                    raise NativeBuildError("native int() requires int, float or str (M14 v1)")
                self.types[result] = "int"
            elif function_name in {"decimal", "float"}:
                if len(values) != 1:
                    raise NativeBuildError("native float requires exactly one argument")
                vtype = self.types.get(values[0])
                if vtype in {"int", "bool"}:
                    self._load_operand(values[0], "rax")
                    self.lines.append("    cvtsi2sd xmm0, rax")
                    self.lines.append("    movq rax, xmm0")
                elif vtype == "float":
                    self._load_operand(values[0], "rax")
                elif vtype == "str":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_float_from_str")
                    if call_handler is not None:
                        self.lines.append("    call piton_catch_flag")
                        self.lines.append("    test rax, rax")
                        self.lines.append(f"    jne {labels.get(call_handler, call_handler)}")
                    self.lines.append("    movq rax, xmm0")
                else:
                    raise NativeBuildError("native float() requires int, float or str (M14 v1)")
                self.types[result] = "float"
            elif function_name in {"texto", "str"}:
                if len(values) != 1:
                    raise NativeBuildError("native str requires exactly one argument")
                vtype = self.types.get(values[0])
                if vtype in {"int", "bigint"}:
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_str_from_int")
                elif vtype == "float":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    movq xmm0, rcx")
                    self.lines.append("    call piton_str_from_float")
                elif vtype == "bool":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_str_from_bool")
                elif vtype == "none":
                    self.lines.append("    call piton_str_from_none")
                elif vtype == "str":
                    self._load_operand(values[0], "rax")
                else:
                    raise NativeBuildError("native str() requires int/float/bool/None/str (M14 v1)")
                self.types[result] = "str"
            elif function_name in {"booleano", "bool"}:
                if len(values) != 1:
                    raise NativeBuildError("native bool requires exactly one argument")
                vtype = self.types.get(values[0])
                if vtype in {"int", "bool"}:
                    self._load_operand(values[0], "rax")
                    self.lines.append("    test rax, rax")
                    self.lines.append("    setnz al")
                    self.lines.append("    movzx eax, al")
                elif vtype == "float":
                    # bits != 0 y != -0.0: mascara de signo (NaN es True, como CPython)
                    self._load_operand(values[0], "rax")
                    self.lines.append("    mov rcx, 0x7FFFFFFFFFFFFFFF")
                    self.lines.append("    and rax, rcx")
                    self.lines.append("    test rax, rax")
                    self.lines.append("    setnz al")
                    self.lines.append("    movzx eax, al")
                elif vtype == "str":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_str_truthy")
                elif vtype in {"list", "tuple", "dict", "set"}:
                    len_helper = {"dict": "piton_dict_len", "set": "piton_set_len"}.get(vtype, "piton_collection_len")
                    self._load_operand(values[0], "rcx")
                    self.lines.append(f"    call {len_helper}")
                    self.lines.append("    test rax, rax")
                    self.lines.append("    setnz al")
                    self.lines.append("    movzx eax, al")
                elif vtype == "none":
                    self.lines.append("    xor eax, eax")
                else:
                    raise NativeBuildError("native bool() requires int/float/str/collection/None (M14 v1)")
                self.types[result] = "bool"
            elif function_name in {"min", "max"}:
                if len(values) != 2:
                    raise NativeBuildError("native min/max requires two arguments")
                # MINMAX_TYPES_V1: the winner keeps its kind. Both str ->
                # lexicographic compare (raw pointers used to order by
                # address AND print as ints); both float -> float; both
                # int/bool -> int compare ("bool" only when both are bool,
                # else the repr diverges); anything mixed (str/int,
                # int/float, None, bigint, collections) fails closed
                # (CPython raises TypeError, or the winner's type is not
                # statically knowable for int/float mixes).
                t0, t1 = self.types.get(values[0], "int"), self.types.get(values[1], "int")
                if t0 == t1 == "str":
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append("    call piton_str_cmp")
                    self.lines.append("    test rax, rax")
                    pick = "jl" if function_name == "min" else "jg"
                    hit = self._internal_label("minmax_hit")
                    done = self._internal_label("minmax_done")
                    self.lines.append(f"    {pick} {hit}")
                    self._load_operand(values[1], "rax")
                    self.lines.append(f"    jmp {done}")
                    self.lines.append(f"{hit}:")
                    self._load_operand(values[0], "rax")
                    self.lines.append(f"{done}:")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "str"
                elif t0 == t1 == "float":
                    fn = "piton_min_float" if function_name == "min" else "piton_max_float"
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.extend(["    movq xmm0, rcx", "    movq xmm1, rdx", f"    call {fn}"])
                    self.lines.append("    movq rax, xmm0")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "float"
                elif t0 == t1 == "bool":
                    fn = "piton_min_int" if function_name == "min" else "piton_max_int"
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append(f"    call {fn}")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "bool"
                elif t0 == t1 == "int":
                    fn = "piton_min_int" if function_name == "min" else "piton_max_int"
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append(f"    call {fn}")
                    self.lines.append(f"    mov {self._address(result)}, rax")
                    self.types[result] = "int"
                elif {t0, t1} <= {"int", "bool"}:
                    # mixed bool/int: the winner's type depends on runtime
                    # values (min(True, 5) is True, max(True, 5) is 5), so
                    # the result type is not statically knowable.
                    raise NativeBuildError(
                        f"native min/max requires two values of the same kind, not {t0}/{t1}"
                    )
                else:
                    raise NativeBuildError(
                        f"native min/max requires two values of the same kind, not {t0}/{t1}"
                    )
            elif function_name == "sum":
                if len(values) != 1:
                    raise NativeBuildError("native sum requires one collection")
                ctype = self.types.get(values[0])
                if ctype == "dict":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_sum_dict")
                elif ctype == "set":
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_sum_set")
                else:
                    self._load_operand(values[0], "rcx")
                    self.lines.append("    call piton_sum_collection")
                self.types[result] = "int"
            elif function_name == "type":
                if len(values) != 1:
                    raise NativeBuildError("native type requires one argument")
                vtype = self.types.get(values[0], "int")
                # Map emitter type to sub_tag for piton_type_from_raw
                type_tag_map = {
                    "none": 0, "bool": 1, "int": 2, "float": 3,
                    "str": 5, "list": 6, "tuple": 7, "dict": 8, "set": 9, "bigint": 10,
                }
                type_tag = type_tag_map.get(vtype, 2)
                self._load_operand(values[0], "rcx")
                self.lines.append(f"    mov rdx, {type_tag}")
                self.lines.append("    call piton_type_from_raw")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "str"
            elif function_name in {"sorted", "ordenar"}:
                if len(values) != 1 or self.types.get(values[0]) not in {"list", "tuple"}:
                    raise NativeBuildError("native sorted currently requires one list or tuple")
                self._load_operand(values[0], "rcx")
                self.lines.append("    call piton_sorted_new")
                self.types[result] = "list"
            else:
                if function_operand and isinstance(function_operand, str) and "." in function_operand:
                    parts = function_operand.split(".", 1)
                    class_name, method_name = parts
                    call_args_list = list(call_args)
                    self._emit_method_call(class_name, method_name, call_args_list, result)
                else:
                    # BUILTIN_MARKER_V1: a builtin name that reached the generic
                    # call path has no slot value (its load is a marker).
                    # Calling it here used to jump through an uninitialized
                    # slot — garbage / crash. Fail closed instead.
                    if self.types.get(function_operand) == "builtin":
                        raise NativeBuildError(
                            f"native call to builtin '{function_name}' is not supported in this position"
                        )
                    for value in call_args:
                        if self.types.get(value) == "builtin":
                            raise NativeBuildError(
                                f"native call passes builtin '{self.aliases.get(value, value)}' as an argument (unsupported)"
                            )
                    values = self._complete_call_args(function_name, list(call_args))
                    argc = len(values)
                    if function_name in self.function_names:
                        for register, value in zip(("rcx", "rdx", "r8", "r9"), values):
                            self._load_operand(value, register)
                        self.lines.append(f"    call {function_name}")
                    else:
                        # CALLABLE_PROTOCOL_V1: calling a class instance whose
                        # class defines __call__ routes to Class.__call__. The
                        # instance is statically typed, so this is a compile
                        # time decision, not a runtime sniff.
                        caller_type = self.types.get(function_operand, "")
                        call_owner = None
                        if caller_type.startswith("object:"):
                            cls = caller_type.split(":", 1)[1]
                            for candidate in self.mir_module_class_mro.get(cls, []):
                                if "__call__" in self.mir_module_classes.get(candidate, set()):
                                    call_owner = candidate
                                    break
                            if call_owner is None and "__call__" in self.mir_module_classes.get(cls, set()):
                                call_owner = cls
                        if call_owner is not None:
                            target = f"{call_owner}__" + "__call__"
                            call_values = [function_operand, *values]
                            if len(call_values) > 4 and not self.function_frame_abi.get(target, False):
                                raise NativeBuildError("native __call__ supports at most three explicit arguments")
                            if self.function_frame_abi.get(target, False):
                                frame_size = ((len(call_values) * 8 + 32 + 15) // 16) * 16
                                self.lines.append(f"    sub rsp, {frame_size}")
                                for index, value in enumerate(call_values):
                                    self._load_operand(value, "r10")
                                    self.lines.append(f"    mov qword [rsp+32+{index * 8}], r10")
                                self.lines.extend([
                                    f"    lea rcx, [{target}]",
                                    f"    mov edx, {len(call_values)}",
                                    "    lea r8, [rsp+32]",
                                    "    call piton_frame_call",
                                    f"    add rsp, {frame_size}",
                                ])
                            else:
                                for register, value in zip(("rcx", "rdx", "r8", "r9"), call_values):
                                    self._load_operand(value, register)
                                self.lines.append(f"    call {target}")
                        else:
                            frame_size = ((argc * 8 + 32 + 15) // 16) * 16
                            self.lines.append(f"    sub rsp, {frame_size}")
                            for index, value in enumerate(values):
                                self._load_operand(value, "r10")
                                self.lines.append(f"    mov qword [rsp+32+{index * 8}], r10")
                            self.lines.extend([
                                f"    mov rcx, {self._address(function_operand)}",
                                f"    mov edx, {argc}",
                                "    lea r8, [rsp+32]",
                                "    call piton_closure_call_frame",
                                f"    add rsp, {frame_size}",
                            ])
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                if not self.types.get(result):
                    # WRETURNTYPE_V1: statically-known callees propagate
                    # their inferred return type (none/str/float/bool).
                    self.types[result] = self.function_return_types.get(function_name, "int")
        elif op == "truth_test":
            # BOOL_SHORT_V1 / TRUTHY_FIX_V1: full truthiness per static kind
            # (CPython bool()). Reuses the collection len helpers.
            value = args[0]
            vtype = self.types.get(value, "int")
            if vtype == "str":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_str_len")
                self.lines.extend(["    test rax, rax", "    setne al", "    movzx rax, al"])
            elif vtype == "none":
                self.lines.append("    xor eax, eax")
            elif vtype in {"int", "bool", "float"}:
                # int/bool: raw != 0; float: IEEE-754 bits, 0.0 == zero bits
                self._load_operand(value, "rax")
                self.lines.extend(["    test rax, rax", "    setne al", "    movzx rax, al"])
            elif vtype in {"list", "tuple"}:
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_collection_len")
                self.lines.extend(["    test rax, rax", "    setne al", "    movzx rax, al"])
            elif vtype == "dict":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_dict_len")
                self.lines.extend(["    test rax, rax", "    setne al", "    movzx rax, al"])
            elif vtype == "set":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_set_len")
                self.lines.extend(["    test rax, rax", "    setne al", "    movzx rax, al"])
            else:
                raise NativeBuildError(f"native truth test does not support {vtype}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "bool"
        elif op == "call_unpack":
            # CALL_UNPACKING_DYNAMIC4_V1: runtime expansion of *seq / **mapping
            # for a statically-known callee with a plain signature (<=4 params).
            func_name, pos_parts, kw_parts, handler_label = args
            if getattr(self.function, "is_generator", False) or getattr(self.function, "is_coroutine", False):
                raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1 is not supported inside generator bodies yet")
            if func_name not in self.function_names:
                raise NativeBuildError("native dynamic call unpacking requires a module-level function callee")
            f_params = self.function_param_map.get(func_name) or []
            if not f_params or len(f_params) > 4:
                raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1 supports callees with one to four parameters")
            f_defaults = self.function_defaults.get(func_name) or []
            n_params = len(f_params)
            for part in pos_parts:
                if part[0] == "star" and self.types.get(part[1]) not in {"list", "tuple"}:
                    raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1: * operand must be a statically-known list or tuple")
            for part in kw_parts:
                if part[0] == "kwstar":
                    if self.types.get(part[2]) != "dict" or part[2] not in self.strkey_dict_temps:
                        raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1: ** operand must be a dict with constant string keys")
            site = self._internal_label("unpack")
            buf = [f"@unpack_buf{i}" for i in range(4)]
            filled = "@unpack_filled"
            mask = "@unpack_mask"
            # The helpers write outward with out4[i], i.e. ascending addresses;
            # reserved frame slots descend (buf0 > buf1 > buf2 > buf3), so the
            # array base must be the LAST reserved slot (lowest address) and
            # every param index i is addressed as [base + i*8].
            base = f"@unpack_buf3"
            UNBOUND = 0x504954554E424E44  # "PITUNBND" arg-slot sentinel

            self.lines.append(f"    mov qword {self._address(filled)}, 0")
            self.lines.append(f"    mov qword {self._address(mask)}, 0")
            # Pre-fill: default value if any, otherwise the unbound sentinel.
            # (rbx is callee-saved under Win64; the base pointer scratch is rdx,
            # rebuilt before every use because helper calls clobber it.)
            for idx in range(4):
                dv = f_defaults[idx] if idx < len(f_defaults) and idx < n_params else None
                if idx >= n_params or dv is None:
                    self.lines.append(f"    mov rax, {UNBOUND}")
                    self.lines.append(f"    lea rdx, {self._address(base)}")
                    self.lines.append(f"    mov [rdx + {idx * 8}], rax")
                else:
                    if isinstance(dv, str):
                        self.lines.append(f"    lea rax, [{self._string(dv)}]")
                    elif isinstance(dv, bool):
                        self.lines.append(f"    mov rax, {1 if dv else 0}")
                    elif isinstance(dv, int):
                        self.lines.append(f"    mov rax, {dv}")
                    else:
                        raise NativeBuildError("CALL_UNPACKING_DYNAMIC4_V1: only int/bool/str defaults are supported")
                    self.lines.append(f"    lea rdx, {self._address(base)}")
                    self.lines.append(f"    mov [rdx + {idx * 8}], rax")
            for part in pos_parts:
                if part[0] == "value":
                    self.lines.append(f"    mov rcx, {self._address(filled)}")
                    self.lines.append(f"    cmp rcx, {n_params}")
                    self.lines.append(f"    jae {site}_too_many")
                    self._load_operand(part[1], "rax")
                    self.lines.append(f"    lea rdx, {self._address(base)}")
                    self.lines.append("    mov [rdx + rcx*8], rax")
                    self.lines.append("    mov r10, 1")
                    self.lines.append("    shl r10, cl")
                    self.lines.append(f"    or {self._address(mask)}, r10")
                    self.lines.append(f"    add qword {self._address(filled)}, 1")
                else:  # star
                    self._load_operand(part[1], "rcx")
                    self.lines.append(f"    mov rdx, {n_params}")
                    self.lines.append(f"    sub rdx, {self._address(filled)}")
                    self.lines.append(f"    mov r8, {self._address(filled)}")
                    self.lines.append(f"    lea r9, {self._address(base)}")
                    self.lines.append("    lea r8, [r9 + r8*8]")
                    self.lines.append("    call piton_unpack_seq4")
                    self.lines.append("    test rax, rax")
                    self.lines.append(f"    js {site}_runtime_err")
                    self.lines.append("    test rax, rax")
                    self.lines.append(f"    jz {site}_star_skip")
                    # mask |= ((1 << count) - 1) << filled
                    self.lines.append("    mov r10, 1")
                    self.lines.append("    mov rcx, rax")
                    self.lines.append("    shl r10, cl")
                    self.lines.append("    dec r10")
                    self.lines.append(f"    mov rcx, {self._address(filled)}")
                    self.lines.append("    shl r10, cl")
                    self.lines.append(f"    or {self._address(mask)}, r10")
                    self.lines.append(f"    add qword {self._address(filled)}, rax")
                    self.lines.append(f"{site}_star_skip:")
            for part in kw_parts:
                if part[0] == "keyword":
                    name, value = part[1], part[2]
                    if name not in f_params:
                        raise NativeBuildError(f"unexpected keyword argument: {name}")
                    idx = f_params.index(name)
                    self.lines.append(f"    mov rcx, {self._address(mask)}")
                    self.lines.append(f"    bt rcx, {idx}")
                    self.lines.append(f"    jc {site}_dup")
                    self.lines.append(f"    or qword {self._address(mask)}, {1 << idx}")
                    self._load_operand(value, "rax")
                    self.lines.append(f"    lea rdx, {self._address(base)}")
                    self.lines.append(f"    mov [rdx + {idx * 8}], rax")
                else:  # kwstar
                    table_label = f"__piton_unpack_names_{len(self.unpack_tables)}"
                    self.unpack_tables.append((table_label, list(f_params)))
                    self._load_operand(part[2], "rcx")
                    self.lines.append(f"    lea rdx, [{table_label}]")
                    self.lines.append(f"    mov r8d, {n_params}")
                    self.lines.append(f"    lea r9, {self._address(base)}")
                    self.lines.extend([
                        "    sub rsp, 48",
                        f"    lea rax, {self._address(mask)}",
                        "    mov [rsp + 32], rax",
                        "    call piton_dict_unpack4",
                        "    add rsp, 48",
                    ])
                    self.lines.append("    test rax, rax")
                    self.lines.append(f"    js {site}_runtime_err")
            # Missing required argument check via the sentinel.
            for idx in range(n_params):
                has_default = idx < len(f_defaults) and f_defaults[idx] is not None
                if has_default:
                    continue
                self.lines.append(f"    lea rdx, {self._address(base)}")
                self.lines.append(f"    mov rax, [rdx + {idx * 8}]")
                self.lines.append(f"    mov rcx, {UNBOUND}")
                self.lines.append("    cmp rax, rcx")
                self.lines.append(f"    je {site}_missing")
            self.lines.append(f"    lea rdx, {self._address(base)}")
            self.lines.append("    mov rcx, [rdx + 0]")
            if n_params > 2:
                self.lines.append("    mov r8, [rdx + 16]")
            if n_params > 3:
                self.lines.append("    mov r9, [rdx + 24]")
            if n_params > 1:
                self.lines.append("    mov rdx, [rdx + 8]")
            self.lines.append(f"    call {func_name}")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                if not self.types.get(result):
                    self.types[result] = self.function_return_types.get(func_name, "int")
            self.lines.append(f"    jmp {site}_done")
            self.lines.extend([
                f"{site}_too_many:",
                f"    lea rcx, [{self._string('TypeError')}]",
                f"    lea rdx, [{self._string('too many positional arguments for call')}]",
                f"    jmp {site}_raise",
                f"{site}_missing:",
                f"    lea rcx, [{self._string('TypeError')}]",
                f"    lea rdx, [{self._string('missing required positional argument')}]",
                f"    jmp {site}_raise",
                f"{site}_dup:",
                f"    lea rcx, [{self._string('TypeError')}]",
                f"    lea rdx, [{self._string('multiple values for argument')}]",
                f"{site}_raise:",
                "    call piton_raise",
                f"{site}_runtime_err:",
            ])
            if handler_label:
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                self.lines.append(f"    jne {labels.get(handler_label, handler_label)}")
                # The runtime flagged an exception but this static site is the
                # one that handles it; loop exits through the handler above.
            else:
                self.lines.append(f"    lea rcx, [{self._string('TypeError')}]")
                self.lines.append(f"    lea rdx, [{self._string('call unpacking failed')}]")
                self.lines.append("    call piton_raise_unhandled")
            self.lines.append(f"{site}_done:")
        elif op == "gen_yield":
            if getattr(self.function, "is_generator", False) or getattr(self.function, "is_coroutine", False):
                self._emit_gen_suspend(args[0] if args else None, result, await_flag=getattr(self.function, "is_async_generator", False))
                return
            value = args[0] if args else None
            gen_list = "@gen_result"
            if gen_list not in self.types:
                self._reserve(gen_list)
                self.lines.append("    mov ecx, 1")
                self.lines.append("    mov edx, 16")
                self.lines.append("    call piton_collection_new")
                self.lines.append(f"    mov {self._address(gen_list)}, rax")
                self.types[gen_list] = "list"
            if value is not None:
                self._load_operand(value, "rdx")
                self.lines.append(f"    mov rcx, {self._address(gen_list)}")
                self.lines.append("    xor r8, r8")
                self.lines.append("    call piton_list_append")
            if result:
                self.types[result] = "list"
        elif op == "agen_emit":
            if not getattr(self.function, "is_async_generator", False):
                raise NativeBuildError("agen_emit outside an async generator body is not supported")
            # ASYNC_GENERATOR_V1: a data yield (producir) inside an async
            # generator. Structurally identical to gen_yield but clears the
            # await marker so piton_agen_next returns the value as data instead
            # of running it as a coroutine.
            self._emit_gen_suspend(args[0] if args else None, result, await_flag=False)
            return
        elif op == "agen_next":
            if not args:
                raise NativeBuildError("agen_next requires an async generator operand")
            self._load_operand(args[0], "rcx")
            self.lines.append("    call piton_agen_next")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                if not self.types.get(result):
                    self.types[result] = "int"
            return
        elif op == "agen_done":
            if not args:
                raise NativeBuildError("agen_done requires an async generator operand")
            self.lines.append(f"    mov rcx, {self._address(args[0])}")
            self.lines.append("    mov rax, [rcx+16]")   # finished flag
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
                if not self.types.get(result):
                    self.types[result] = "int"
            return
        elif op == "return":
            if getattr(self.function, "is_coroutine", False):
                # A coroutine's return value is the result awaited by the caller.
                if args[0] is not None and args[0] != "None":
                    self._load_operand(args[0], "rax")
                else:
                    self.lines.append("    xor eax, eax")
                # _emit_cleanup frees owned slots (the awaited inner coroutine)
                # and may clobber rax: stash the return value across it.
                self.lines.append(f"    mov {self._address('@scratch0')}, rax")
                self._emit_cleanup()
                self.lines.extend([
                    f"    mov rax, {self._address('@scratch0')}",
                    f"    mov rcx, {self._address('@gen_ptr')}",
                    "    mov qword [rcx+16], 1",
                    "    leave", "    ret",
                ])
                return
            if getattr(self.function, "is_generator", False):
                if args[0] is not None and args[0] != "None":
                    # M5: generator return value (int subset) — stored on the
                    # generator object, exposed via piton_gen_return_value when
                    # the generator stops.
                    self._load_operand(args[0], "rdx")
                    self.lines.append(f"    mov rcx, {self._address('@gen_ptr')}")
                    self.lines.append("    call piton_gen_return_set")
                self._emit_cleanup()
                self.lines.extend([
                    f"    mov rcx, {self._address('@gen_ptr')}",
                    "    mov qword [rcx+16], 1",
                    "    xor eax, eax",
                    "    leave", "    ret",
                ])
                return
            is_gen_return = (args[0] is None or args[0] == "None") and "@gen_result" in self.types
            if isinstance(args[0], str) and self.types.get(args[0]) == "builtin":
                # BUILTIN_MARKER_V1: returning a builtin marker would hand the
                # caller an uninitialized slot.
                raise NativeBuildError(
                    f"native return of builtin '{self.aliases.get(args[0], args[0])}' is not supported"
                )
            if is_gen_return:
                self.lines.append(f"    mov rax, {self._address('@gen_result')}")
                self.lines.append(f"    mov {self._address('@scratch0')}, rax")
                self._emit_cleanup()
                self.lines.extend([
                    f"    mov rcx, {self._address('@scratch0')}",
                    "    call piton_genexpr_new",
                    f"    mov {self._address('@scratch1')}, rax",
                    f"    mov rcx, {self._address('@scratch0')}",
                    "    call piton_collection_free",
                    f"    mov rax, {self._address('@scratch1')}",
                    "    leave", "    ret",
                ])
            else:
                if self.types.get(args[0]) in {"list", "tuple", "dict", "set", "bigint"}:
                    # COLL_RETURN_V1: transfer ownership to the caller. The
                    # pointer is stashed, cleanup frees everything EXCEPT
                    # slots still holding it (value-compared skip: any alias
                    # stays live), and the caller receives it live. Received
                    # collections are never owned, so nothing double-frees
                    # (mirrors Linux, which never frees). bigint returns ride
                    # the same path (their slots live in bigint_slots).
                    self._load_operand(args[0], "rax")
                    self.lines.append(f"    mov {self._address('@scratch0')}, rax")
                    self._emit_cleanup(skip_slot="@scratch0")
                    self.lines.extend([f"    mov rax, {self._address('@scratch0')}", "    leave", "    ret"])
                    return
                self._load_operand(args[0], "rax")
                self.lines.append(f"    mov {self._address('@scratch0')}, rax")
                self._emit_cleanup()
                self.lines.extend([f"    mov rax, {self._address('@scratch0')}", "    leave", "    ret"])
        elif op == "global_decl":
            # GLOBAL_DECL_V1: pure metadata (resolved in the pre-scan); no code.
            return
        elif op == "runtime_call":
            raise NativeBuildError(f"runtime operation not supported in native subset: {args[0]}")

    def _immediate(self, value: Any) -> str:
        if value is None:
            return "0"
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        return self._string(str(value))

    def _complete_call_args(self, function_name: str, values: list[Any]) -> list[Any]:
        defaults = self.function_defaults.get(function_name)
        if not defaults:
            return values
        while len(values) < len(defaults):
            default_value = defaults[len(values)]
            if default_value is None:
                break
            const_temp = f"%d{len(values)}"
            self._emit_const_into(const_temp, default_value)
            values.append(const_temp)
        return values

    def _emit_const_into(self, temp: str, value: Any) -> None:
        if isinstance(value, str):
            self.lines.append(f"    lea rcx, [{self._string(value)}]")
            self.lines.append(f"    mov {self._address(temp)}, rcx")
            self.types[temp] = "str"
        else:
            self.lines.append(f"    mov qword {self._address(temp)}, {self._immediate(value)}")
            self.types[temp] = "none" if value is None else "bool" if isinstance(value, bool) else "int"

    def _string(self, value: str) -> str:
        if value not in self.strings:
            self.strings[value] = f"str_{self.next_string}"
            self.next_string += 1
        return self.strings[value]

    @staticmethod
    def _nasm_db(value: str) -> str:
        utf8 = value.encode("utf-8")
        parts: list[str] = []
        current: list[str] = []
        for byte in utf8:
            if 0x20 <= byte < 0x7f and byte not in (0x22, 0x5c):
                current.append(chr(byte))
            else:
                if current:
                    parts.append(f'"{"".join(current)}"')
                    current = []
                parts.append(f"0x{byte:02x}")
        if current:
            parts.append(f'"{"".join(current)}"')
        return "db " + (", ".join(parts) if parts else '"", 0') + ", 0"

    def _internal_label(self, prefix: str) -> str:
        label = f"__piton_{prefix}_{self.next_internal_label}"
        self.next_internal_label += 1
        return label
    _UNKNOWN = "\x00?"

    def _infer_return_types(self, module) -> dict[str, str]:
        """WRETURNTYPE_V1: narrow, sound return-type inference over MIR
        (mirrors the Linux backend's RETURNTYPE_V1; Linux codegen is
        untouched). Only statically unambiguous origins count: constant
        literals, build_collection kinds, object_new classes, cell
        loads/stores, and direct calls to known functions (recursive,
        cycle-safe). Variables accumulate the union of every store origin
        (fixpoint). A function is typed only when EVERY return resolves
        to the SAME type; anything ambiguous keeps the historical "int"
        default. Fixes `imprimir(f())` printing a raw pointer / 0 / 1
        instead of the value for str/float/bool/None returns, and turns
        None-arithmetic into a build-time rejection instead of computing
        on a null slot. Big ints keep the default (Windows has no
        bigint-return plumbing yet); generators are marked like on Linux
        so their printed form fails closed instead of printing an int.
        """
        by_name = {function.name: function for function in module.functions}
        memo: dict[str, str | None] = {}
        resolving: set[str] = set()

        def literal_type(value) -> str:
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

        def infer(name: str):
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
                origins: dict[str, tuple[str, object]] = {}
                load_src: dict[str, str] = {}
                returns: list = []
                seeded: set[str] = set()

                def note_var(key: str, kind) -> bool:
                    kinds = {kind} if kind else {self._UNKNOWN}
                    if kinds <= var_sets.get(key, set()):
                        return False
                    var_sets.setdefault(key, set()).update(kinds)
                    return True

                def single(key: str):
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
                            elif op in {"load", "cell_load"} and result and iargs:
                                load_src[result] = iargs[0]
                                origins[result] = ("var", iargs[0])
                            elif op == "call" and result:
                                callee = load_src.get(iargs[0], iargs[0] if isinstance(iargs[0], str) else None)
                                if isinstance(callee, str) and callee in by_name and callee not in function.params:
                                    origins[result] = ("call", callee)
                                else:
                                    origins[result] = ("opaque", None)
                            elif op in {"store", "cell_store"} and result is None and iargs:
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

                def operand_type(operand):
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
                    inferred = "none"
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


    def _parse_percent_template(self, template: str) -> tuple[str, list[tuple]]:
        """PCT_FORMAT_V1: same translation contract as the Linux backend —
        %-specs to {} fields for the piton_str_format engine, returning
        (template, fields) with one tuple per conversion:
        (name, conv, flags, width, prec). Anything outside the subset fails
        closed at build time (the template is always a literal here)."""
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
    def _percent_literal_type(value: Any) -> str:
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
        raise NativeBuildError("native str % formatting: unsupported literal argument")

    def _convert_percent_operand(self, spec: str, value: Any, pad: Any = None, etype: Any = None) -> None:
        """Load one %-conversion argument as a C string pointer into rax,
        then apply width/precision padding when pad=(width, prec, flags).
        etype overrides the static type lookup (mapping operands resolve
        frozen literals whose type the table cannot know)."""
        arg_type = etype if etype is not None else self.types.get(value, "int")
        if spec == "s":
            if arg_type == "int":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_str_from_int")
            elif arg_type == "float":
                self._load_float_operand(value, "xmm0")
                self.lines.append("    call piton_str_from_float")
            elif arg_type == "bool":
                false_label = self._internal_label("pct_bool_false")
                ready_label = self._internal_label("pct_bool_ready")
                self._load_operand(value, "rax")
                self.lines.extend([
                    "    test rax, rax",
                    f"    jz {false_label}",
                    "    lea rax, [lit_true]",
                    f"    jmp {ready_label}",
                    f"{false_label}:",
                    "    lea rax, [lit_false]",
                    f"{ready_label}:",
                ])
            elif arg_type == "none":
                self.lines.append("    lea rax, [lit_none]")
            elif arg_type == "str":
                self._load_operand(value, "rax")
            else:
                raise NativeBuildError(f"native str %s does not support {arg_type} arguments")
        elif spec == "d":
            if arg_type in {"int", "bool"}:
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_str_from_int")
            elif arg_type == "float":
                self._load_float_operand(value, "xmm0")
                self.lines.append("    cvttsd2si rax, xmm0")
                self.lines.append("    mov rcx, rax")
                self.lines.append("    call piton_str_from_int")
            else:
                raise NativeBuildError(f"native str %d requires a real number, not {arg_type}")
        elif spec == "r":
            if arg_type == "str":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_str_quote")
            elif arg_type in {"int", "bool", "float", "none"}:
                self._convert_percent_operand("s", value, None, etype)
            else:
                raise NativeBuildError(f"native str %r does not support {arg_type} arguments")
        elif spec == "c":
            if pad is not None and pad[1] >= 0:
                raise NativeBuildError("native str %c does not support precision")
            if arg_type in {"int", "bool"}:
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_percent_chr")
            elif arg_type == "str":
                self._load_operand(value, "rcx")
                self.lines.append("    call piton_str_single_char")
            else:
                raise NativeBuildError(f"native str %c requires int or 1-character str, not {arg_type}")
        elif spec in {"x", "X", "o"}:
            if arg_type not in {"int", "bool"}:
                raise NativeBuildError(f"native str %{spec} requires an int, not {arg_type}")
            self._load_operand(value, "rcx")
            self.lines.append(f"    mov rdx, {16 if spec in {'x', 'X'} else 8}")
            self.lines.append(f"    mov r8, {1 if spec == 'X' else 0}")
            self.lines.append("    call piton_str_from_int_base")
        else:
            raise NativeBuildError(f"native str %{spec} is not supported")
        if pad is not None:
            width, prec, flags = pad
            isnum = 1 if spec in {"d", "x", "X", "o"} else 0
            # flags bit2 carries isnum (Win64 has no 5th register).
            self.lines.append("    mov rcx, rax")
            self.lines.append(f"    mov rdx, {width}")
            self.lines.append(f"    mov r8, {prec}")
            self.lines.append(f"    mov r9, {flags | (isnum << 2)}")
            self.lines.append("    call piton_str_pad")

    def _emit_percent(self, result: Any, translated: str, fields: list[tuple], arg_values: list[Any],
                      arg_etypes: list[Any], handler_label: Any, labels: Any) -> None:
        """PCT_FORMAT_V1 emission: convert each argument to a C string into
        a stack array (width/precision applied per field), call
        piton_str_format."""
        if len(fields) != len(arg_values):
            raise NativeBuildError(
                f"native str % formatting: {len(fields)} conversion(s) but {len(arg_values)} argument(s)"
            )
        frame_size = ((len(arg_values) * 8 + 32 + 15) // 16) * 16
        if frame_size:
            self.lines.append(f"    sub rsp, {frame_size}")
        for index, (field, value, etype) in enumerate(zip(fields, arg_values, arg_etypes)):
            _fname, spec, flags, width, prec = field
            pad = None if (width < 0 and prec < 0) else (width, prec, flags)
            self._convert_percent_operand(spec, value, pad, etype)
            self.lines.append(f"    mov qword [rsp+32+{index * 8}], rax")
            # a raising conversion (percent_chr out of range, single_char on
            # a long string) yields NULL: route after EACH piece so the
            # format engine never dereferences it (same left-to-right order
            # as CPython).
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            if handler_label:
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
            # without a static handler there is nothing to route to: an
            # unhandled raise already printed and exited inside piton_raise,
            # so reaching here means clean state (mirrors raise_typed).
        string_label = self._string(translated)
        self.lines.append(f"    lea rcx, [{string_label}]")
        self.lines.extend([
            f"    mov edx, {len(arg_values)}",
            "    lea r8, [rsp+32]",
            "    call piton_str_format",
        ])
        # the result MUST be stored before the exc routing: piton_catch_flag
        # returns the flag in rax, clobbering the format result (previously a
        # successful formatting inside intentar stored the flag (0) and
        # printed "(null)").
        self.lines.append(f"    mov {self._address(result)}, rax")
        if frame_size:
            self.lines.append(f"    add rsp, {frame_size}")
        self._emit_exc_routing(handler_label, labels)
        self.types[result] = "str"

    def _literal_str_operand(self, operand: Any) -> str | None:
        """FMT_SPEC_V1: the operand's string value when it is PROVABLY a
        literal (a temp recorded in constants, or a literal embedded in the
        MIR). A plain variable name returns None even if it is in constants,
        because a name is not a template; such operands keep the historical
        runtime path ({} / {N} only)."""
        if not isinstance(operand, str):
            return None
        if operand.startswith("%"):
            value = self.constants.get(operand)
            return value if isinstance(value, str) else None
        if operand.isidentifier() or operand in self.function_names:
            return None
        return operand

    @staticmethod
    def _parse_format_template(template: str) -> tuple[str, list[tuple[int, str]]]:
        """FMT_SPEC_V1: mirror of the Linux backend — parse a str.format()
        template into a simplified template (placeholders become {}) plus
        (index, spec) fields. Named placeholders fail closed; escaped braces
        ({{ }}) are left intact for the format engine."""
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
        """FMT_SPEC_V1: type char of a format spec ('' when absent)."""
        if not spec:
            return ""
        t = spec[-1]
        return t if t in "bcdeEfFgGnosxX%" else ""

    @staticmethod
    def _format_spec_presentation(spec: str) -> str:
        """FMT_SPEC_V1: the spec without its type char."""
        if not spec:
            return ""
        t = spec[-1]
        if t in "bcdeEfFgGnosxX%":
            return spec[:-1]
        return spec

    @staticmethod
    def _validate_presentation_spec(pres: str) -> None:
        """FMT_SPEC_V1: static validation of [[fill]align][sign][#][0]
        [width][,][.prec]; unknown specs fail closed at build (the C helper
        returns NULL and a NULL template argument crashed the engine)."""
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

    def _emit_exc_routing(self, handler_label, labels) -> None:
        """TRUEDIV_V1 / CONTAINS_V1: after a call to a raising helper. The
        helper's piton_raise already printed and exited when no handler
        matched, so only the handler case needs the flag branch."""
        if handler_label:
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            target = labels.get(handler_label, handler_label)
            self.lines.append(f"    jne {target}")

    def _emit_contains(self, result: Any, left: Any, right: Any, negate: bool) -> None:
        """CONTAINS_V1: CPython membership (`in` / `no en`) via the
        native_runtime.c helpers.

        Needle restrictions: int/bool needles compare as encoded values
        (pv_int); str needles only work within str haystacks (raw C strings
        both sides). Collection string ELEMENTS are tagged PitonStr objects
        while literal needles are raw pointers — the known untagged-strings
        limitation — so str needles in collections fail closed instead of
        comparing pointers.
        """
        needle_type = self.types.get(left, "int")
        haystack_type = self.types.get(right, "int")
        if haystack_type == "str":
            if needle_type != "str":
                raise NativeBuildError("native 'in' on str requires a str needle")
            self._load_operand(right, "rcx")
            self._load_operand(left, "rdx")
            self.lines.append("    call piton_str_contains")
        elif haystack_type in {"list", "tuple", "dict", "set"}:
            if needle_type not in {"int", "bool"}:
                raise NativeBuildError(
                    f"native 'in' on {haystack_type} requires an int/bool needle, not {needle_type} "
                    "(str elements are tagged objects; untagged needle comparison would be silent approximation)"
                )
            helper = {"list": "piton_seq_contains", "tuple": "piton_seq_contains",
                      "dict": "piton_dict_contains", "set": "piton_set_contains"}[haystack_type]
            self._load_operand(right, "rcx")
            self._load_operand(left, "rdx")
            self.lines.append("    xor r8d, r8d")  # type_tag 0 = raw int, matches list_append
            self.lines.append(f"    call {helper}")
        else:
            raise NativeBuildError(f"native 'in' is not supported on {haystack_type}")
        if negate:
            self.lines.append("    xor eax, 1")
        self.lines.append(f"    mov {self._address(result)}, rax")
        self.types[result] = "bool"

    def _emit_int_overflow_guard(self, handler_label, labels) -> None:
        """INTOVF_GUARD_V1: CPython promotes to arbitrary precision on int
        overflow; the untagged i64 subset used to wrap silently. The promotion
        itself needs a tagged representation (follow-up); until then an
        overflowing runtime operation raises a catchable OverflowError
        instead of corrupting the value. Constant operands are folded exactly
        by the caller, so this only guards the runtime path."""
        ok = self._internal_label("intovf_ok")
        self.lines.append(f"    jno {ok}")
        otype = self._string("OverflowError")
        omsg = self._string("integer arithmetic result too large for the native int subset")
        self.lines.append(f"    lea rcx, [{otype}]")
        self.lines.append(f"    lea rdx, [{omsg}]")
        if handler_label:
            self.lines.append("    call piton_raise")
            self.lines.append("    call piton_catch_flag")
            self.lines.append("    test rax, rax")
            target = labels.get(handler_label, handler_label)
            self.lines.append(f"    jne {target}")
        else:
            self.lines.append("    call piton_raise_unhandled")
        self.lines.append(f"{ok}:")

    def _emit_str_method(self, result: Any, method: str, owner: Any, call_args: list[Any]) -> None:
        """STR_METHODS_V1: builtin str methods bound by static dispatch.

        Mirrors the Linux backend's table. Arity and argument static types
        are validated at build time; anything else fails closed. The runtime
        helpers print and exit on valid-Python but out-of-subset inputs
        (non-ASCII case conversion, non-str join elements, bad format
        fields) — the uncatchable-error convention of the other helpers.
        """
        def require_str(index: int, what: str) -> None:
            if len(call_args) <= index or self.types.get(call_args[index]) != "str":
                raise NativeBuildError(f"native str.{method}() requires {what}")

        def require_count(count: int, what: str) -> None:
            if len(call_args) != count:
                raise NativeBuildError(f"native str.{method}() requires {what}")

        self._load_operand(owner, "rcx")
        if method in {"upper", "lower"}:
            require_count(0, "no arguments")
            self.lines.append(f"    mov edx, {1 if method == 'upper' else 0}")
            self.lines.append("    call piton_str_case")
            self.types[result] = "str"
        elif method == "find":
            require_count(1, "exactly one str argument")
            require_str(0, "a str argument")
            self._load_operand(call_args[0], "rdx")
            self.lines.append("    call piton_str_find")
            self.types[result] = "int"
        elif method in {"startswith", "endswith"}:
            require_count(1, "exactly one str argument")
            require_str(0, "a str argument")
            helper = "piton_str_startswith" if method == "startswith" else "piton_str_endswith"
            self._load_operand(call_args[0], "rdx")
            self.lines.append(f"    call {helper}")
            self.types[result] = "bool"
        elif method == "replace":
            require_count(2, "exactly two str arguments")
            require_str(0, "two str arguments")
            require_str(1, "two str arguments")
            self._load_operand(call_args[0], "rdx")
            self._load_operand(call_args[1], "r8")
            self.lines.append("    call piton_str_replace")
            self.types[result] = "str"
        elif method == "split":
            if len(call_args) > 1:
                raise NativeBuildError("native str.split() requires zero or one argument")
            if call_args and self.types.get(call_args[0]) not in {"str", "none"}:
                raise NativeBuildError("native str.split() requires a str separator or nothing")
            if not call_args or self.types.get(call_args[0]) == "none":
                self.lines.append("    xor edx, edx")
            else:
                self._load_operand(call_args[0], "rdx")
            self.lines.append("    call piton_str_split")
            self.types[result] = "list"
        elif method in {"strip", "lstrip", "rstrip"}:
            require_count(0, "no arguments")
            mode = {"strip": 0, "lstrip": 1, "rstrip": 2}[method]
            self.lines.append(f"    mov edx, {mode}")
            self.lines.append("    call piton_str_strip")
            self.types[result] = "str"
        elif method == "join":
            require_count(1, "exactly one list or tuple argument")
            if self.types.get(call_args[0]) not in {"list", "tuple"}:
                raise NativeBuildError("native str.join() requires one list or tuple argument")
            self._load_operand(call_args[0], "rdx")
            self.lines.append("    call piton_str_join")
            self.types[result] = "str"
        elif method == "format":
            # FMT_SPEC_V1: mirror of the Linux backend. A format spec needs a
            # provably literal template; a runtime template keeps the
            # historical pointer path ({} and {N} only).
            template = self._literal_str_operand(owner)
            if template is None:
                self._emit_format_runtime(result, owner, call_args)
            else:
                self._emit_format_specs(result, owner, template, call_args)
        else:
            raise NativeBuildError(f"native str.{method}() is not supported")
        self.lines.append(f"    mov {self._address(result)}, rax")

    def _emit_format_runtime(self, result: Any, owner: Any, call_args: list[Any]) -> None:
        """FMT_SPEC_V1: historical str.format() path — the template pointer
        is passed straight to piton_str_format, which handles {} and {N}.
        Used when the template is not provably a literal."""
        for value in call_args:
            if self.types.get(value, "int") not in {"int", "float", "bool", "none", "str"}:
                raise NativeBuildError(
                    f"native str.format() does not support {self.types.get(value)} arguments"
                )
        frame_size = ((len(call_args) * 8 + 32 + 15) // 16) * 16
        self.lines.append(f"    sub rsp, {frame_size}")
        for index, value in enumerate(call_args):
            self._convert_format_operand(value)
            self.lines.append(f"    mov qword [rsp+32+{index * 8}], rax")
        # the operand conversions clobbered rcx: reload the template
        self._load_operand(owner, "rcx")
        self.lines.extend([
            f"    mov edx, {len(call_args)}",
            "    lea r8, [rsp+32]",
            "    call piton_str_format",
            f"    add rsp, {frame_size}",
        ])
        self.types[result] = "str"

    def _emit_format_specs(
        self, result: Any, owner: Any, template: str, call_args: list[Any],
    ) -> None:
        """FMT_SPEC_V1: literal-template path. Each field converts to its
        type's string (d/x/X/o/b) and then applies the presentation part
        ([[fill]align][sign][#][0][width][,][.prec]) via
        piton_str_apply_spec. Float presentations and unknown specs fail
        closed at build."""
        simplified, fields = self._parse_format_template(template)
        # a field may be referenced twice ({0} {0}), so the count is not an
        # equality: automatic numbering may not exceed the arguments, and
        # explicit indices are bounds-checked below.
        if len(fields) > len(call_args) and any(f[2] == "{}" for f in fields):
            raise NativeBuildError(
                f"native str.format(): {len(fields)} placeholder(s) but {len(call_args)} argument(s)"
            )
        if not call_args:
            # no arguments: still run the template through the engine — it
            # is what unescapes {{ }} into literal braces (returning the
            # raw operand printed '{{}}' for 'literal {}'.format()).
            # n=0 so the array is never indexed; the slot is a dummy.
            self.lines.append("    sub rsp, 48")
            self.lines.append('    lea rax, [fmt_nl]')
            self.lines.append("    mov qword [rsp+32], rax")
            string_label0 = self._string(simplified)
            self.lines.append(f"    lea rcx, [{string_label0}]")
            self.lines.extend([
                "    mov edx, 0",
                "    lea r8, [rsp+32]",
                "    call piton_str_format",
                "    add rsp, 48",
            ])
            self.types[result] = "str"
            return
        # the stack array is indexed by CALL position (the simplified
        # template keeps explicit {N} tokens), so each call index is
        # converted once with the spec of the field that references it.
        by_index: dict[int, tuple[Any, str]] = {}
        for arg_idx, spec, _token in fields:
            if arg_idx in by_index:
                continue
            if arg_idx < 0 or arg_idx >= len(call_args):
                raise NativeBuildError(
                    f"native str.format(): field index {arg_idx} out of range "
                    f"for {len(call_args)} argument(s)"
                )
            by_index[arg_idx] = (call_args[arg_idx], spec)
        frame_size = ((len(call_args) * 8 + 32 + 15) // 16) * 16
        self.lines.append(f"    sub rsp, {frame_size}")
        for index in range(len(call_args)):
            value, spec = by_index[index]
            type_char = self._format_spec_type(spec)
            pres = self._format_spec_presentation(spec)
            self._validate_presentation_spec(pres)
            arg_type = self.types.get(value, "int")
            if type_char in {"x", "X", "o", "b"}:
                if arg_type not in {"int", "bool"}:
                    raise NativeBuildError(
                        f"native str.format() %{type_char} requires an int, not {arg_type}"
                    )
                base = {"x": 16, "X": 16, "o": 8, "b": 2}[type_char]
                self._load_operand(value, "rcx")
                self.lines.append(f"    mov rdx, {base}")
                self.lines.append(f"    mov r8, {1 if type_char == 'X' else 0}")
                self.lines.append("    call piton_str_from_int_base")
            elif type_char in {"f", "e", "E", "g", "G", "F", "n"}:
                # FLOAT_PRESENT_V1: needs correctly-rounded decimal
                # conversion; repr diverged from CPython, so fail closed.
                raise NativeBuildError(
                    f"native str.format() %{type_char} is not supported yet "
                    "(needs rounded decimal conversion)"
                )
            elif type_char == "%":
                raise NativeBuildError(
                    "native str.format() %% is not supported yet "
                    "(needs rounded percentage conversion)"
                )
            else:
                self._convert_format_operand(value)
            if pres:
                pres_label = self._string(pres)
                self.lines.append("    mov rcx, rax")
                self.lines.append(f"    lea rdx, [{pres_label}]")
                self.lines.append("    call piton_str_apply_spec")
            self.lines.append(f"    mov qword [rsp+32+{index * 8}], rax")
        string_label = self._string(simplified)
        self.lines.append(f"    lea rcx, [{string_label}]")
        self.lines.extend([
            f"    mov edx, {len(call_args)}",
            "    lea r8, [rsp+32]",
            "    call piton_str_format",
            f"    add rsp, {frame_size}",
        ])
        self.types[result] = "str"

    def _convert_format_operand(self, value: Any) -> None:
        """Load one str.format() argument as a C string pointer into rax,
        str()-converted by static type (mirrors CPython's str() conversion)."""
        arg_type = self.types.get(value, "int")
        if arg_type == "int":
            self._load_operand(value, "rcx")
            self.lines.append("    call piton_str_from_int")
        elif arg_type == "float":
            self._load_float_operand(value, "xmm0")
            self.lines.append("    call piton_str_from_float")
        elif arg_type == "bool":
            false_label = self._internal_label("fmt_bool_false")
            ready_label = self._internal_label("fmt_bool_ready")
            self._load_operand(value, "rax")
            self.lines.extend([
                "    test rax, rax",
                f"    jz {false_label}",
                "    lea rax, [lit_true]",
                f"    jmp {ready_label}",
                f"{false_label}:",
                "    lea rax, [lit_false]",
                f"{ready_label}:",
            ])
        elif arg_type == "none":
            self.lines.append("    lea rax, [lit_none]")
        elif arg_type == "str":
            self._load_operand(value, "rax")
        else:
            raise NativeBuildError(f"native str.format() does not support {arg_type} arguments")

    def _emit_collection_method(self, result: Any, method: str, owner: Any,
                                  call_args: list[Any], coll_type: str) -> None:
        """COLL_METHODS_V1: builtin collection methods bound by static dispatch.

        Mirrors the Linux backend. Mutators return None, reads follow the
        get_item convention (decoded values, statically "int"). Value
        operands pass raw with type_tag 0 (int convention, like
        piton_list_append); str needles inside collections are the known
        untagged limitation.
        """
        def require_count(counts: tuple[int, ...], what: str) -> None:
            if len(call_args) not in counts:
                raise NativeBuildError(f"native {coll_type}.{method}() requires {what}")

        self._load_operand(owner, "rcx")
        if coll_type == "list":
            if method == "append":
                require_count((1,), "exactly one argument")
                self._load_operand(call_args[0], "rdx")
                self.lines.append("    xor r8d, r8d")
                self.lines.append("    call piton_list_append")
                self.types[result] = "none"
            elif method == "pop":
                require_count((0, 1), "zero or one int argument")
                if call_args and self.types.get(call_args[0]) not in {"int", "bool"}:
                    raise NativeBuildError("native list.pop() requires an int index or nothing")
                if call_args:
                    self._load_operand(call_args[0], "rdx")
                else:
                    self.lines.append("    mov rdx, -1")
                self.lines.append("    call piton_seq_pop")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
            elif method == "reverse":
                require_count((0,), "no arguments")
                self.lines.append("    call piton_seq_reverse")
                self.types[result] = "none"
            elif method == "insert":
                require_count((2,), "exactly two arguments (index, value)")
                if self.types.get(call_args[0]) not in {"int", "bool"}:
                    raise NativeBuildError("native list.insert() requires an int index")
                self._load_operand(call_args[0], "rdx")
                self._load_operand(call_args[1], "r8")
                self.lines.append("    xor r9d, r9d")
                self.lines.append("    call piton_seq_insert")
                self.types[result] = "none"
            elif method == "count":
                require_count((1,), "exactly one argument")
                self._load_operand(call_args[0], "rdx")
                self.lines.append("    xor r8d, r8d")
                self.lines.append("    call piton_seq_count")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
            elif method == "sort":
                require_count((0,), "no arguments")
                self.lines.append("    call piton_seq_sort")
                self.types[result] = "none"
            else:
                raise NativeBuildError(f"native list.{method}() is not supported")
        elif coll_type == "tuple":
            if method == "count":
                require_count((1,), "exactly one argument")
                self._load_operand(call_args[0], "rdx")
                self.lines.append("    xor r8d, r8d")
                self.lines.append("    call piton_seq_count")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
            else:
                raise NativeBuildError(f"native tuple.{method}() is not supported")
        elif coll_type == "dict":
            if method == "get":
                require_count((1, 2), "one or two arguments (key[, default])")
                if len(call_args) == 2 and self.types.get(call_args[1]) not in {"int", "bool"}:
                    raise NativeBuildError("native dict.get() default must be an int in this subset")
                self._load_operand(call_args[0], "rdx")
                if len(call_args) == 2:
                    self._load_operand(call_args[1], "r8")
                    self.lines.append("    call piton_dict_get_d")
                else:
                    self.lines.append("    call piton_dict_get_1")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
            else:
                raise NativeBuildError(f"native dict.{method}() is not supported")
        elif coll_type == "set":
            if method == "add":
                require_count((1,), "exactly one argument")
                self._load_operand(call_args[0], "rdx")
                self.lines.append("    call piton_set_add")
                self.types[result] = "none"
            else:
                raise NativeBuildError(f"native set.{method}() is not supported")
        else:
            raise NativeBuildError(f"native {coll_type}.{method}() is not supported")

    @staticmethod
    def _global_label(name: str) -> str:
        """GLOBAL_DECL_V1: nasm data label for a shared global."""
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
        return f"piton_g_{cleaned}"

    def _scan_module_globals(self, module: MIRModule) -> None:
        """GLOBAL_DECL_V1: mirror of the Linux backend's pre-scan — shared
        globals are the module-stored names declared global in at least one
        function; module-level constant/collection/object initializers feed
        the cross-function type table."""
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
                return "bigint" if abs(value) > (1 << 60) else "int"
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

    def _is_gen_function(self) -> bool:
        function = self.function
        return bool(
            getattr(function, "is_generator", False)
            or getattr(function, "is_coroutine", False)
            or getattr(function, "is_async_generator", False)
        )

    def _emit_truth_test(self, operand: Any) -> None:
        if self.types.get(operand, "int") == "str":
            self._load_operand(operand, "rcx")
            self.lines.extend(["    call strlen", "    test rax, rax"])
        elif self.types.get(operand) == "none":
            self.lines.extend(["    xor eax, eax", "    test rax, rax"])
        elif self.types.get(operand) == "float":
            self._load_float_operand(operand, "xmm0")
            self.lines.extend([
                "    pxor xmm1, xmm1", "    ucomisd xmm0, xmm1", "    setne al",
                "    setp dl", "    or al, dl", "    movzx eax, al", "    test eax, eax",
            ])
        elif self.types.get(operand) in {"list", "tuple"}:
            self._load_operand(operand, "rcx")
            self.lines.extend([
                "    call piton_collection_len", "    test rax, rax",
            ])
        elif self.types.get(operand) == "dict":
            self._load_operand(operand, "rcx")
            self.lines.extend([
                "    call piton_dict_len", "    test rax, rax",
            ])
        elif self.types.get(operand) == "set":
            self._load_operand(operand, "rcx")
            self.lines.extend([
                "    call piton_set_len", "    test rax, rax",
            ])
        else:
            self._load_operand(operand, "rax")
            self.lines.append("    test rax, rax")

    def _emit_string_concat(self, left: Any, right: Any, result: str) -> None:
        self._load_operand(left, "rax")
        self.lines.append(f"    mov {self._address('@scratch0')}, rax")
        self._load_operand(right, "rax")
        self.lines.append(f"    mov {self._address('@scratch1')}, rax")
        self.lines.extend([
            f"    mov rcx, {self._address('@scratch0')}",
            "    call strlen",
            f"    mov {self._address('@scratch2')}, rax",
            f"    mov rcx, {self._address('@scratch1')}",
            "    call strlen",
            f"    mov {self._address('@scratch3')}, rax",
            f"    add rax, {self._address('@scratch2')}",
            "    inc rax",
            "    mov rcx, rax",
            "    call malloc",
            f"    mov {self._address(result)}, rax",
            "    mov rcx, rax",
            f"    mov rdx, {self._address('@scratch0')}",
            f"    mov r8, {self._address('@scratch2')}",
            "    call memcpy",
            f"    mov rcx, {self._address(result)}",
            f"    add rcx, {self._address('@scratch2')}",
            f"    mov rdx, {self._address('@scratch1')}",
            f"    mov r8, {self._address('@scratch3')}",
            "    inc r8",
            "    call memcpy",
            f"    mov rax, {self._address(result)}",
        ])
        self.types[result] = "str"

    def _emit_cleanup(self, skip_slot: Any = None) -> None:
        # COLL_RETURN_V1: when returning a heap object, cleanup must skip
        # every slot that still holds its pointer (a plain free would
        # use-after-free through any alias). The skip compares VALUES, so
        # it is sound for arbitrary store/load aliasing; skip_slot holds
        # the stashed return pointer and is never in either free list.
        for slot, free_function in self.owned_slots:
            self.lines.append(f"    mov rcx, {self._address(slot)}")
            skip_label = None
            if skip_slot is not None:
                skip_label = self._internal_label("cleanup_skip")
                self.lines.append(f"    cmp rcx, {self._address(skip_slot)}")
                self.lines.append(f"    je {skip_label}")
            self.lines.extend([
                f"    call {free_function}",
                f"    mov qword {self._address(slot)}, 0",
            ])
            if skip_label is not None:
                self.lines.append(f"{skip_label}:")
        for slot in self.bigint_slots:
            self.lines.append(f"    mov rcx, {self._address(slot)}")
            skip_label = None
            if skip_slot is not None:
                skip_label = self._internal_label("cleanup_skip")
                self.lines.append(f"    cmp rcx, {self._address(skip_slot)}")
                self.lines.append(f"    je {skip_label}")
            self.lines.extend([
                "    call piton_bigint_free",
                f"    mov qword {self._address(slot)}, 0",
            ])
            if skip_label is not None:
                self.lines.append(f"{skip_label}:")

    def _load_float_operand(self, operand: Any, register: str) -> None:
        if self.types.get(operand) == "float":
            self.lines.append(f"    movq {register}, {self._address(operand)}")
        else:
            self._load_operand(operand, "rax")
            self.lines.append(f"    cvtsi2sd {register}, rax")

    def _emit_bigint_binary(self, operator: str, left: Any, right: Any, result: str) -> None:
        func = {"+": "piton_bigint_add", "-": "piton_bigint_sub", "*": "piton_bigint_mul",
                "//": "piton_bigint_floor_div", "%": "piton_bigint_mod"}.get(operator)
        if not func:
            raise NativeBuildError(f"native bigint operator not supported: {operator}")
        left_type = self.types.get(left, "int")
        right_type = self.types.get(right, "int")
        scratch0 = self._address("@scratch0")
        scratch1 = self._address("@scratch1")
        if left_type == "bigint" and right_type == "bigint":
            self.lines.extend([
                f"    mov rcx, {self._address(left)}",
                f"    mov rdx, {self._address(right)}",
                f"    call {func}",
                f"    mov {self._address(result)}, rax",
            ])
        elif left_type == "bigint":
            self.lines.append(f"    mov {scratch0}, rcx")
            self.lines.extend([f"    mov rcx, {self._address(left)}", f"    mov {scratch0}, rcx"])
            self._load_operand(right, "rcx")
            self.lines.extend([
                "    call piton_bigint_from_i64",
                f"    mov rdx, rax",
                f"    mov rcx, {scratch0}",
                f"    call {func}",
                f"    mov {self._address(result)}, rax",
            ])
        elif right_type == "bigint":
            self._load_operand(left, "rcx")
            self.lines.extend([
                "    call piton_bigint_from_i64",
                f"    mov {scratch0}, rax",
                f"    mov rdx, {self._address(right)}",
                f"    mov rcx, {scratch0}",
                f"    call {func}",
                f"    mov {self._address(result)}, rax",
            ])
        else:
            self._load_operand(left, "rcx")
            self.lines.extend([
                "    call piton_bigint_from_i64",
                f"    mov {scratch0}, rax",
            ])
            self._load_operand(right, "rcx")
            self.lines.extend([
                "    call piton_bigint_from_i64",
                f"    mov rdx, rax",
                f"    mov rcx, {scratch0}",
                f"    call {func}",
                f"    mov {self._address(result)}, rax",
            ])
        self.types[result] = "bigint"
        self.bigint_slots.append(result)

    def _emit_bigint_compare(self, operator: str, left: Any, right: Any, result: str) -> None:
        # BIGINT_CMP_MIXED_V1: a plain int is not a PitonBigInt*, so the
        # both-sides-pointer helper dereferenced it as a struct (SIGSEGV on
        # `10 ** 20 > 5`). The mixed case uses the int-aware helper, negating
        # when the int is the left operand.
        left_big = self.types.get(left) == "bigint"
        right_big = self.types.get(right) == "bigint"
        if left_big and not right_big:
            self.lines.extend([
                f"    mov rcx, {self._address(left)}",
                f"    mov rdx, {self._address(right)}",
                "    call piton_bigint_cmp_int",
            ])
        elif right_big and not left_big:
            self.lines.extend([
                f"    mov rcx, {self._address(right)}",
                f"    mov rdx, {self._address(left)}",
                "    call piton_bigint_cmp_int",
                "    neg rax",
            ])
        else:
            self.lines.extend([
                f"    mov rcx, {self._address(left)}",
                f"    mov rdx, {self._address(right)}",
                "    call piton_bigint_cmp",
            ])
        self.lines.append("    cmp rax, 0")
        condition = {"==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge"}[operator]
        self.lines.extend([f"    set{condition} al", "    movzx rax, al"])
        self.lines.append(f"    mov {self._address(result)}, rax")
        self.types[result] = "bool"


def emit_nasm(module: MIRModule) -> str:
    return Win64NasmEmitter().emit(module)


def compile_native(source: str, output: str | Path) -> Path:
    import sys
    if not sys.platform.startswith("win32"):
        from .linux_x86 import compile_native_linux
        return compile_native_linux(source, output)
    try:
        hir = lower_cst_to_hir(parse(source))
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    from_imports = {}
    for statement in hir.body:
        if getattr(statement, "kind", None) == HIRKind.IMPORT_FROM:
            mod_name = getattr(statement, "module", None)
            if mod_name and mod_name not in {"asyncio", "math", "sys"}:
                raise NativeBuildError(f"native from-import requires multi-file compilation: {mod_name}")
            for alias in getattr(statement, "names", []):
                if mod_name in {"asyncio", "math", "sys"}:
                    from_imports[(mod_name, alias.asname or alias.name)] = True
    try:
        mir = lower_hir_to_mir(hir, from_imports=from_imports or None)
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    return _compile_native_mir(mir, output)


def resolve_native_module(root: Path, dotted: str) -> Path:
    """Resuelve un módulo nativo: hermano (``x.piton``), paquete (``x/__init__.piton``)
    o submódulo (``pkg/sub.piton``). Fail-closed con mensaje claro en cada caso."""
    parts = dotted.split(".")
    if len(parts) == 1:
        direct = root / f"{parts[0]}.piton"
        if direct.is_file():
            return direct
        package_init = root / parts[0] / "__init__.piton"
        if package_init.is_file():
            return package_init
        if (root / parts[0]).is_dir():
            raise NativeBuildError(
                f"native module not found: '{parts[0]}' requires package '{parts[0]}' with __init__.piton"
            )
        raise NativeBuildError(f"native module not found: {parts[0]}")
    package_dir = root / parts[0]
    if not (package_dir / "__init__.piton").is_file():
        raise NativeBuildError(
            f"native module not found: '{dotted}' requires package '{parts[0]}' with __init__.piton"
        )
    current = package_dir
    for part in parts[1:-1]:
        current = current / part
        if not (current / "__init__.piton").is_file():
            raise NativeBuildError(
                f"native module not found: package '{current.name}' requires __init__.piton"
            )
    candidate = current / f"{parts[-1]}.piton"
    if candidate.is_file():
        return candidate
    sub_package_init = current / parts[-1] / "__init__.piton"
    if sub_package_init.is_file():
        return sub_package_init
    raise NativeBuildError(f"native module not found: {dotted}")


def _scan_native_modules(
    entry: Path,
) -> tuple[dict[str, Any], dict[tuple[str, str], bool], dict[str, tuple[str, bool]]]:
    """Cargan los módulos nativos (hermano, paquete o submódulo) referenciados
    por el entry, cerrando transitivamente ANY import statement the scanned
    modules contain (IMPORT_RELATIVE_V1).

    Returns ``(modules, from_imports, module_meta)``:
    - ``modules``: dotted name -> HIR for every reachable module (package
      intermediates included), used to lift their functions with qualified names
      (``pkg__numeros__suma``).
    - ``from_imports``: entry-level ``(mod, sym)`` bindings.
    - ``module_meta``: dotted name -> (abs path, is_package) for the sys.modules
      catalog.

    Relative imports (``desde . importar x`` / ``desde .x importar f``) are
    resolved against the package context of the module that CONTAINS them: a
    package's ``__init__.piton`` resolves against its own dotted name; the entry
    script (like CPython ``python main.py``) has no parent package, so any
    relative import in it is rejected fail-closed with the same meaning as
    CPython's ImportError. Relative imports beyond one level (``desde ..``) stay
    fail-closed."""
    root = entry.parent
    modules: dict[str, Any] = {}
    module_meta: dict[str, tuple[str, bool]] = {}
    from_imports: dict[tuple[str, str], bool] = {}

    def parse_path(path: Path) -> Any:
        return lower_cst_to_hir(parse(path.read_text(encoding="utf-8-sig")))

    def register(dotted: str, path: Path, is_package: bool) -> None:
        if dotted in modules:
            return
        modules[dotted] = parse_path(path)
        module_meta[dotted] = (str(path.resolve()), is_package)

    def register_chain(dotted: str) -> None:
        """Registers ``pkg``, ``pkg.sub``, ... up to ``dotted`` by resolving every
        intermediate package on disk; fail-closed when any missing."""
        parts = dotted.split(".")
        for length in range(1, len(parts) + 1):
            prefix = ".".join(parts[:length])
            if prefix in modules or prefix in {"asyncio", "math", "sys"}:
                continue
            path = resolve_native_module(root, prefix)
            register(prefix, path, path.name == "__init__.piton")

    def is_entry(package: str | None) -> bool:
        return package == "<entry>"

    def scan_module_hir(hir: Any, dotted: str, package: str) -> None:
        """Processes one module body, queuing every module it imports. ``dotted``
        identifies the module for relative resolution when it is a package
        (``__init__``); entry top-level passes ``package="<entry>"`` (no parent
        package)."""
        for statement in hir.body:
            if statement.kind.name == "IMPORT":
                for alias in statement.names:
                    if alias.name in {"asyncio", "math", "sys"}:
                        continue
                    register_chain(alias.name)
            elif statement.kind.name == "IMPORT_FROM":
                level = getattr(statement, "level", 0) or 0
                mod_name = getattr(statement, "module", None)
                is_star = bool(getattr(statement, "is_star", False))
                if level:
                    if is_entry(package):
                        raise NativeBuildError(
                            "native relative import ('desde . importar ...') in the entry module "
                            "is not supported: like CPython scripts it has no parent package"
                        )
                    if is_star:
                        raise NativeBuildError(
                            "native star imports ('desde . importar *') are only supported "
                            "at the entry module, not inside imported modules"
                        )
                    # M8 IMPORT_RELATIVE_V2: N levels — drop (level-1)
                    # segments from the module's own package context.
                    up = level - 1
                    base = package
                    if up > 0:
                        head = base.split(".")
                        if up > len(head) - 1:
                            raise NativeBuildError(
                                f"relative import level {level} escapes the package at '{base}'"
                            )
                        base = ".".join(head[:-up])
                    if not mod_name:
                        for alias in statement.names:
                            register_chain(f"{base}.{alias.name}")
                    else:
                        register_chain(f"{base}.{mod_name}")
                else:
                    if not mod_name or mod_name in {"asyncio", "math", "sys"}:
                        continue
                    register_chain(mod_name)
                    if not is_entry(package):
                        if is_star:
                            raise NativeBuildError(
                                "native star imports ('desde X importar *') are only supported "
                                "at the entry module, not inside imported modules"
                            )
                        continue
                    if is_star:
                        from_imports[(mod_name, "*")] = True
                    else:
                        for alias in statement.names:
                            from_imports[(mod_name, alias.asname or alias.name)] = True

    entry_hir = parse_path(entry)
    # Entry module: top-level (no package context; relative imports fail-closed).
    scan_module_hir(entry_hir, "", "<entry>")
    # Fixed point: every module registered above may itself import siblings
    # (relative against its own package context) or further absolute modules.
    pending = True
    while pending:
        pending = False
        for dotted, hir in list(modules.items()):
            path, is_package = module_meta[dotted]
            if is_package:
                package_ctx = dotted  # __init__ resolves relative against itself
            else:
                package_ctx = dotted.rsplit(".", 1)[0] if "." in dotted else "<entry>"
            before = set(modules)
            scan_module_hir(hir, dotted, package_ctx)
            if set(modules) != before:
                pending = True

    # IMPORT_CYCLIC_V1: imported module bodies are static-lifted, never
    # executed, but the ALLOWED import shapes must mirror what CPython actually
    # runs. CPython executes a from-import by first fully importing the target
    # (so a cyclic `desde X importar N` succeeds iff X is not mid-initialization
    # or N was already bound when the importing statement ran; otherwise it
    # raises ImportError "cannot import name"). We simulate that order over the
    # static bodies and fail-closed where CPython would raise.
    states: dict[str, str] = {}
    live: dict[str, set[str]] = {}

    def from_import(package_ctx: str, statement: Any, names: set[str]) -> None:
        level = getattr(statement, "level", 0) or 0
        mod_name = getattr(statement, "module", None)
        is_star = bool(getattr(statement, "is_star", False))
        if is_star:
            return
        if level:
            # M8 IMPORT_RELATIVE_V2: level-1 correction against package_ctx
            up = level - 1
            if up > 0:
                head = package_ctx.split(".")
                if up > len(head) - 1:
                    raise NativeBuildError(
                        f"relative import level {level} escapes the package at '{package_ctx}'"
                    )
                package_ctx = ".".join(head[:-up])
            if not mod_name:
                for alias in statement.names:
                    simulate_execute(f"{package_ctx}.{alias.name}")
                    names.add(alias.asname or alias.name)
                return
            target = f"{package_ctx}.{mod_name}"
        else:
            target = mod_name
        if not target:
            return
        for length in range(1, len(target.split(".")) + 1):
            simulate_execute(".".join(target.split(".")[:length]))
        if target in {"asyncio", "math", "sys"}:
            for alias in statement.names:
                names.add(alias.asname or alias.name)
            return
        if states.get(target) == "IN_PROGRESS":
            for alias in statement.names:
                if alias.name not in live.get(target, ()):
                    raise NativeBuildError(
                        f"native from-import 'desde {target} importar {alias.name}' "
                        f"fails like CPython: cannot import name '{alias.name}' from "
                        f"partially initialized module '{target}'"
                    )
        else:
            for alias in statement.names:
                if alias.name not in live[target]:
                    raise NativeBuildError(
                        f"native from-import 'desde {target} importar {alias.name}' "
                        f"fails like CPython: cannot import name '{alias.name}' from "
                        f"module '{target}'"
                    )
        for alias in statement.names:
            names.add(alias.asname or alias.name)

    def simulate_execute(dotted: str) -> None:
        if dotted in states or dotted in {"asyncio", "math", "sys"}:
            return
        states[dotted] = "IN_PROGRESS"
        hir = modules[dotted]
        meta_is_package = module_meta[dotted][1]
        package_ctx = dotted if meta_is_package else (dotted.rsplit(".", 1)[0] if "." in dotted else "<entry>")
        names = live.setdefault(dotted, set())
        for statement in hir.body:
            kind = statement.kind.name
            if kind == "FUNC_DEF":
                names.add(statement.name)
            elif kind == "IMPORT":
                for alias in statement.names:
                    if alias.name in {"asyncio", "math", "sys"}:
                        continue
                    for length in range(1, len(alias.name.split(".")) + 1):
                        simulate_execute(".".join(alias.name.split(".")[:length]))
                    names.add(alias.asname or alias.name.split(".")[0])
                    if not alias.asname:
                        names.add(alias.name.split(".")[0])
            elif kind == "IMPORT_FROM":
                from_import(package_ctx, statement, names)
        states[dotted] = "DONE"

    entry_names: set[str] = set()
    for statement in entry_hir.body:
        if statement.kind.name == "IMPORT":
            for alias in statement.names:
                if alias.name in {"asyncio", "math", "sys"}:
                    continue
                for length in range(1, len(alias.name.split(".")) + 1):
                    simulate_execute(".".join(alias.name.split(".")[:length]))
        elif statement.kind.name == "IMPORT_FROM":
            from_import("<entry>", statement, entry_names)
    return modules, from_imports, module_meta


def compile_native_files(entry: str | Path, output: str | Path) -> Path:
    import sys
    if not sys.platform.startswith("win32"):
        from .linux_x86 import compile_native_linux_files
        return compile_native_linux_files(entry, output)
    entry_path = Path(entry).resolve()
    try:
        hir = lower_cst_to_hir(parse(entry_path.read_text(encoding="utf-8-sig")))
        modules, from_imports, module_meta = _scan_native_modules(entry_path)
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    try:
        mir = lower_hir_to_mir(
            hir, modules, from_imports=from_imports,
            entry_file=str(entry_path), module_meta=module_meta,
        )
    except (MIRLoweringError, LoweringError) as error:
        raise NativeBuildError(str(error)) from error
    return _compile_native_mir(mir, output)


def _compile_native_mir(mir: MIRModule, output: str | Path) -> Path:
    nasm = shutil.which("nasm")
    gcc = shutil.which("gcc")
    if not nasm or not gcc:
        raise NativeBuildError("Fase 5 requiere nasm y gcc en PATH")
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="piton-native-") as directory:
        directory_path = Path(directory)
        assembly = directory_path / "program.asm"
        object_file = directory_path / "program.obj"
        runtime_object = directory_path / "native_runtime.obj"
        assembly.write_text(emit_nasm(mir), encoding="ascii")
        assembled = subprocess.run([nasm, "-f", "win64", str(assembly), "-o", str(object_file)], capture_output=True, text=True)
        if assembled.returncode:
            raise NativeBuildError(assembled.stderr or assembled.stdout)
        runtime_source = Path(__file__).with_name("native_runtime.c")
        runtime_compiled = subprocess.run(
            [gcc, "-std=c11", "-O2", "-c", str(runtime_source), "-o", str(runtime_object)],
            capture_output=True, text=True,
        )
        if runtime_compiled.returncode:
            raise NativeBuildError(runtime_compiled.stderr or runtime_compiled.stdout)
        linked = subprocess.run([gcc, str(object_file), str(runtime_object), "-o", str(output_path), "-lm"], capture_output=True, text=True)
        if linked.returncode:
            raise NativeBuildError(linked.stderr or linked.stdout)
    return output_path
