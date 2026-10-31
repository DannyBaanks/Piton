# Catálogo de acceso a valor de atributo de módulo - Parity Corpus v4

Generado a partir de parity_corpus_v4_report con depuración habilitada.
Baseline MIR: 30.5%

## Casos detectados: native module attribute value access is not supported yet

| Archivo | mod | attrs |
|---------|-----|-------|
| _pylong.py | decimal | ['Decimal'] |
| _strptime.py | time | ['tzname'] |
| cProfile.py | _pyprofile | ['run','__doc__'] |
| crypt.py | errno | ['EINVAL'] |
| filecmp.py | os | ['path','normcase'] |
| ftplib.py | socket | ['AF_INET'] |
| inspect.py | tokenize | ['NEWLINE'] |
| io.py | _io | ['_IOBase','__doc__'] |
| locale.py | _collections_abc | ['Mapping'] |
| mailbox.py | email | ['message','Message'] |
| ntpath.py | genericpath | ['_splitext','__doc__'] |
| pkgutil.py | importlib | ['util','MAGIC_NUMBER'] |
| poplib.py | socket | ['SHUT_RDWR'] |
| pprint.py | _sys | ['stdout'] |
| shutil.py | errno | ['EINVAL'] |
| site.py | os | ['path','join'] |
| symtable.py | _symtable | ['TYPE_FUNCTION'] |
| sysconfig.py | os | ['path','join'] |

## Clasificación sugerida

* Clases de módulo: decimal.Decimal, tokenize.NEWLINE, _io._IOBase, _collections_abc.Mapping, email.message.Message
* Submódulos: os.path, email.message, importlib.util
* Constantes/flags: errno.EINVAL, socket.AF_INET, socket.SHUT_RDWR, time.tzname
* Atributos internos: _pyprofile.run, _symtable.TYPE_FUNCTION, _sys.stdout

## Estado
* Reproducibilidad: verified=True, git_clean=True
* Próximos pasos: implementar stubs/resolución para los 5 casos más frecuentes sin placeholders silenciosos.
