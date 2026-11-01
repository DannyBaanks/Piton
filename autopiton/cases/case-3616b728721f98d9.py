def _f(*_a):
    return _a[0]
_b = _f(*[7 + 3])
_v = lambda: _b
print(_v())

