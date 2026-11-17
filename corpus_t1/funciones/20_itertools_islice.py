import itertools
def cortar(datos, inicio, fin):
    return list(itertools.islice(datos, inicio, fin))
print(cortar([1, 2, 3, 4, 5, 6], 2, 5))
