def f1(v1):
    if True:
        raise ValueError('bounded')
    return ((v1 * 3) * 1)

try:
    v2 = f1('cd')
except ValueError:
    v2 = 'cd'
print(v2)

