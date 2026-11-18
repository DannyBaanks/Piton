import textwrap
def ajustar(texto, ancho):
    return textwrap.fill(texto, ancho)
print(ajustar("el zorro marron rapido salta sobre el perro perezoso", 12))
