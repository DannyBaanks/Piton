def f1(v1):
    v2 = v1
    v2.append((-(-8)))
    return v1[::-1]

base = [(10 + -5), (-(1))]
rev = f1(base)
d1 = {'a': base[0], 'b': rev[0]}
d2 = {'b': rev[-1], 'c': len(base)}
merged = {**d1, **d2}
print(base, rev, merged['a'], merged['b'], merged['c'])

