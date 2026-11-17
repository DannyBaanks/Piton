# 24_float_comparison.py - comparaciones de floats
# Token: PITONF-C1A55E77

print(0.1 + 0.2)                     # 0.30000000000000004
print(0.1 + 0.2 == 0.3)             # False
print(0.3 == 0.3)                    # True
print(0.1 + 0.2 > 0.3)               # True
print(0.1 + 0.2 >= 0.3)              # True

# comparaciones simples con floats
print(0.1 < 0.2)                     # True
print(0.2 < 0.3)                     # True
print(0.1 + 0.2 < 0.4)               # True

# float('inf') y float('-inf')
print(float('inf') > 1e308)        # True
print(float('-inf') < -1e308)      # True
print(float('inf') == float('inf')) # True

# comparacion de float con int (Python los trata como equivalentes por valor)
print(1.0 == 1)                      # True
print(1 > 0.9999999999999999)       # True