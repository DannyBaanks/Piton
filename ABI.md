# Pitón ABI y Value

Estado: diseño congelado para la Fase 4, primer backend `windows-x64`.

## Value

`Value` ocupa una palabra de 64 bits. Los 3 bits superiores son el tag y los
61 bits inferiores son el payload.

- `NONE`, `BOOL` e `INT` son inmediatos.
- `FLOAT_HANDLE` y `OBJECT_HANDLE` son handles al runtime.
- Los enteros inmediatos usan complemento a dos de 61 bits.

## Windows x64

- Argumentos: `rcx`, `rdx`, `r8`, `r9`.
- Retorno: `rax`.
- Shadow space obligatorio: 32 bytes.
- Alineación de stack: 16 bytes.
- Registros callee-saved: `rbx`, `rbp`, `rsi`, `rdi`, `r12`-`r15`.

El módulo `piton.abi` valida el contrato y produce planes de llamada. Todavía
no emite ensamblador ni afirma que exista un ejecutable nativo.
