def f1(v1):
    if (False and True):
        raise ValueError('bounded')
    return (v1 or (v1 and True))

try:
    v2 = f1(True)
except ValueError:
    v2 = True
print(v2)

