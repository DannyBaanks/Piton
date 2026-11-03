vals = [-7, 12, -5]
res1 = list(map(abs, vals))
res2 = tuple(map(lambda x: x * 2, res1))
print(res1, res2)

