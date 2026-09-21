"""Optimizaciones MIR bootstrap, apagables y comparables."""
from __future__ import annotations

import operator
from dataclasses import replace
from typing import Any

from .mir import MIRBlock, MIRFunction, MIRInstruction, MIRModule


def _copy_function_shell(function: MIRFunction, blocks: list[MIRBlock] | None = None) -> MIRFunction:
    copied = MIRFunction(
        function.name,
        list(function.params),
        blocks if blocks is not None else [MIRBlock(b.label, list(b.instructions)) for b in function.blocks],
    )
    copied.defaults = list(function.defaults)
    copied.vararg = function.vararg
    copied.kwarg = function.kwarg
    copied.cell_vars = list(function.cell_vars)
    copied.self_class = function.self_class
    copied.frame_abi = function.frame_abi
    copied.is_generator = function.is_generator
    copied.is_coroutine = function.is_coroutine
    copied.is_async_generator = function.is_async_generator
    return copied


def optimize_mir(module: MIRModule, level: int = 0) -> MIRModule:
    if level not in (0, 1, 2):
        raise ValueError("optimization level must be 0, 1 or 2")
    if level == 0:
        result = MIRModule([_copy_function_shell(f) for f in module.functions])
        result.classes = dict(module.classes)
        result.class_parents = dict(module.class_parents)
        return result
    functions = []
    for function in module.functions:
        blocks = []
        for block in function.blocks:
            constants: dict[str, Any] = {}
            instructions = []
            for instruction in block.instructions:
                if instruction.op == "const" and instruction.result:
                    constants[instruction.result] = instruction.args[0]
                elif instruction.op == "binary" and instruction.result:
                    op, left, right = instruction.args
                    if left in constants and right in constants:
                        operation = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}.get(op)
                        if operation:
                            value = operation(constants[left], constants[right])
                            constants[instruction.result] = value
                            # ME: rebuild with replace so the effect lattice
                            # fields (effects/token/token_prev) are preserved.
                            instructions.append(replace(instruction, op="const", args=(value,)))
                            continue
                instructions.append(instruction)
            blocks.append(MIRBlock(block.label, instructions))
        functions.append(_copy_function_shell(function, blocks))
    result = MIRModule(functions)
    result.classes = dict(module.classes)
    result.class_parents = dict(module.class_parents)
    return result
