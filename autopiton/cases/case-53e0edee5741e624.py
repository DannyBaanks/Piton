def _f(*_a):
    return _a[0]
_b = _f(*[True == True])
_v = lambda: _b
print(_v())

