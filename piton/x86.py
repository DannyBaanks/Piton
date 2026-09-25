"""Backend x86-64 Win64 mínimo desde MIR.

El alcance inicial es int/string, variables locales, aritmética, comparaciones,
branch/jump, funciones simples y ``imprimir`` mediante el CRT de Windows.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from piton.lower import lower_cst_to_hir
from piton.hir import HIRKind
from piton.mir import MIRBlock, MIRFunction, MIRInstruction, MIRLoweringError, MIRModule, NATIVE_BUILTIN_MODULES, lower_hir_to_mir
from piton.parser import parse


class NativeBuildError(RuntimeError):
    pass


_BUILTINS = {"imprimir", "print", "rango", "range", "longitud", "len", "enumerar", "enumerate", "abs", "max", "min", "sum", "tipo", "type", "texto", "str", "entero", "int", "decimal", "float", "booleano", "bool", "lista", "list", "tupla", "tuple", "conjunto", "set", "diccionario", "dict", "entrada", "input", "abrir", "open", "ordenar", "sorted", "all", "any", "bin", "chr", "ord", "pow", "round", "redondear"}

_math_fn_map = {"math_sqrt": "piton_float_sqrt"}

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
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
        self.function_param_map = {function.name: list(function.params) for function in module.functions}
        self.function_frame_abi = {function.name: bool(function.frame_abi) for function in module.functions}
        # CALL_UNPACKING_DYNAMIC4_V1: temp/local names of dicts proven to be
        # built from constant-string keys (the only **-unpackable dicts).
        self.strkey_dict_temps: set[str] = set()
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
            "extern piton_calliter_new", "extern piton_calliter_next",
            "extern piton_collection_print", "extern piton_collection_free",
            "extern piton_gc_collect",
            "extern piton_collection_live_count",
            "extern piton_object_delattr",
            "extern piton_dict_new", "extern piton_dict_put",
            "extern piton_dict_len", "extern piton_dict_get",
            "extern piton_dict_print", "extern piton_dict_free",
            "extern piton_dict_live_count",
            "extern piton_set_new", "extern piton_set_add",
            "extern piton_set_len", "extern piton_set_print",
            "extern piton_set_free", "extern piton_set_live_count",
            "extern piton_raise",
            "extern piton_raise_unhandled",
            "extern piton_try_push", "extern piton_try_pop", "extern piton_try_set_accepted",
            "extern piton_catch_flag", "extern piton_catch_type", "extern piton_catch_message", "extern piton_catch_message_safe", "extern piton_catch_clear",
            "extern piton_reraise_save", "extern piton_reraise", "extern piton_reraise_unhandled",
            "extern piton_abs_int", "extern piton_abs_float",
            "extern piton_min_int", "extern piton_max_int", "extern piton_min_float", "extern piton_max_float",
            "extern piton_sum_collection", "extern piton_sum_dict", "extern piton_sum_set",
            "extern piton_all_iterable", "extern piton_any_iterable",
            "extern piton_pow_int", "extern piton_pow_float",
            "extern piton_ord", "extern piton_chr", "extern piton_bin", "extern piton_round_float",
            "extern piton_int_from_str", "extern piton_float_from_str",
            "extern piton_str_from_int", "extern piton_str_from_bool",
            "extern piton_str_from_none", "extern piton_str_from_float",
            "extern piton_str_truthy",
            "extern piton_math_floor", "extern piton_math_ceil", "extern piton_math_trunc",
            "extern piton_math_fabs", "extern piton_math_gcd",
            "extern piton_float_sin", "extern piton_float_cos", "extern piton_float_log",
            "extern piton_exit", "extern piton_argv_new", "extern piton_set_context_from_reraise",
            "extern piton_type_name", "extern piton_type_from_raw",
            "extern piton_object_new", "extern piton_object_new_with_parent", "extern piton_object_new_with_finalizer", "extern piton_object_set", "extern piton_object_set_tagged", "extern piton_object_get", "extern piton_object_lookup",

            "extern piton_object_free", "extern piton_object_live_count",
            "extern piton_print_float",
            "extern piton_print_value",
            "extern piton_bigint_from_str", "extern piton_bigint_from_i64", "extern piton_bigint_free",
            "extern piton_bigint_add", "extern piton_bigint_sub", "extern piton_bigint_mul",
            "extern piton_bigint_neg", "extern piton_bigint_cmp",
            "extern piton_bigint_floor_div", "extern piton_bigint_mod",
            "extern piton_bigint_print",
            "extern piton_closure_new8", "extern piton_closure_call6",
            "extern piton_closure_new_frame", "extern piton_closure_call_frame", "extern piton_bound_method_new", "extern piton_bound_method_self",
            "extern piton_frame_call",
            "extern piton_unpack_seq4", "extern piton_dict_unpack4",
            "extern piton_gen_return_value", "extern piton_math_fabs", "extern piton_math_gcd",
            "extern piton_raise_chain",
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
            'lit_true: db "True", 0',
            'lit_false: db "False", 0',
            'lit_none: db "None", 0',
        ])
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
        self.bigint_slots = []
        self.constants = {}
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
                if instruction.op == "sys_argv" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_collection_free"))
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
        self.bigint_slots = []
        self.constants = {}
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
                if instruction.op == "sys_argv" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_collection_free"))
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
            self.lines.append(f"    mov {register}, __float64__({operand!r})")
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
            else:
                self._load_operand(source, "rcx")
                self.lines.append("    call piton_iterator_new_any")
                self.types[result] = f"iterator:{source_type or 'unknown'}"
            self.lines.append(f"    mov {self._address(result)}, rax")
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
                if iterator_type == "iterator:enumerate":
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
            self.types[result] = "tuple" if iterator_type in {"iterator:enumerate", "iterator:zip"} else "str" if iterator_type == "iterator:dict" else "int"
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
            if name in self.function_names:
                self.lines.append(f"    lea rax, [{name}]")
                self.lines.append(f"    mov {self._address(result)}, rax")
                return
            if name in _BUILTINS and name not in self.slots:
                return
            self.lines.append(f"    mov rax, {self._address(name)}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            if name in self.strkey_dict_temps:
                self.strkey_dict_temps.add(result)
        elif op == "store":
            self.lines.append(f"    mov rax, {self._address(args[1]) if isinstance(args[1], str) and args[1].startswith('%') else self._immediate(args[1])}")
            self.lines.append(f"    mov {self._address(args[0])}, rax")
            if isinstance(args[1], str):
                self.types[args[0]] = self.types.get(args[1], "int")
                if args[1] in self.strkey_dict_temps:
                    self.strkey_dict_temps.add(args[0])
        elif op == "binary":
            operator, left, right = args
            left_type = self.types.get(left, "int")
            right_type = self.types.get(right, "int")
            if "str" in {left_type, right_type}:
                if operator == "+" and left_type == right_type == "str":
                    self._emit_string_concat(left, right, result)
                    return
                raise NativeBuildError(f"native string operator not supported yet: {operator}")
            if "bigint" in {left_type, right_type}:
                self._emit_bigint_binary(operator, left, right, result)
                return
            if "float" in {left_type, right_type}:
                if operator not in {"+", "-", "*"}:
                    raise NativeBuildError(f"native float operator not supported yet: {operator}")
                self._load_float_operand(left, "xmm0")
                self._load_float_operand(right, "xmm1")
                instruction_name = {"+": "addsd", "-": "subsd", "*": "mulsd"}[operator]
                self.lines.append(f"    {instruction_name} xmm0, xmm1")
                self.lines.extend(["    movq rax, xmm0", f"    mov {self._address(result)}, rax"])
                self.types[result] = "float"
                return
            self._load_operand(left, "rax")
            self._load_operand(right, "rcx")
            if operator == "+":
                self.lines.append("    add rax, rcx")
            elif operator == "-":
                self.lines.append("    sub rax, rcx")
            elif operator == "*":
                self.lines.append("    imul rax, rcx")
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
            self._load_operand(operand, "rax")
            if self.types.get(operand) == "float" and operator in {"+", "-"}:
                if operator == "-":
                    self.lines.append("    btc rax, 63")
                self.types[result] = "float"
            elif operator == "-":
                self.lines.append("    neg rax")
                self.types[result] = "int"
            elif operator == "+":
                self.types[result] = self.types.get(operand, "int")
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
            if operator == "es":
                self._load_operand(left, "rax")
                self._load_operand(right, "rcx")
                self.lines.append("    cmp rax, rcx")
                self.lines.append("    sete al")
                self.lines.append("    movzx rax, al")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "bool"
                return
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
        elif op == "get_item":
            container, key = args
            container_type = self.types.get(container)
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
        elif op == "collection_len":
            collection = args[0]
            ctype = self.types.get(collection)
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
            elif owner_type.startswith("object:"):
                class_name = owner_type.split(":", 1)[1]
                resolved_class = None
                for candidate in self.mir_module_class_mro.get(class_name, []):
                    if "__delattr__" in self.mir_module_classes.get(candidate, set()):
                        resolved_class = candidate
                        break
                if resolved_class is None and "__delattr__" in self.mir_module_classes.get(class_name, set()):
                    resolved_class = class_name
                if resolved_class:
                    target = f"{resolved_class}____delattr__"
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self.lines.append(f"    call {target}")
                else:
                    self._load_operand(owner, "rcx")
                    self.lines.append(f"    lea rdx, [{self._string(name)}]")
                    self.lines.append("    call piton_object_delattr")
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
            self.types[result] = "int"
        elif op == "math_sqrt":
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
        elif op in {"math_sin", "math_cos", "math_log"}:
            handler_label = args[1] if len(args) > 1 else None
            # Tagged float wire format: IEEE-754 bits in rcx, bits back in rax.
            if self.types.get(args[0]) == "float":
                self._load_operand(args[0], "rcx")
            else:
                self._load_operand(args[0], "rax")
                self.lines.extend(["    cvtsi2sd xmm0, rax", "    movq rcx, xmm0"])
            self.lines.append(f"    call piton_float_{op.split('_', 1)[1]}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "float"
            if handler_label is not None:
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                self.lines.append(f"    jne {labels.get(handler_label, handler_label)}")
        elif op == "sys_exit":
            # V1: sys.exit terminates immediately (no SystemExit unwinding).
            code = args[0] if args else None
            if code is not None and self.types.get(code) not in {"int", "bool", "none"}:
                raise NativeBuildError("native sys.exit requires an int code (V1)")
            if code is None:
                self.lines.append("    xor ecx, ecx")
            else:
                self._load_operand(code, "rcx")
            self.lines.append("    call piton_exit")
            if result:
                self.lines.append(f"    mov qword {self._address(result)}, 0")
                self.types[result] = "none"
        elif op == "sys_argv":
            self.lines.append("    call piton_argv_new")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "list"
        elif op == "set_context_from_reraise":
            self.lines.append("    call piton_set_context_from_reraise")
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
            lifted_name, n_args, capture_ops = args[0], args[1], args[2]
            has_vararg = args[3] if len(args) > 3 else 0
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
                if not values:
                    self.lines.append("    lea rcx, [fmt_str]")
                    self.lines.append("    xor edx, edx")
                else:
                    value = values[0]
                    value_type = self.types.get(value, "int")
                    # SPECIAL_METHOD_LOOKUP_V1: print(obj) despacha a __str__
                    # cuando existe (MRO); el método devuelve una str.
                    if value_type.startswith("object:"):
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
                            self.lines.append("    lea rcx, [fmt_str]")
                            self.lines.extend(["    call printf", "    xor eax, eax"])
                            if result:
                                self.lines.append(f"    mov qword {self._address(result)}, 0")
                            return
                    if value_type in {"list", "tuple", "dict", "set"}:
                        self._load_operand(value, "rcx")
                        if value_type == "dict":
                            self.lines.append("    call piton_dict_print")
                        elif value_type == "set":
                            self.lines.append("    call piton_set_print")
                        else:
                            self.lines.append("    call piton_collection_print")
                        if result:
                            self.lines.append(f"    mov qword {self._address(result)}, 0")
                        return
                    if value_type == "bigint":
                        self.lines.extend([
                            f"    mov rcx, {self._address(value)}",
                            "    call piton_bigint_print",
                        ])
                        if result:
                            self.lines.append(f"    mov qword {self._address(result)}, 0")
                        return
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
                        fmt = "fmt_str"
                    elif value_type == "none":
                        self.lines.append("    lea rdx, [lit_none]")
                        fmt = "fmt_str"
                    elif value_type == "float":
                        self._load_float_operand(value, "xmm0")
                        self.lines.append("    call piton_print_float")
                        if result:
                            self.lines.append(f"    mov qword {self._address(result)}, 0")
                        return
                    elif value_type == "module-pkg":
                        self._load_operand(value, "rcx")
                        self.lines.append("    call piton_print_value")
                        if result:
                            self.lines.append(f"    mov qword {self._address(result)}, 0")
                        return
                    else:
                        self._load_operand(value, "rdx")
                        fmt = "fmt_str" if value_type == "str" else "fmt_int"
                    self.lines.append(f"    lea rcx, [{fmt}]")
                self.lines.extend(["    call printf", "    xor eax, eax"])
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
                if len(values) != 1 or self.types.get(values[0]) not in {"list", "tuple", "dict", "set"}:
                    raise NativeBuildError("native len currently requires one collection")
                self._load_operand(values[0], "rcx")
                ctype = self.types.get(values[0])
                if ctype == "dict":
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
                vtype = self.types.get(values[0])
                fn = "piton_min_int" if function_name == "min" else "piton_max_int"
                if vtype == "float":
                    fn = "piton_min_float" if function_name == "min" else "piton_max_float"
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.extend(["    movq xmm0, rcx", "    movq xmm1, rdx", f"    call {fn}"])
                    self.lines.append("    movq rax, xmm0")
                    self.types[result] = "float"
                else:
                    self._load_operand(values[0], "rcx")
                    self._load_operand(values[1], "rdx")
                    self.lines.append(f"    call {fn}")
                    self.types[result] = "int"
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
                    self.types[result] = "int"
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
                    self.types[result] = "int"
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
                if self.types.get(args[0]) in {"list", "tuple", "dict", "set"}:
                    raise NativeBuildError("returning native collections is not supported yet")
                self._load_operand(args[0], "rax")
                self.lines.append(f"    mov {self._address('@scratch0')}, rax")
                self._emit_cleanup()
                self.lines.extend([f"    mov rax, {self._address('@scratch0')}", "    leave", "    ret"])
        elif op == "runtime_call":
            raise NativeBuildError(f"runtime operation not supported in native subset: {args[0]}")
        elif op == "gen_retval":
            gen_ref = args[0]
            self._load_operand(gen_ref, "rcx")
            self.lines.append("    call piton_gen_return_value")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "math_fabs":
            self._load_float_operand(args[0], "xmm0")
            self.lines.extend(["    movq rax, xmm0", "    mov rcx, rax", "    call piton_math_fabs"])
            self.lines.append(f"    movq {self._address(result)}, xmm0")
            self.types[result] = "float"
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
        elif op == "raise_chain":
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
                self.lines.append("    call piton_raise_chain")

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

    def _emit_cleanup(self) -> None:
        for slot, free_function in self.owned_slots:
            self.lines.extend([
                f"    mov rcx, {self._address(slot)}",
                f"    call {free_function}",
                f"    mov qword {self._address(slot)}, 0",
            ])
        for slot in self.bigint_slots:
            self.lines.extend([
                f"    mov rcx, {self._address(slot)}",
                "    call piton_bigint_free",
                f"    mov qword {self._address(slot)}, 0",
            ])

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
        self.lines.extend([
            f"    mov rcx, {self._address(left)}",
            f"    mov rdx, {self._address(right)}",
            "    call piton_bigint_cmp",
            "    cmp rax, 0",
        ])
        condition = {"==": "e", "!=": "ne", "<": "l", "<=": "le", ">": "g", ">=": "ge"}[operator]
        self.lines.extend([f"    set{condition} al", "    movzx rax, al"])
        self.lines.append(f"    mov {self._address(result)}, rax")
        self.types[result] = "bool"


def emit_nasm(module: MIRModule) -> str:
    return Win64NasmEmitter().emit(module)


def compile_native(source: str, output: str | Path) -> Path:
    hir = lower_cst_to_hir(parse(source))
    from_imports = {}
    for statement in hir.body:
        if getattr(statement, "kind", None) == HIRKind.IMPORT_FROM:
            mod_name = getattr(statement, "module", None)
            if mod_name and mod_name not in NATIVE_BUILTIN_MODULES:
                raise NativeBuildError(f"native from-import requires multi-file compilation: {mod_name}")
            for alias in getattr(statement, "names", []):
                if mod_name in NATIVE_BUILTIN_MODULES:
                    from_imports[(mod_name, alias.asname or alias.name)] = True
    try:
        mir = lower_hir_to_mir(hir, from_imports=from_imports or None)
    except MIRLoweringError as error:
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
            if prefix in modules or prefix in NATIVE_BUILTIN_MODULES:
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
                    if alias.name in NATIVE_BUILTIN_MODULES:
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
                    if not mod_name or mod_name in NATIVE_BUILTIN_MODULES:
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
        if target in NATIVE_BUILTIN_MODULES:
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
        if dotted in states or dotted in NATIVE_BUILTIN_MODULES:
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
                    if alias.name in NATIVE_BUILTIN_MODULES:
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
                if alias.name in NATIVE_BUILTIN_MODULES:
                    continue
                for length in range(1, len(alias.name.split(".")) + 1):
                    simulate_execute(".".join(alias.name.split(".")[:length]))
        elif statement.kind.name == "IMPORT_FROM":
            from_import("<entry>", statement, entry_names)
    return modules, from_imports, module_meta


def compile_native_files(entry: str | Path, output: str | Path) -> Path:
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
