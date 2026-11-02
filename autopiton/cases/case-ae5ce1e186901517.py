def f1(v1):
    if (False and True):
        return v1
    return 3

v2 = 0
for i in range(3):
    v2 = v2 + f1(i)
print(v2)

