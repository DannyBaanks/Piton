def _f(*_a):
    return _a[0]
_b = _f(*['ab' + 'cd'])
_v = lambda: _b
print(_v())

