def _outer():
    _cap = 7 + 3
    def _inner():
        return _cap
    return _inner
_b = _outer()()
print(_b)

