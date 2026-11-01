def _f(**_k):
    return _k["x"]
_b = _f(x=7 + 3)
_v = lambda: _b
print(_v())

