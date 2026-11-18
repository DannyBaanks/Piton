from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
def mostrar(px, py):
    p = Punto(px, py)
    return p
print(mostrar(1, 2))
