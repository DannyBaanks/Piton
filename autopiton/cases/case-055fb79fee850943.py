def f1(v1=(True and False), v2=(1 ^ 31)):
    return ((v2 // 3) > (5 << 3))

print(f1())
print(f1((not (True))))

