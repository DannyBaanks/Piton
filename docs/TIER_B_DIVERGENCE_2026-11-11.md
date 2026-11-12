# Divergencia por ejemplo en tier B (2026-11-11)

- git: `6165e06` (rama `feat/build-unlock-stdlib`)
- instrumento: `tools/tier_b_divergence_map.py` (diagnostico; la METRICA sigue
  siendo `tools/parity_corpus_v4.py run --tier b`: 8/11 EQUIV, 72.7%)
- oracle: CPython 3.12.3, backend medido: linux_x86

`parity_corpus_v4.py` dice QUE script diverge, no POR QUE: un script es
todo-o-nada. Este mapa reparte cada script en sus ejemplos del tutorial y
compara uno a uno con la misma regla del corpus (una excepcion en los dos
lados es EXC == EXC y cuenta como igual).

Estado de los 3 scripts DIVERGENT, con lo que bloquea cada ejemplo:

| script | ejemplos divergentes | naturaleza del bloqueo |
|---|---|---|
| `inputoutput` | 3 de 68 | 1 feature de introspeccion (`vars()`) + 1 feature de E/S binaria (`rb+`) |
| `stdlib` | 25 de 52 | 14 modulos ausentes + 1 ejemplo estructuralmente inalcanzable (`help(os)`) |
| `stdlib2` | 20 de 60 | 10 modulos ausentes + `decimal` + 1 bug real (`del` de nombre no definido) |

Ninguno de los tres se cierra solo con "arreglar el backend": lo que falta es
superficie de stdlib y dos features de runtime. El detalle por ejemplo esta
abajo.

## Lo que NO es cerrable por implementacion (y por que)

`help(os)` (stdlib, ejemplo 7) imprime la pagina de ayuda de CPython byte a
byte. Un lenguaje nativo tendria su propio texto de ayuda, y entonces la
salida no seria identica byte a byte: cerrarlo exigiria COPIAR el texto del
oracle, y eso no es una afirmacion de paridad honesta. El ejemplo queda
fuera de alcance por construccion, no por descuido.

`decimal` (stdlib2, 5 ejemplos) exige aritmetica decimal de precision
arbitraria con contexto, precision y redondeo propio. Los enteros nativos de
PITON son i64 (con bigint aparte) y no hay un tipo decimal: es un proyecto
propio, no un modulo.

## Bugs reales encontrados por el camino (ya arreglados)

- `str.format()` con campos con nombre pisaba los posicionales:
  `'{0} {1} {o}'.format('Bill','Manfred',o='Georg')` imprimia
  `Georg Manfred Georg` — el dato correcto con el nombre equivocado.
  Arreglado en `6165e06` (FMT_NAMED_SLOT_V1).
- `s.format(**mapping)` caia en el call dinamico generico y levantaba
  `TypeError: 'str' object has no attribute 'format'`. Arreglado en
  `6165e06` (FMT_NS_V1).
- `f.closed` levantaba `AttributeError` (el handle no es un objeto con
  atributos). Arreglado en `6165e06` (FILE_ATTR_V1).

Con esos tres arreglos, `inputoutput` paso de 6 ejemplos divergentes a 3.

## Bugs reales que quedan (no arreglados aqui)

- `del <nombre no definido>` deberia levantar `NameError`; PITON no lo hace
  (stdlib2, ejemplo 32). NO es un arreglo de una linea: el backend no tiene
  la nocion runtime de "nombre ligado" — los globales del modulo son
  variables C locales inicializadas a 0, y un `int 0` es indistinguible de
  "nunca asignado". El `del` se modela en build (borra el nombre del mapa de
  tipos) y no emite codigo. Arreglarlo de verdad pide un flag de binding por
  global (o un centinela de "no ligado" que no colisione con el int 0).
- `f.write(bytes)` sobre un fichero abierto en `rb+` falla cerrado con
  `io.UnsupportedOperation: not writable` (inputoutput, ejemplo 61). El
  modelo de ficheros actual carga el fichero entero en memoria al abrirlo y
  no tiene escritura binaria ni buffer de escritura. Arreglar solo el flag
  de "escribible" convertiria ese fallo ruidoso en corrupcion silenciosa
  (la lectura devolveria el buffer viejo, no lo escrito), asi que queda
  como esta: el subconjunto binario de E/S de fichero es otro proyecto.

# Mapa de divergencia por ejemplo (tier B)

Regla: una excepcion en los dos lados es EXC == EXC (equivale), igual
que en el corpus. Solo diverge cuando un lado imprime y el otro no, o
cuando las salidas difieren.

## inputoutput — 3 de 68 ejemplos divergentes

| # | codigo | CPython | PITON |
|---|---|---|---|
| 39 | `table = {k: str(v) for k, v in vars().items()}` | (vacio) | EXC |
| 41 | `print(message.format(**table))` | __name__: __main__; __doc__: None; __package__: None; __loader__: <class '_frozen_importli | Sjoerd: 4127; Jack: 4098; Dcab: 8637678; |
| 61 | `f.write(b'0123456789abcdef')` | (vacio) | EXC |

## stdlib — 25 de 52 ejemplos divergentes

| # | codigo | CPython | PITON |
|---|---|---|---|
| 2 | `os.getcwd()      # Return the current working directory` | (vacio) | EXC |
| 4 | `os.system('mkdir today')   # Run the command mkdir in the system shell` | (vacio) | EXC |
| 6 | `dir(os)` | (vacio) | EXC |
| 7 | `help(os)` | Help on module os: /  / NAME /     os - OS routines for NT or Posix depending on what syst | EXC |
| 11 | `import glob` | (vacio) | EXC |
| 12 | `glob.glob('*.py')` | (vacio) | EXC |
| 15 | `re.findall(r'\bf[a-z]*', 'which foot or hand fell fastest')` | (vacio) | EXC |
| 16 | `re.sub(r'(\b[a-z]+) \1', r'\1', 'cat in the the hat')` | (vacio) | EXC |
| 22 | `random.choice(['apple', 'pear', 'banana'])` | (vacio) | EXC |
| 23 | `random.sample(range(100), 10)   # sampling without replacement` | (vacio) | EXC |
| 24 | `random.random()    # random float` | (vacio) | EXC |
| 25 | `random.randrange(6)    # random integer chosen from range(6)` | (vacio) | EXC |
| 26 | `import statistics` | (vacio) | EXC |
| 28 | `statistics.mean(data)` | (vacio) | EXC |
| 29 | `statistics.median(data)` | (vacio) | EXC |
| 30 | `statistics.variance(data)` | (vacio) | EXC |
| 31 | `from urllib.request import urlopen` | (vacio) | EXC |
| 32 | `import smtplib` | (vacio) | EXC |
| 37 | `now = date.today()` | (vacio) | EXC |
| 39 | `now.strftime("%m-%d-%y. %d %b %Y is a %A on the %d day of %B.")` | (vacio) | EXC |
| 40 | `birthday = date(1964, 7, 31)` | (vacio) | EXC |
| 42 | `age.days` | (vacio) | EXC |
| 49 | `zlib.crc32(s)` | (vacio) | EXC |
| 51 | `Timer('t=a; a=b; b=t', 'a=1; b=2').timeit()` | (vacio) | EXC |
| 52 | `Timer('a,b = b,a', 'a=1; b=2').timeit()` | (vacio) | EXC |

## stdlib2 — 20 de 60 ejemplos divergentes

| # | codigo | CPython | PITON |
|---|---|---|---|
| 2 | `reprlib.repr(set('supercalifragilisticexpialidocious'))` | (vacio) | EXC |
| 5 | `pprint.pprint(t, width=30)` | [[[['black', 'cyan'], /    'white', /    ['green', 'red']], /   [['magenta', 'yellow'], /  | EXC |
| 8 | `print(textwrap.fill(doc, width=40))` | The wrap() method is just like fill() / except that it returns     a list of / strings ins | EXC |
| 11 | `conv = locale.localeconv()          # get a mapping of conventions` | (vacio) | EXC |
| 13 | `locale.format_string("%d", x, grouping=True)` | (vacio) | EXC |
| 14 | `locale.format_string("%s%.*f", (conv['currency_symbol'], | conv['frac_` | (vacio) | EXC |
| 17 | `t.substitute(village='Nottingham', cause='the ditch fund')` | (vacio) | EXC |
| 21 | `t.safe_substitute(d)` | (vacio) | EXC |
| 22 | `import time, os.path` | (vacio) | EXC |
| 26 | `date = time.strftime('%d%b%y')` | (vacio) | EXC |
| 32 | `del a                       # remove the one reference` | EXC | (vacio) |
| 33 | `gc.collect()                # run garbage collection right away` | (vacio) | EXC |
| 49 | `heapify(data)                      # rearrange the list into heap orde` | (vacio) | EXC |
| 50 | `heappush(data, -5)                 # add a new entry` | (vacio) | EXC |
| 51 | `[heappop(data) for i in range(3)]  # fetch the three smallest entries` | (vacio) | EXC |
| 53 | `round(Decimal('0.70') * Decimal('1.05'), 2)` | (vacio) | EXC |
| 55 | `Decimal('1.00') % Decimal('.10')` | (vacio) | EXC |
| 57 | `sum([Decimal('0.1')]*10) == Decimal('1.0')` | (vacio) | EXC |
| 59 | `getcontext().prec = 36` | (vacio) | EXC |
| 60 | `Decimal(1) / Decimal(7)` | (vacio) | EXC |
