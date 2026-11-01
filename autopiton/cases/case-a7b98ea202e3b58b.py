def _g():
    return 7.5 + 3.5
def _f():
    return _g()
_b = _f()
_v = lambda: _b
print(_v())

