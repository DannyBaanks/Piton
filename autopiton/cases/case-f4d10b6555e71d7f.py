def _g():
    return 7 + 3
def _f():
    return _g()
_b = _f()
_v = lambda: _b
print(_v())

