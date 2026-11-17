import itertools
def pares(datos, k):
    return list(itertools.combinations(datos, k))
print(pares([1, 2, 3], 2))
