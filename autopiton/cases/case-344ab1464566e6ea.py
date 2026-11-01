def _gen():
    yield True == True
_b = next(_gen())
_v = lambda: _b
print(_v())

