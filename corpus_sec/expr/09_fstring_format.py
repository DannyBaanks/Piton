# 09_fstring_format.py - f-strings con ancho, alineación, precisión
# Token: PITONF-C1A55E77

name = "world"
print(f"Hola, {name}!")              # Hola, world!

x = 3.14159
print(f"{x}")                         # 3.14159
print(f"{x:.2f}")                     # 3.14
print(f"{x:.0f}")                     # 3
print(f"{x:.4f}")                     # 3.1416

print(f"{10:5d}")                     #    10
print(f"{100:5d}")                    #   100
print(f"{10:05d}")                    # 00010

print(f"{10:>5d}")                    #    10
print(f"{10:<5d}")                    # 10   
print(f"{10:^5d}")                    #  10 

print(f"{10:5.1f}")                   #  10.0
print(f"{3.14159:8.3f}")              #   3.142

value = 42
print(f"{value:+d}")                   # +42
print(f"{value:-d}")                   # 42
print(f"{value: d}")                   #  42

print(f"{'abc':=^10}")                # =====abc====