def f1(v1):
    if (4.75 != 6.5):
        return 0
    return (2 << 0)

v2 = 0
for i in range(4):
    v2 = v2 + f1(i)
print(v2)

