def _outer():
    _cap = True == True
    def _inner():
        return _cap
    return _inner
_b = _outer()()
print(_b)

