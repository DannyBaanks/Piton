from collections import defaultdict
def agrupar(pares):
    d = defaultdict(list)
    for par in pares:
        d[par[0]].append(par[1])
    return dict(d)
print(agrupar([["a", 1], ["b", 2], ["a", 3]]))
