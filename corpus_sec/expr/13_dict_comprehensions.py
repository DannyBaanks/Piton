# 13_dict_comprehensions.py - comprensiones de diccionarios con filtro
# Token: PITONF-C1A55E77

print({x: x**2 for x in range(5)})   # {0: 0, 1: 1, 2: 4, 3: 9, 4: 16}
print({x: x**2 for x in range(10) if x % 2 == 0})  # {0: 0, 2: 4, 4: 16, 6: 36, 8: 64}
print({k: v for k, v in {'a': 1, 'b': 2}.items() if v > 0})  # {'a': 1, 'b': 2}
print({k: v for k, v in {'a': 1, 'b': 0, 'c': 3}.items() if v != 0})  # {'a': 1, 'c': 3}
print({s: len(s) for s in ['a', 'bb', 'ccc']})  # {'a': 1, 'bb': 2, 'ccc': 3}
print({i: i * 10 for i in range(3, 6)})  # {3: 30, 4: 40, 5: 50}