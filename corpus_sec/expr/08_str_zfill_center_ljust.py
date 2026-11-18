# 08_str_zfill_center_ljust.py - métodos zfill, center, ljust, rjust
# Token: PITONF-C1A55E77

print("42".zfill(5))                 # 00042
print("-42".zfill(5))                # -0042
print("+42".zfill(5))                # +0042
print("42".zfill(2))                 # 42
print("42".zfill(1))                 # 42
print("".zfill(3))                   # 000
print("123".zfill(6))                # 000123

print("hello".center(9))             #   hello   
print("hello".center(9, "*"))        # **hello**
print("hello".center(5))             # hello
print("hello".center(4))             # hell
print("h".center(3))                 # hhh

print("hello".ljust(10))             # hello    
print("hello".ljust(10, "*"))        # hello*****
print("hello".ljust(5))              # hello
print("hello".ljust(3))              # hel

print("hello".rjust(10))             #     hello
print("hello".rjust(10, "*"))        # *****hello
print("hello".rjust(5))              # hello
print("hello".rjust(10, "-"))        # -----hello