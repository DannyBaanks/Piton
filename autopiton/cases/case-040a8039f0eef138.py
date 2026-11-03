def f1(v1):
    if True:
        return (v1 // 5)
    return (6 % 6)

v2 = 0
for i in range(4):
    v2 = v2 + f1(i)
print(v2)

