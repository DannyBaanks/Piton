def _f(_x):
    return _x
_b = _f('ab' + 'cd')
_v = lambda: _b
print(_v())

