def _gen():
    yield 'ab' + 'cd'
_b = next(_gen())
print(_b)

