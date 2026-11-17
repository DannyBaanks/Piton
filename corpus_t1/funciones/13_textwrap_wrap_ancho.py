import textwrap
def partir(texto, ancho):
    return textwrap.wrap(texto, ancho)
print(partir("aaaa bb cccc", 3))
