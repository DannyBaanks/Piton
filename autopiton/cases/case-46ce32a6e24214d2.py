def _outer():
    _cap = 7.5 + 3.5
    def _inner():
        return _cap
    return _inner
_b = _outer()()
_v = lambda: _b
print(_v())

