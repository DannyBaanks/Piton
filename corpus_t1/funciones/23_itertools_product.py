import itertools
def combinar(a, b):
    return list(itertools.product(a, b))
print(combinar([1, 2], ["a", "b"]))
