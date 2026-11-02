def f1(v1):
    if (not (False)):
        raise ValueError('bounded')
    return ((v1 and v1) and (not (v1)))

try:
    v2 = f1(True)
except ValueError:
    v2 = True
print(v2)

