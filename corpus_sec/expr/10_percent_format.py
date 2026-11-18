# 10_percent_format.py - formateo con operador %
# Token: PITONF-C1A55E77

print("%s" % "hola")                  # hola
print("%d" % 42)                      # 42
print("%f" % 3.14)                    # 3.140000
print("%.2f" % 3.14159)               # 3.14
print("%5d" % 42)                     #   42
print("%-5d" % 42)                    # 42   
print("%+d" % 42)                     # +42

print("%s %s" % ("a", "b"))          # a b
print("%(x)s" % {"x": 10})           # 10
print("%(x)d" % {"x": 10})           # 10
print("%(v).2f" % {"v": 3.14159})    # 3.14

print("%r" % 42)                      # 42
print("%r" % "hola")                  # 'hola'

print("%o" % 8)                       # 10
print("%x" % 255)                     # ff
print("%X" % 255)                     # FF

print("%%" % 42)                      # %