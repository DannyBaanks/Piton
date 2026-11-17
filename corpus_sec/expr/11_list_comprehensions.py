# 11_list_comprehensions.py - comprensiones con filtro
# Token: PITONF-C1A55E77

print([x for x in range(5)])           # [0, 1, 2, 3, 4]
print([x for x in range(10) if x % 2 == 0])  # [0, 2, 4, 6, 8]
print([x * 2 for x in range(5)])       # [0, 2, 4, 6, 8]
print([x ** 2 for x in range(5)])      # [0, 1, 4, 9, 16]
print([(x, y) for x in [1, 2] for y in [3, 4]])  # [(1, 3), (1, 4), (2, 3), (2, 4)]
print([x for x in range(10) if x > 5]) # [6, 7, 8, 9]
print([x for x in [1, 2, 3, 4, 5] if x != 3])  # [1, 2, 4, 5]
print([s.upper() for s in ['a', 'b', 'c']])  # ['A', 'B', 'C']