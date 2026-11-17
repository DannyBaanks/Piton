import textwrap
def limpiar(texto):
    return textwrap.dedent(texto)
print(limpiar("    hola\n    mundo"))