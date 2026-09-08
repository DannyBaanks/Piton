"""Modelo de Value y contrato ABI para el backend nativo de Pitón."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


WORD_BITS = 64
TAG_BITS = 3
PAYLOAD_BITS = WORD_BITS - TAG_BITS
PAYLOAD_MASK = (1 << PAYLOAD_BITS) - 1
TAG_SHIFT = PAYLOAD_BITS
WORD_MASK = (1 << WORD_BITS) - 1


class ValueKind(Enum):
    NONE = 0
    BOOL = 1
    INT = 2
    FLOAT_HANDLE = 3
    OBJECT_HANDLE = 4


@dataclass(frozen=True, slots=True)
class Value:
    kind: ValueKind
    payload: int = 0

    def encode(self) -> int:
        if self.kind is ValueKind.INT:
            minimum = -(1 << (PAYLOAD_BITS - 1))
            maximum = (1 << (PAYLOAD_BITS - 1)) - 1
            if not minimum <= self.payload <= maximum:
                raise OverflowError("integer does not fit in immediate Value")
            payload = self.payload & PAYLOAD_MASK
        elif self.kind is ValueKind.BOOL:
            payload = 1 if self.payload else 0
        elif self.kind is ValueKind.NONE:
            payload = 0
        elif self.payload < 0 or self.payload > PAYLOAD_MASK:
            raise ValueError("handle must fit in Value payload")
        else:
            payload = self.payload
        return ((self.kind.value << TAG_SHIFT) | payload) & WORD_MASK

    @classmethod
    def decode(cls, word: int) -> "Value":
        word &= WORD_MASK
        tag = (word >> TAG_SHIFT) & ((1 << TAG_BITS) - 1)
        try:
            kind = ValueKind(tag)
        except ValueError as error:
            raise ValueError(f"unknown Value tag: {tag}") from error
        payload = word & PAYLOAD_MASK
        if kind is ValueKind.INT and payload & (1 << (PAYLOAD_BITS - 1)):
            payload -= 1 << PAYLOAD_BITS
        if kind is ValueKind.BOOL:
            payload = int(bool(payload))
        return cls(kind, payload)


@dataclass(frozen=True, slots=True)
class ABIConfig:
    name: str = "windows-x64"
    word_size: int = 8
    stack_alignment: int = 16
    shadow_space: int = 32
    argument_registers: tuple[str, ...] = ("rcx", "rdx", "r8", "r9")
    return_register: str = "rax"
    callee_saved: tuple[str, ...] = ("rbx", "rbp", "rsi", "rdi", "r12", "r13", "r14", "r15")

    def validate(self) -> None:
        if self.word_size != 8:
            raise ValueError("Windows x64 ABI requires 8-byte words")
        if self.stack_alignment != 16:
            raise ValueError("stack alignment must be 16 bytes")
        if self.shadow_space != 32:
            raise ValueError("Windows x64 requires 32 bytes of shadow space")
        if len(set(self.argument_registers)) != len(self.argument_registers):
            raise ValueError("argument registers must be unique")

    def plan_call(self, argument_count: int) -> "CallPlan":
        self.validate()
        register_count = min(argument_count, len(self.argument_registers))
        stack_count = max(0, argument_count - register_count)
        stack_bytes = stack_count * self.word_size
        frame_bytes = self.shadow_space + stack_bytes
        if frame_bytes % self.stack_alignment:
            frame_bytes += self.stack_alignment - frame_bytes % self.stack_alignment
        return CallPlan(
            registers=self.argument_registers[:register_count],
            stack_offsets=tuple(self.shadow_space + i * self.word_size for i in range(stack_count)),
            frame_bytes=frame_bytes,
        )


@dataclass(frozen=True, slots=True)
class CallPlan:
    registers: tuple[str, ...]
    stack_offsets: tuple[int, ...]
    frame_bytes: int

    def validate(self, entry_rsp_mod16: int = 8) -> None:
        if entry_rsp_mod16 % 16 != 8:
            raise ValueError("Windows x64 function entry RSP must be 8 mod 16")
        if self.frame_bytes % 16:
            raise ValueError("call frame must preserve 16-byte alignment")


WINDOWS_X64 = ABIConfig()


def freeze_value_model() -> dict[str, Any]:
    WINDOWS_X64.validate()
    return {
        "word_bits": WORD_BITS,
        "tag_bits": TAG_BITS,
        "payload_bits": PAYLOAD_BITS,
        "tags": {kind.name: kind.value for kind in ValueKind},
        "immediates": ["NONE", "BOOL", "INT"],
        "handles": ["FLOAT_HANDLE", "OBJECT_HANDLE"],
        "abi": {
            "name": WINDOWS_X64.name,
            "argument_registers": WINDOWS_X64.argument_registers,
            "return_register": WINDOWS_X64.return_register,
            "shadow_space": WINDOWS_X64.shadow_space,
            "stack_alignment": WINDOWS_X64.stack_alignment,
        },
    }
