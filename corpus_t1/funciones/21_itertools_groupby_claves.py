import itertools
def claves(datos):
    salida = []
    for par in itertools.groupby(datos):
        salida.append(par[0])
    return salida
print(claves([1, 1, 2, 3, 3]))
