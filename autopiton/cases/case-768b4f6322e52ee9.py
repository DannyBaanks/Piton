def _f(**_k):
    return _k["x"]
_b = _f(x=True == True)
_v = lambda: _b
print(_v())

