def _gen():
    yield 7 + 3
_b = next(_gen())
print(_b)

