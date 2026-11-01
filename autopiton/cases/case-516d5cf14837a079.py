def _gen():
    yield 7.5 + 3.5
_b = next(_gen())
_v = lambda: _b
print(_v())

