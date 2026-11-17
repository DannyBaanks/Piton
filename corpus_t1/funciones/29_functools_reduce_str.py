import functools
def juntar(a, b):
    return a + b
def unir(partes):
    return functools.reduce(juntar, partes)
print(unir(["a", "b", "c"]))
