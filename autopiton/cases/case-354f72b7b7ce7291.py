def f1(v1):
    if True:
        return 13
    return (v1 | 1)

v2 = 0
for i in range(2):
    v2 = v2 + f1(i)
print(v2)

