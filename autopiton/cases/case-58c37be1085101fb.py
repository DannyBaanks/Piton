def _outer():
    _cap = 'ab' + 'cd'
    def _inner():
        return _cap
    return _inner
_b = _outer()()
print(_b)

