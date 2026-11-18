# 07_str_strip_find_replace.py - métodos strip, find, replace
# Token: PITONF-C1A55E77

print("  hello  ".strip())           # hello
print("abchelloabc".strip("abc"))    # hello
print("hello".strip("lh"))           # eo
print("hello".strip())               # hello
print("abc".strip())                 # abc
print("".strip())                    # ''
print("hello".strip("x"))            # hello
print("abcabc".strip("abc"))         # ''

print("hello world".find("world"))   # 6
print("hello world".find("xyz"))     # -1
print("hello hello".find("hello", 6)) # 6
print("hello hello".find("hello", 7)) # -1
print("hello".find("llo", 1))        # 2
print("hello".find(""))              # 0

print("hello".replace("l", "x"))     # hexxo
print("hello".replace("l", "ll"))    # hello
print("hello".replace("h", ""))      # ello
print("hello world".replace(" ", "_")) # hello_world
print("hello".replace("l", "L", 1))  # heLLo
print("aaa".replace("a", "b", 2))    # bba
print("hello".replace("x", "y"))     # hello