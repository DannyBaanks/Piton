import textwrap
def recortar(texto, ancho, marca):
    return textwrap.shorten(texto, ancho, marca)
print(recortar("hola mundo entero", 5, "..."))
