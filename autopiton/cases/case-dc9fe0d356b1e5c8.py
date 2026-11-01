def _f(**_k):
    return _k["x"]
_b = _f(x='ab' + 'cd')
print(_b)

