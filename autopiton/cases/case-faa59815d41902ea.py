def f1(v1):
    if True:
        return (v1 + v1)
    return (v1 ^ 27)

v2 = 0
for i in range(2):
    v2 = v2 + f1(i)
print(v2)

