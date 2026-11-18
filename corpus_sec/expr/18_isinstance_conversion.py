# 18_isinstance_conversion.py - isinstance, conversiones, bool() de vacíos
# Token: PITONF-C1A55E77

print(isinstance(1, int))            # True
print(isinstance(1.0, int))          # False
print(isinstance(1.0, float))      # True
print(isinstance("hello", str))      # True
print(isinstance([1, 2, 3], list))   # True
print(isinstance((1, 2), list))     # False
print(isinstance({1: 2}, dict))    # True

# conversiones numericas
print(int(3.7))                    # 3
print(int("42"))                     # 42
print(float(3))                      # 3.0
print(float("3.14"))                  # 3.14
print(str(42))                       # '42'
print(str(3.14))                     # '3.14'

# bool() de vacios
print(bool(False))                   # False
print(bool(True))                    # True
print(bool(0))                       # False
print(bool(1))                       # True
print(bool(""))                      # False
print(bool("hello"))                 # True
print(bool([]))                      # False
print(bool([1]))                     # True
print(bool({}))                      # False
print(bool({"a": 1}))                # True
print(bool(()))                      # False
print(bool((1,)))                    # True

# None como bool
print(bool(None))                    # False
print(not None)                      # True