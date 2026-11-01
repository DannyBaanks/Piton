def _gen():
    yield 7 + 3
_b = next(_gen())
_v = lambda: _b
print(_v())

