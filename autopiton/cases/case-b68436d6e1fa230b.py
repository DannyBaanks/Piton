v2 = []
def f1(v1):
    try:
        if v1 < 0:
            raise RuntimeError('neg')
        v2.append(v1)
        return v1 * 10
    except RuntimeError:
        v2.append(-7)
        return -99
    finally:
        v2.append(99)

r1 = f1(2)
r2 = f1(-1)
print(r1, r2, v2)

