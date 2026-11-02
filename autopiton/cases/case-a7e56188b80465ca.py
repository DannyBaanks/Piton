def f1(v1):
    if (True and False):
        raise ValueError('bounded')
    return (-(2.25))

try:
    v2 = f1(7.5)
except ValueError:
    v2 = 3.5
print(v2)

