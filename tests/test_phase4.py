from __future__ import annotations

import unittest

from piton.abi import (
    ABIConfig,
    Value,
    ValueKind,
    WINDOWS_X64,
    freeze_value_model,
)


class Phase4Gates(unittest.TestCase):
    def test_value_model_is_frozen(self):
        model = freeze_value_model()
        self.assertEqual(model["word_bits"], 64)
        self.assertEqual(model["tag_bits"], 3)
        self.assertEqual(model["immediates"], ["NONE", "BOOL", "INT"])
        for value in (
            Value(ValueKind.NONE),
            Value(ValueKind.BOOL, 1),
            Value(ValueKind.BOOL, 0),
            Value(ValueKind.INT, 0),
            Value(ValueKind.INT, -(1 << 60)),
            Value(ValueKind.INT, (1 << 60) - 1),
            Value(ValueKind.OBJECT_HANDLE, 42),
        ):
            self.assertEqual(Value.decode(value.encode()), value)

    def test_windows_x64_abi_gate(self):
        WINDOWS_X64.validate()
        self.assertEqual(WINDOWS_X64.argument_registers, ("rcx", "rdx", "r8", "r9"))
        self.assertEqual(WINDOWS_X64.return_register, "rax")
        self.assertEqual(WINDOWS_X64.shadow_space, 32)
        self.assertEqual(WINDOWS_X64.stack_alignment, 16)

    def test_stack_alignment_gate(self):
        for count in range(0, 10):
            plan = WINDOWS_X64.plan_call(count)
            plan.validate()
            self.assertEqual(plan.frame_bytes % 16, 0)
            self.assertEqual(len(plan.registers), min(count, 4))
            self.assertEqual(len(plan.stack_offsets), max(0, count - 4))

    def test_call_convention_shape(self):
        plan = ABIConfig().plan_call(6)
        self.assertEqual(plan.registers, ("rcx", "rdx", "r8", "r9"))
        self.assertEqual(plan.stack_offsets, (32, 40))
        self.assertEqual(plan.frame_bytes, 48)


if __name__ == "__main__":
    unittest.main()
