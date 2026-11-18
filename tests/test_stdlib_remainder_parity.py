#!/usr/bin/env python3
"""
Paridad de trabajo real para el frente F6 (resto de stdlib):
os.system, glob.glob, re.findall/re.sub, import smtplib,
from urllib.request import urlopen.

Nota de alcance: el backend nativo de PITON no expone primitivas de SO
(system/opendir/regex/socket) y esos modulos no se inlinean en el embudo
nativo (os__system no definido). Estas pruebas ejecutan fuente PITON a traves
de ``exec_piton`` (backend Python/host), donde ``importar os``, ``importar
glob``, etc. resuelven contra el stdlib real de CPython y por tanto hacen
trabajo REAL (crean directorios, listan directorios, aplican regex de verdad).
No son stubs que devuelven None/0/"".
"""

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from piton.runtime import exec_piton  # noqa: E402


def _run(src):
    return exec_piton(src)


def test_os_system_creates_directory_for_real():
    """os.system('mkdir ...') ejecuta el comando de verdad y devuelve su codigo de salida."""
    import os as _os
    ns = _run("importar os\nr = os .system(\"mkdir test_f6_system\")\n")
    assert _os.path.isdir("test_f6_system"), "el directorio deberia haberse creado de verdad"
    assert ns.get("r") == 0, f"codigo de salida esperado 0, obtenido {ns.get('r')!r}"
    _os.rmdir("test_f6_system")


def test_glob_glob_lista_python_de_verdad():
    """glob.glob('*.py') lista de verdad los .py del directorio de trabajo."""
    import glob as _glob
    import os as _os
    repo = _os.path.dirname(_os.path.dirname(__file__))
    ns = _run("importar glob\ng = glob .glob(\"*.py\")\n")
    piton = sorted(ns.get("g", []))
    cpy = sorted(_glob.glob(_os.path.join(repo, "*.py")))
    cpy = [_os.path.basename(p) for p in cpy]
    assert piton == cpy, f"piton={piton} cpy={cpy}"


def test_re_findall_aplica_regex_de_verdad():
    """re.findall con \\bf[a-z]* aplica el patron de verdad."""
    ns = _run('importar re\nr = re .findall(r"\\bf[a-z]*", "which foot or hand fell fastest")\n')
    assert ns.get("r") == ["foot", "fell", "fastest"], ns.get("r")


def test_re_sub_retroceso_de_verdad():
    """re.sub con grupo de retroceso \\1 aplica el patron de verdad."""
    ns = _run('importar re\nr = re .sub(r"(\\b[a-z]+) \\1", r"\\1", "cat in the the hat")\n')
    assert ns.get("r") == "cat in the hat", repr(ns.get("r"))


def test_import_smtplib_resuelve():
    """import smtplib resuelve sin lanzar (la funcion SMTP no se usa aqui)."""
    _run("importar smtplib\n")


def test_import_urlopen_resuelve():
    """from urllib.request import urlopen resuelve sin lanzar (la funcion no se usa aqui)."""
    _run("desde urllib.request importar urlopen\n")
