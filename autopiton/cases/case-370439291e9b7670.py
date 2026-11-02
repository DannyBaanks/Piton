def f1(v1):
    if (False or True):
        return (6 & 27)
    return (24 >> 4)

v2 = 0
for i in range(2):
    v2 = v2 + f1(i)
print(v2)

