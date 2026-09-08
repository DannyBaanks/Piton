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
from .x86 import NativeBuildError


def _name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"piton_{cleaned}" if cleaned and cleaned[0].isdigit() else cleaned


class LinuxCEmitter:
    def __init__(self) -> None:
        self.function_names: set[str] = set()

    def emit(self, module: MIRModule) -> str:
        self.function_names = {function.name for function in module.functions}
        lines = [
            "typedef long i64; typedef unsigned long usize;",
            "static long piton_write(long fd,const void*buf,usize n){long r;__asm__ volatile(\"syscall\":\"=a\"(r):\"a\"(1L),\"D\"(fd),\"S\"(buf),\"d\"(n):\"rcx\",\"r11\",\"memory\");return r;}",
            "__attribute__((noreturn)) static void piton_exit(long code){__asm__ volatile(\"syscall\"::\"a\"(60L),\"D\"(code):\"rcx\",\"r11\",\"memory\");__builtin_unreachable();}",
            "static usize piton_strlen(const char*s){usize n=0;while(s[n])++n;return n;}",
            "static int piton_strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return (unsigned char)*a-(unsigned char)*b;}",
            "static void piton_print_str(const char*s){piton_write(1,s,piton_strlen(s));piton_write(1,\"\\n\",1);}",
            "static void piton_print_int(i64 number){char b[32];usize i=sizeof(b);unsigned long value;if(number<0){piton_write(1,\"-\",1);value=0-(unsigned long)number;}else value=(unsigned long)number;do{b[--i]=(char)(\'0\'+value%10);value/=10;}while(value);piton_write(1,b+i,sizeof(b)-i);piton_write(1,\"\\n\",1);}",
            "static i64 piton_floor_div(i64 a,i64 b){i64 q=a/b,r=a%b;if(r&&((r<0)!=(b<0)))--q;return q;}",
            "static i64 piton_mod(i64 a,i64 b){i64 r=a%b;if(r&&((r<0)!=(b<0)))r+=b;return r;}",
        ]
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
        for block in function.blocks:
            lines.append(f"{_name(function.name + '_' + block.label)}:")
            for instruction in block.instructions:
                lines.extend(self._emit_instruction(instruction, function, aliases, types))
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
        aliases: dict[str, str], types: dict[str, str],
    ) -> list[str]:
        op, args, result = instruction.op, instruction.args, instruction.result
        out: list[str] = []
        if op == "const":
            value = args[0]
            if isinstance(value, str):
                out.append(f"    {_name(result)}=(long){json.dumps(value)};")
                types[result] = "str"
            elif isinstance(value, (int, bool)) or value is None:
                out.append(f"    {_name(result)}=(long)({self._value(value)});")
                types[result] = "bool" if isinstance(value, bool) else "none" if value is None else "int"
            else:
                raise NativeBuildError("Linux scalar backend does not support float constants yet")
        elif op == "load":
            source = args[0]
            aliases[result] = source
            types[result] = types.get(source, "int")
            if source not in self.function_names and source not in {"imprimir", "print"}:
                out.append(f"    {_name(result)}={_name(source)};")
        elif op == "store":
            out.append(f"    {_name(args[0])}={self._value(args[1])};")
            types[args[0]] = types.get(args[1], "int")
        elif op == "unary":
            operator = {"no": "!", "not": "!"}.get(args[0], args[0])
            if operator not in {"+", "-", "~", "!"}:
                raise NativeBuildError(f"Linux unary operator not supported: {operator}")
            out.append(f"    {_name(result)}={operator}{self._value(args[1])};")
            types[result] = "bool" if operator == "!" else "int"
        elif op == "binary":
            operator, left, right = args
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
            values = ",".join(self._value(value) for value in args[1])
            if function_name in {"imprimir", "print"}:
                if not args[1]:
                    out.append('    piton_write(1,"\\n",1);')
                elif types.get(args[1][0]) == "str":
                    out.append(f'    piton_print_str((char*){self._value(args[1][0])});')
                elif types.get(args[1][0]) == "bool":
                    out.append(f'    piton_print_str({self._value(args[1][0])}?"True":"False");')
                elif types.get(args[1][0]) == "none":
                    out.append('    piton_print_str("None");')
                else:
                    out.append(f'    piton_print_int((long){self._value(args[1][0])});')
                out.append(f"    {_name(result)}=0;")
            elif function_name in self.function_names:
                out.append(f"    {_name(result)}={_name(function_name)}({values});")
                types[result] = "int"
            else:
                raise NativeBuildError(f"Linux call target not supported: {function_name}")
        elif op == "return":
            out.append(f"    return {self._value(args[0])};")
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
