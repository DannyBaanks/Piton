# Rescate de worktrees PITON — 2026-11-21

Rama de archivo creada al unificar la ola 23 en main (4034d2e).
Contiene solo los cambios **tracked** únicos detectados en worktrees de agentes
que no estaban en main, más tests sueltos relevantes. El scratch (debug_*.py,
fix_*.py, .w21scratch, runs de autopiton) y el worktree con el árbol borrado
(piton_wt_w23_shelve_super_len_c_error) se descartaron a propósito.

| worktree | HEAD | archivos | aplica a main |
|---|---|---|---|
| piton_wt_w16_exc_imports | f54a090 | piton/linux_x86.py piton/mir.py | no (conflicto con main) |
| piton_wt_w16_re_datetime | f54a090 | piton/linux_x86.py piton/mir.py piton/stdlib_piton/datetime.piton piton/stdlib_piton/re.piton | no (conflicto con main) |
| piton_wt_w17_cgen_errors_b | 3110c14 | piton/linux_x86.py | no (conflicto con main) |
| piton_wt_w17_linker_errors | 3110c14 | piton/linux_x86.py tests/test_w17_linker_errors.py | no (conflicto con main) |
| piton_wt_w17_filter_builtin | 3110c14 | autopiton/cases/index.json | sí |
| piton_wt_w18_timeit_timer | d0f3c7d | piton/linux_x86.py piton/mir.py tests/test_w18_timeit_timer.py | sí |
| piton_wt_w21_super_namedtuple_base | 3ff3cac | piton/linux_x86.py | no (conflicto con main) |
| piton_wt_w23_super_dynamic_base_mro | fbc5077 | piton/namedtuple_bases.py | sí |
| piton_wt_w6_re | 3f36c4b | piton/stdlib_piton/re.piton piton/stdlib_piton/retest.piton | no (conflicto con main) |
| piton_wt_w6_timeit | f0442af | piton/stdlib_piton/timeit.piton | no (conflicto con main) |
| piton_wt_w4_decimal_builtins | 31fb876 | test_decimal_import.piton | no (conflicto con main) |

Los parches se generaron con `git -C <worktree> diff -- <archivos>`.
Los archivos untracked preservados están bajo `files/<worktree>/...`.
Nada de esto se aplicó a main: es un archivo de rescate.
