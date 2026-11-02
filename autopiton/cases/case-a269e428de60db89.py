def f1(v1):
    if (not (True)):
        raise ValueError('bounded')
    return v1

try:
    v2 = f1(True)
except ValueError:
    v2 = True
print(v2)

