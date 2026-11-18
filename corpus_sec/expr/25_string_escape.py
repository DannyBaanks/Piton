# 25_string_escape.py - strings con escape y caracteres especiales
# Token: PITONF-C1A55E77

print("hello\nworld")                 # hello<newline>world
print("hello\tworld")                # hello<tab>world
print("hello\rworld")                # hellloworld (carriage return)
print("hello\\nworld")                # hello\nworld (literal \n)
print("hello\"world\"")                # hello"world"

# caracteres unicode
print("\u0041")                        # A
print("\u00e9")                        # é
print(chr(0x2603))                     # snowman

# bytes
print(b"hello")                        # b'hello'
print(b"hello\nworld")                # b'hello\nworld'
print(b"\x41")                         # b'A'

# concatenacion de strings
print("hello" + " " + "world")        # hello world