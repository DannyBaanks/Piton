def f1(v1):
    if (not (True)):
        raise ValueError('bounded')
    return ((False and True) and v1)

try:
    v2 = f1(False)
except ValueError:
    v2 = True
print(v2)

