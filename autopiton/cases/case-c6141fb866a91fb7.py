def _f(**_k):
    return _k["x"]
_b = _f(x='ab' + 'cd')
_v = lambda: _b
print(_v())

