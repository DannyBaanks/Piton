import itertools
def cortar(datos, n):
    return list(itertools.islice(datos, n))
print(cortar(["a", "b", "c", "d"], 3))
