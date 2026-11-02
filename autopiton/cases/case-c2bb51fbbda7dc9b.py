class C1:
    a = 19
    b = 46

class C2(C1):
    b = 75

class C3(C2):
    pass

s1 = C2()
s2 = C3()
print(C1.a, C1.b)
print(C2.a, C2.b)
print(C3.a, C3.b)
print(s1.a, s1.b, s2.a, s2.b)

