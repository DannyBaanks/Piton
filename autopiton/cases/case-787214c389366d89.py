class C1:
    a = 16
    b = 47

class C2(C1):
    b = 82

class C3(C2):
    pass

s1 = C2()
s2 = C3()
print(C1.a, C1.b)
print(C2.a, C2.b)
print(C3.a, C3.b)
print(s1.a, s1.b, s2.a, s2.b)

