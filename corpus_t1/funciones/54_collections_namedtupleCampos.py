from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
def datos(px, py):
    p = Punto(px, py)
    return p.x + p.y
print(datos(3, 4))
