def _gen():
    yield True == True
_b = next(_gen())
print(_b)

