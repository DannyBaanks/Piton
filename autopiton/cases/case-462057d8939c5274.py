def _g():
    return True == True
def _f():
    return _g()
_b = _f()
_v = lambda: _b
print(_v())

