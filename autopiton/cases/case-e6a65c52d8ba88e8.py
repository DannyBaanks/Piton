v2 = []
def f1(v1):
    try:
        if v1 < 0:
            raise RuntimeError('neg')
        v2.append(v1)
    except RuntimeError:
        v2.append(7)
    finally:
        v2.append(99)
    return len(v2)

r1 = f1(2)
r2 = f1(-1)
print(r1, r2, v2)

