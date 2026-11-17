import textwrap
def recortar(texto, ancho):
    return textwrap.shorten(texto, ancho)
print(recortar("hola mundo", 5))
