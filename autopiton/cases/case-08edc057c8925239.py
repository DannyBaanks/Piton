def f1(v1):
    if (False == True):
        raise ValueError('bounded')
    return (False and (not (False)))

try:
    v2 = f1(True)
except ValueError:
    v2 = False
print(v2)

