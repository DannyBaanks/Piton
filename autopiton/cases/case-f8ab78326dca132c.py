def f1(v1):
    if (not (False)):
        raise ValueError('bounded')
    return (True == (v1 or False))

try:
    v2 = f1(True)
except ValueError:
    v2 = True
print(v2)

