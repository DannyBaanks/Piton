from collections import defaultdict
def contar(claves):
    d = defaultdict(int)
    for k in claves:
        d[k] += 1
    return dict(d)
print(contar(["a", "b", "a", "a"]))
