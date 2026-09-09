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
from piton.mir import MIRBlock, MIRFunction, MIRInstruction, MIRLoweringError, MIRModule, lower_hir_to_mir
from piton.parser import parse


class NativeBuildError(RuntimeError):
    pass


_BUILTINS = {"imprimir", "print", "rango", "range", "longitud", "len", "enumerar", "enumerate", "abs", "max", "min", "sum", "tipo", "type", "texto", "str", "entero", "int", "decimal", "float", "booleano", "bool", "lista", "list", "tupla", "tuple", "conjunto", "set", "diccionario", "dict", "entrada", "input", "abrir", "open", "ordenar", "sorted"}


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

    def emit(self, module: MIRModule) -> str:
        self.mir_module = module
        self.mir_module_classes = getattr(module, 'classes', {})
        self.function_names = {function.name for function in module.functions}
        self.function_defaults = {function.name: list(function.defaults) for function in module.functions}
        self.lines = [
            "default rel", "extern printf", "extern strcmp", "extern strlen",
            "extern malloc", "extern memcpy", "section .text",
        ]
        self.lines[0:0] = [
            "extern piton_collection_new", "extern piton_collection_put",
            "extern piton_collection_len", "extern piton_collection_get",
            "extern piton_collection_print", "extern piton_collection_free",
            "extern piton_collection_live_count",
            "extern piton_dict_new", "extern piton_dict_put",
            "extern piton_dict_len", "extern piton_dict_get",
            "extern piton_dict_print", "extern piton_dict_free",
            "extern piton_dict_live_count",
            "extern piton_set_new", "extern piton_set_add",
            "extern piton_set_len", "extern piton_set_print",
            "extern piton_set_free", "extern piton_set_live_count",
            "extern piton_raise",
            "extern piton_try_push", "extern piton_try_pop", "extern piton_try_set_accepted",
            "extern piton_catch_flag", "extern piton_catch_type", "extern piton_catch_message", "extern piton_catch_clear",
            "extern piton_abs_int", "extern piton_abs_float",
            "extern piton_min_int", "extern piton_max_int", "extern piton_min_float", "extern piton_max_float",
            "extern piton_sum_collection", "extern piton_sum_dict", "extern piton_sum_set",
            "extern piton_type_name", "extern piton_type_from_raw",
            "extern piton_object_new", "extern piton_object_new_with_parent", "extern piton_object_set", "extern piton_object_get",
            "extern piton_object_free", "extern piton_object_live_count",
            "extern piton_print_float",
            "extern piton_bigint_from_str", "extern piton_bigint_from_i64", "extern piton_bigint_free",
            "extern piton_bigint_add", "extern piton_bigint_sub", "extern piton_bigint_mul",
            "extern piton_bigint_neg", "extern piton_bigint_cmp",
            "extern piton_bigint_floor_div", "extern piton_bigint_mod",
            "extern piton_bigint_print",
        ]
        for function in module.functions:
            self._emit_function(function)
        self.lines.append("section .rdata")
        for value, label in self.strings.items():
            self.lines.append(f"{label}: {self._nasm_db(value)}")
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
                if instruction.op == "object_new" and instruction.result:
                    self.owned_slots.append((instruction.result, "piton_object_free"))
                if instruction.op == "store":
                    self._reserve(instruction.args[0])
        for scratch in ("@scratch0", "@scratch1", "@scratch2", "@scratch3"):
            self._reserve(scratch)
        for default_slot in ("%d0", "%d1", "%d2", "%d3"):
            self._reserve(default_slot)
        # Keep the Win64 32-byte shadow area below every local slot.
        frame = max(48, ((self.next_slot + 32 + 15) // 16) * 16)
        label = "main" if function.name == "<module>" else function.name
        self.lines.extend([f"global {label}", f"{label}:", "    push rbp", "    mov rbp, rsp", f"    sub rsp, {frame}"])
        for slot, _ in self.owned_slots:
            self.lines.append(f"    mov qword {self._address(slot)}, 0")
        if len(function.params) > 4:
            raise NativeBuildError("native calls with more than four parameters are not supported yet")
        if function.params:
            for register, name in zip(("rcx", "rdx", "r8", "r9"), function.params):
                self._store_slot(name, register)
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

    def _reserve(self, name: str | None) -> None:
        if name is not None and name not in self.slots:
            self.slots[name] = self.next_slot
            self.next_slot += 8

    def _address(self, name: str) -> str:
        self._reserve(name)
        return f"[rbp-{self.slots[name]}]"

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
        if op == "const":
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
            if name in _BUILTINS:
                return
            self.lines.append(f"    mov rax, {self._address(name)}")
            self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "store":
            self.lines.append(f"    mov rax, {self._address(args[1]) if isinstance(args[1], str) and args[1].startswith('%') else self._immediate(args[1])}")
            self.lines.append(f"    mov {self._address(args[0])}, rax")
            if isinstance(args[1], str):
                self.types[args[0]] = self.types.get(args[1], "int")
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
                    self._load_operand(item, "r8")
                    self._load_operand(item, "r9")
                    self.lines.append("    call piton_collection_put")
            self.types[result] = kind
        elif op == "get_item":
            container, key = args
            container_type = self.types.get(container)
            if container_type not in {"list", "tuple", "dict"}:
                raise NativeBuildError(f"native subscription not supported for {container_type}")
            if container_type == "dict":
                self._load_operand(container, "rcx")
                self._load_operand(key, "rdx")
                self.lines.append("    call piton_dict_get")
                self.lines.append(f"    mov {self._address(result)}, rax")
                self.types[result] = "int"
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
        elif op == "object_new":
            class_name, parent_name = args
            self.lines.extend([
                f"    mov rcx, {self._address(result)}", "    call piton_object_free",
            ])
            if parent_name:
                self.lines.extend([
                    f"    lea rcx, [{self._string(class_name)}]",
                    f"    lea rdx, [{self._string(parent_name)}]",
                    "    call piton_object_new_with_parent",
                ])
            else:
                self.lines.extend([
                    f"    lea rcx, [{self._string(class_name)}]",
                    "    call piton_object_new",
                ])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = f"object:{class_name}"
        elif op == "set_attr":
            owner, name, value = args
            self._load_operand(owner, "rcx")
            self.lines.append(f"    lea rdx, [{self._string(name)}]")
            self._load_operand(value, "r8")
            self.lines.append("    call piton_object_set")
        elif op == "get_attr":
            owner, name = args
            self._load_operand(owner, "rcx")
            self.lines.append(f"    lea rdx, [{self._string(name)}]")
            self.lines.append("    call piton_object_get")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "method_call":
            explicit_class, method_name, owner, raw_values = args
            owner_type = self.types.get(owner, "")
            class_name = explicit_class or (owner_type.split(":", 1)[1] if owner_type.startswith("object:") else None)
            if not class_name:
                raise NativeBuildError("native method receiver class is not statically known")
            # Resolve method through inheritance chain
            resolved_class = class_name
            class_parents = getattr(self.mir_module, 'class_parents', {})
            while resolved_class and method_name not in self.mir_module_classes.get(resolved_class, set()):
                resolved_class = class_parents.get(resolved_class)
            if not resolved_class:
                resolved_class = class_name  # fallback to original
            values = [owner, *raw_values]
            if len(values) > 4:
                raise NativeBuildError("native method calls support at most four total arguments")
            for register, value in zip(("rcx", "rdx", "r8", "r9"), values):
                self._load_operand(value, register)
            self.lines.append(f"    call {resolved_class}__{method_name}")
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "int"
        elif op == "math_sqrt":
            self._load_float_operand(args[0], "xmm0")
            self.lines.extend(["    sqrtsd xmm0, xmm0", "    movq rax, xmm0"])
            self.lines.append(f"    mov {self._address(result)}, rax")
            self.types[result] = "float"
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
        elif op == "raise_typed":
            exception_type, payload, handler_label = args
            self.lines.append(f"    lea rcx, [{self._string(exception_type)}]")
            if payload is None:
                self.lines.append("    xor edx, edx")
            elif self.types.get(payload) in {"str", "bigint"}:
                self._load_operand(payload, "rdx")
            else:
                raise NativeBuildError("native exception payload must be a string")
            self.lines.append("    call piton_raise")
            if handler_label:
                # Check if an exception was caught and branch to handler
                self.lines.append("    call piton_catch_flag")
                self.lines.append("    test rax, rax")
                target = labels.get(handler_label, handler_label)
                self.lines.append(f"    jne {target}")
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
        elif op == "branch":
            condition, yes, no = args
            self._emit_truth_test(condition)
            self.lines.extend([f"    jne {labels[yes]}", f"    jmp {labels[no]}"])
        elif op == "jump":
            self.lines.append(f"    jmp {labels[args[0]]}")
        elif op == "call":
            function_operand, call_args = args
            function_name = self.aliases.get(function_operand, function_operand)
            values = list(call_args)
            if function_name in {"imprimir", "print"}:
                if not values:
                    self.lines.append("    lea rcx, [fmt_str]")
                    self.lines.append("    xor edx, edx")
                else:
                    value = values[0]
                    value_type = self.types.get(value, "int")
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
                    else:
                        self._load_operand(value, "rdx")
                        fmt = "fmt_str" if value_type == "str" else "fmt_int"
                    self.lines.append(f"    lea rcx, [{fmt}]")
                self.lines.extend(["    call printf", "    xor eax, eax"])
            elif function_name in {"longitud", "len"}:
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
            else:
                values = self._complete_call_args(function_name, list(call_args))
                if len(values) > 4:
                    raise NativeBuildError("native calls with more than four arguments are not supported yet")
                for register, value in zip(("rcx", "rdx", "r8", "r9"), values):
                    self._load_operand(value, register)
                if function_name in self.function_names:
                    self.lines.append(f"    call {function_name}")
                else:
                    self._load_operand(function_operand, "rax")
                    self.lines.append("    call rax")
            if result:
                self.lines.append(f"    mov {self._address(result)}, rax")
        elif op == "return":
            if self.types.get(args[0]) in {"list", "tuple", "dict", "set"}:
                raise NativeBuildError("returning native collections is not supported yet")
            self._load_operand(args[0], "rax")
            self.lines.append(f"    mov {self._address('@scratch0')}, rax")
            self._emit_cleanup()
            self.lines.extend([f"    mov rax, {self._address('@scratch0')}", "    leave", "    ret"])
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
            if mod_name and mod_name not in {"asyncio", "math"}:
                raise NativeBuildError(f"native from-import requires multi-file compilation: {mod_name}")
            for alias in getattr(statement, "names", []):
                if mod_name in {"asyncio", "math"}:
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
    if not candidate.is_file():
        raise NativeBuildError(f"native module not found: {dotted}")
    return candidate


def _scan_native_modules(entry: Path) -> tuple[dict[str, Any], dict[tuple[str, str], bool]]:
    """Cargan los módulos nativos (hermano o paquete) referenciados por el entry."""
    root = entry.parent
    hir = lower_cst_to_hir(parse(entry.read_text(encoding="utf-8-sig")))
    modules: dict[str, Any] = {}
    from_imports: dict[tuple[str, str], bool] = {}
    for statement in hir.body:
        if statement.kind.name == "IMPORT":
            for alias in statement.names:
                if alias.name in {"asyncio", "math"}:
                    continue
                if "." in alias.name:
                    raise NativeBuildError(
                        "'importar pkg.sub' (dotted package import) is not supported yet; "
                        "use 'desde pkg.sub importar fn'"
                    )
                module_path = resolve_native_module(root, alias.name)
                modules[alias.name] = lower_cst_to_hir(parse(module_path.read_text(encoding="utf-8-sig")))
        elif statement.kind.name == "IMPORT_FROM":
            mod_name = getattr(statement, "module", None)
            if not mod_name or mod_name in {"asyncio", "math"}:
                continue
            module_path = resolve_native_module(root, mod_name)
            modules[mod_name] = lower_cst_to_hir(parse(module_path.read_text(encoding="utf-8-sig")))
            for alias in statement.names:
                from_imports[(mod_name, alias.asname or alias.name)] = True
    return modules, from_imports


def compile_native_files(entry: str | Path, output: str | Path) -> Path:
    entry_path = Path(entry).resolve()
    hir = lower_cst_to_hir(parse(entry_path.read_text(encoding="utf-8-sig")))
    modules, from_imports = _scan_native_modules(entry_path)
    try:
        mir = lower_hir_to_mir(hir, modules, from_imports=from_imports)
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
