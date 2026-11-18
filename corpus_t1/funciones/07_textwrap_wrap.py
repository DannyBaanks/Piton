import textwrap
def partir(texto, ancho):
    return textwrap.wrap(texto, ancho)
print(partir("hola mundo", 5))
