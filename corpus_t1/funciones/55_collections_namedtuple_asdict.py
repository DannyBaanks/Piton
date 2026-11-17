from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
def como_dict(px, py):
    p = Punto(px, py)
    return p._asdict()
print(como_dict(1, 2))
