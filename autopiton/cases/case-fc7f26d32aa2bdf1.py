def _gen():
    yield 'ab' + 'cd'
_b = next(_gen())
_v = lambda: _b
print(_v())

