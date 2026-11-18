# 03_round_trunc.py - round y trunc con floats
# Token: PITONF-C1A55E77

print(round(2.5))        # 2 (banker's rounding)
print(round(3.5))        # 4 (banker's rounding)
print(round(2.6))        # 3
print(round(2.4))        # 2
print(round(2.567, 2))   # 2.57
print(round(2.567, 1))   # 2.6
print(round(1234.5, -1)) # 1230.0
print(trunc(2.9))        # NameError: trunc no esta en built-in global