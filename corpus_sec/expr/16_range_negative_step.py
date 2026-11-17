# 16_range_negative_step.py - range con paso negativo
# Token: PITONF-C1A55E77

print(list(range(5)))                 # [0, 1, 2, 3, 4]
print(list(range(1, 5)))              # [1, 2, 3, 4]
print(list(range(0, 10, 2)))          # [0, 2, 4, 6, 8]
print(list(range(10, 0, -1)))        # [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
print(list(range(5, 0, -1)))         # [5, 4, 3, 2, 1]
print(list(range(0, 10, -2)))        # []
print(list(range(10, 0, -2)))        # [10, 8, 6, 4, 2]
print(list(range(3, -1, -1)))        # [3, 2, 1, 0]
print(list(range(5, 5, 1)))          # []
print(list(range(5, 5, -1)))         # []
print(list(range(0, 5, 1)))          # [0, 1, 2, 3, 4]
print(list(range(5, 0, 1)))          # []