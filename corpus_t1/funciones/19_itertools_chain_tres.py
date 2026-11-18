import itertools
def unir(a, b, c):
    return list(itertools.chain(a, b, c))
print(unir([1], [2, 3], [4, 5]))
