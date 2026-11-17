import itertools
def unir(a, b):
    return list(itertools.chain(a, b))
print(unir([1, 2], [3, 4]))
