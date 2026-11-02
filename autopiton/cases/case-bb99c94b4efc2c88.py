def f1(v1):
    if True:
        raise ValueError('bounded')
    return (v1 == False)

try:
    v2 = f1(True)
except ValueError:
    v2 = False
print(v2)

