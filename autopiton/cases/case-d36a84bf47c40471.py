items = [1, 2, 3, 4]
g1 = (x * 5 for x in items if x > 1)
l1 = list(g1)
g2 = (x + 10 for x in items)
t1 = tuple(g2)
g3 = (x % 2 for x in items)
s1 = sorted(list(set(g3)))
print(l1, t1, s1)

