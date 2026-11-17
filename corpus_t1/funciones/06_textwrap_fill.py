import textwrap
def ajustar(texto, ancho):
    return textwrap.fill(texto, ancho)
print(ajustar("hola mundo", 5))
