# 20_string_fstring_conversion.py - f-strings con conversiones
# Token: PITONF-C1A55E77

name = "world"
age = 25
print(f"Hola, {name}!")              # Hola, world!
print(f"Tengo {age} años")           # Tengo 25 años

# conversion !s (str)
print(f"{name!s}")                   # world

# conversion !r (repr)
print(f"{name!r}")                   # 'world'
print(f"{42!r}")                     # 42

# expresiones dentro de f-string
print(f"{2 + 2}")                    # 4
print(f"{10 * 5}")                   # 50
print(f"{'hello'.upper()}")          # HELLO
print(f"{len('hello')}")             # 5