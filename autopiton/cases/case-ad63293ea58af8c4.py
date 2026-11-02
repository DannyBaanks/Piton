def f1(v1):
    if True:
        raise ValueError('bounded')
    return ((v1 ^ 6) & 24)

try:
    v2 = f1(4)
except ValueError:
    v2 = 8
print(v2)

