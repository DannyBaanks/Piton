import itertools
def contar(datos):
    salida = []
    for par in itertools.groupby(datos):
        salida.append(par[0])
        salida.append(len(list(par[1])))
    return salida
print(contar([1, 1, 2, 3, 3]))
