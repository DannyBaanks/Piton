vals = [0, 1, '', 'hi', [], [1]]
res1 = list(map(bool, vals))
res2 = tuple(map(len, ['a', 'abc', 'hello']))
print(res1, res2)

