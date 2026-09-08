"""Optimizaciones MIR bootstrap, apagables y comparables."""
from __future__ import annotations

import operator
from dataclasses import replace
from typing import Any

from .mir import MIRBlock, MIRFunction, MIRInstruction, MIRModule


def optimize_mir(module: MIRModule, level: int = 0) -> MIRModule:
    if level not in (0, 1, 2):
        raise ValueError("optimization level must be 0, 1 or 2")
    if level == 0:
        return MIRModule([MIRFunction(f.name, list(f.params), [MIRBlock(b.label, list(b.instructions)) for b in f.blocks]) for f in module.functions])
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
                            instructions.append(MIRInstruction("const", (value,), instruction.result))
                            continue
                instructions.append(instruction)
            blocks.append(MIRBlock(block.label, instructions))
        functions.append(MIRFunction(function.name, list(function.params), blocks))
    return MIRModule(functions)
