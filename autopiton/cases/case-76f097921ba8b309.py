def _f(_x='ab' + 'cd'):
    return _x
_b = _f()
_v = lambda: _b
print(_v())

