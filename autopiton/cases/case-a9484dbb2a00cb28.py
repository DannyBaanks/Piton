def f1(v1):
    if (False or False):
        return (v1 * 4)
    return (12 << 3)

v2 = 0
for i in range(2):
    v2 = v2 + f1(i)
print(v2)

