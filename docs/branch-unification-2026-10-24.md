# Unificacion de ramas PITON en main — 2026-10-23T23:28:40-06:00

## Resultado
- main unica rama de trabajo (local y remota).
- Excepcion conservada a proposito: preservacion/terminal-sesion-ajena-2026-10-24
  (trabajo sin commitear de otra sesion; NO se fusiona sin decision de su dueno).
- Respaldo total previo: <ISyCo>/piton-backup-2026-10-24.bundle
  (git bundle --all verificado; recupera TODAS las refs).
- Inventario de SHAs pre-borrado: /tmp/opencode/piton-ramas-inventario.txt
- Manifiesto de verificacion pre-borrado: /tmp/opencode/piton-manifiesto.txt

## Lo que se incorporo a main
- Merge de isycodepit-native-terminal (47 commits): tanda de paridad del corpus
  (82c8fa5). Suite: 1245 passed, 26 skipped, 133 subtests — identica a la previa.
- Rescate de parity-merge (e24e9d6): 22 corridas de validacion Windows
  (validation/windows/*.json/log/md) + tests/win64_toolchain.py. Su codigo quedo
  SUPERADO en main (origin/parity-merge estaba 18.599 lineas por detras en piton/);
  fusionarla habria traido conflictos masivos y regresiones.

## Ramas descartadas (recuperables via bundle + SHA)
  Motivo: contenido ya contenido en main o superado por los PRs #39-#42.
  - feat/float-repr (dd83c20) ancestor-directo
  - feat/int-overflow (bdc0931) ancestor-directo
  - feat/native-os-name-argv (83bee80) ancestor-directo
  - feat/native-sys-exit (74912ff) ancestor-directo
  - feat/native-trig-log (e50d8a0) ancestor-directo
  - feat/p0-parity (625d2f2) ancestor-directo
  - feat/p10-step-slices (c87a367) ancestor-directo
  - feat/p11-float-mod (dd99d18) ancestor-directo
  - feat/p12-float-pow (0909c6c) ancestor-directo
  - feat/p13-negbase-pow (5ae5ce3) ancestor-directo
  - feat/p14-pct-specs (ccad428) ancestor-directo
  - feat/p15-nonlocal (591efe5) ancestor-directo
  - feat/p16-none-return (1d6d087) ancestor-directo
  - feat/p17-coll-return (c1bee4c) ancestor-directo
  - feat/p18-float-fmt (4597851) ancestor-directo
  - feat/p18-unpack (4910aef) ancestor-directo
  - feat/p19-chain-cmp (0124873) ancestor-directo
  - feat/p1-division-in (427ef58) ancestor-directo
  - feat/p20-minmax-str (1a70631) ancestor-directo
  - feat/p21-fmt-spec (e6e93dc) ancestor-directo
  - feat/p22-boolop (d7fbeb4) ancestor-directo
  - feat/p23-str-methods (e3b3b78) ancestor-directo
  - feat/p24-assert-repeat (49ffe07) ancestor-directo
  - feat/p2-expressions (3caa807) ancestor-directo
  - feat/p36-paridad (f514515) ancestor-directo
  - feat/p37-introspection (7c2509d) ancestor-directo
  - feat/p38-native-methods (2d40a0c) ancestor-directo
  - feat/p39-context-managers (3bd1d58) ancestor-directo
  - feat/p3-str-methods (6280e0f) ancestor-directo
  - feat/p40-delegators (19d1f21) ancestor-directo
  - feat/p41-pattern-matching (6359265) ancestor-directo
  - feat/p42-advanced-decorators (ee25924) ancestor-directo
  - feat/p43-bytes-bytearray (349e2e7) ancestor-directo
  - feat/p4-iter-collections (a9018de) ancestor-directo
  - feat/p5-percent-format (5e98303) ancestor-directo
  - feat/p6-int-pow (2f0c77e) ancestor-directo
  - feat/p7-fstrings (ee2aefa) ancestor-directo
  - feat/p8-try-else (43d2038) ancestor-directo
  - feat/p9-global (310d990) ancestor-directo
  - fix/lexer-float-exponent (a60ede8) ancestor-directo
  - fix/linux-float-overflow (27c120e) ancestor-directo
  - fix/linux-native-backend-compilation (0a95342) ancestor-directo
  - fix/pe-nonfinite-float (71439a4) ancestor-directo
  - isycodepit-native-terminal (e22372f) ancestor-directo
  - main (82c8fa5) ancestor-directo
  - origin (82c8fa5) ancestor-directo
  - origin/feat/float-repr (dd83c20) ancestor-directo
  - origin/feat/int-overflow (bdc0931) ancestor-directo
  - origin/feat/native-os-name-argv (83bee80) ancestor-directo
  - origin/feat/native-sys-exit (74912ff) ancestor-directo
  - origin/feat/native-trig-log (e50d8a0) ancestor-directo
  - origin/feat/p0-parity (625d2f2) ancestor-directo
  - origin/feat/p10-step-slices (c87a367) ancestor-directo
  - origin/feat/p11-float-mod (dd99d18) ancestor-directo
  - origin/feat/p12-float-pow (0909c6c) ancestor-directo
  - origin/feat/p13-negbase-pow (5ae5ce3) ancestor-directo
  - origin/feat/p14-pct-specs (ccad428) ancestor-directo
  - origin/feat/p15-nonlocal (591efe5) ancestor-directo
  - origin/feat/p16-none-return (1d6d087) ancestor-directo
  - origin/feat/p17-coll-return (c1bee4c) ancestor-directo
  - origin/feat/p18-unpack (4910aef) ancestor-directo
  - origin/feat/p19-chain-cmp (0124873) ancestor-directo
  - origin/feat/p1-division-in (427ef58) ancestor-directo
  - origin/feat/p20-minmax-str (1a70631) ancestor-directo
  - origin/feat/p21-fmt-spec (e6e93dc) ancestor-directo
  - origin/feat/p22-boolop (d7fbeb4) ancestor-directo
  - origin/feat/p23-str-methods (e3b3b78) ancestor-directo
  - origin/feat/p24-assert-repeat (49ffe07) ancestor-directo
  - origin/feat/p2-expressions (3caa807) ancestor-directo
  - origin/feat/p36-paridad (f514515) ancestor-directo
  - origin/feat/p3-str-methods (6280e0f) ancestor-directo
  - origin/feat/p43-bytes-bytearray (349e2e7) ancestor-directo
  - origin/feat/p4-iter-collections (a9018de) ancestor-directo
  - origin/feat/p5-percent-format (5e98303) ancestor-directo
  - origin/feat/p6-int-pow (2f0c77e) ancestor-directo
  - origin/feat/p7-fstrings (ee2aefa) ancestor-directo
  - origin/feat/p8-try-else (43d2038) ancestor-directo
  - origin/feat/p9-global (310d990) ancestor-directo
  - origin/fix/lexer-float-exponent (a60ede8) ancestor-directo
  - origin/fix/linux-float-overflow (27c120e) ancestor-directo
  - origin/fix/linux-native-backend-compilation (0a95342) ancestor-directo
  - origin/fix/pe-nonfinite-float (71439a4) ancestor-directo
  - origin/isycodepit-native-terminal (e22372f) ancestor-directo
  - origin/main (82c8fa5) ancestor-directo

## Ramas eliminadas sin estar en main (obsoletas, evidencia preservada)
  - parity-merge d162972 / origin/parity-merge da907fc — codigo superado; evidencia rescatada
  - windows-lineage-b53 56ed37c — ancestor de parity-merge, superado

## Flujo de trabajo a partir de ahora
  Todo parte de main. Ramas cortas solo con fusion el mismo dia.
