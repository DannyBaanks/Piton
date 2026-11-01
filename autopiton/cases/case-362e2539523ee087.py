def _f():
    return 'ab' + 'cd'
_b = _f()
_v = lambda: _b
print(_v())

