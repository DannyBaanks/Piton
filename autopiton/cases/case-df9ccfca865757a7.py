def _f(**_k):
    return _k["x"]
_b = _f(x=7.5 + 3.5)
_v = lambda: _b
print(_v())

