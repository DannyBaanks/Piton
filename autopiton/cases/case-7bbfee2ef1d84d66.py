def _g():
    return 'ab' + 'cd'
def _f():
    return _g()
_b = _f()
_v = lambda: _b
print(_v())

