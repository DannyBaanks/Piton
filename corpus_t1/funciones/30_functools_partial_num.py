import functools
def potencia(base, exp):
    return base ** exp
al_cubo = functools.partial(potencia, 3)
print(al_cubo(4))
