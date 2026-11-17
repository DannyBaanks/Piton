import functools
def etiqueta(prefijo, texto):
    return prefijo + texto
con_hola = functools.partial(etiqueta, "hola ")
print(con_hola("mundo"))
