def _g():
    return 'ab' + 'cd'
def _f():
    return _g()
_b = _f()
print(_b)

