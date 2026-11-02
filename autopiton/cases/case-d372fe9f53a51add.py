def f1(v1):
    if (6.5 != 1.5):
        return (~(v1))
    return v1

v2 = 0
for i in range(2):
    v2 = v2 + f1(i)
print(v2)

