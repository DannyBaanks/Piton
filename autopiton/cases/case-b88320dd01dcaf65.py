def f1(v1):
    if (False and False):
        raise ValueError('bounded')
    return v1

try:
    v2 = f1(6.5)
except ValueError:
    v2 = 9.25
print(v2)

