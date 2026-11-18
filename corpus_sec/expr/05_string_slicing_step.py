# 05_string_slicing_step.py - slicing de strings con paso
# Token: PITONF-C1A55E77

s = "abcdefghij"
print(s[::2])          # acegi
print(s[1::2])         # bdfhj
print(s[::-1])         # jihgfedcba
print(s[::-2])         # jhfdb
print(s[7:1:-1])       # hgfed
print(s[7:1:-2])       # hf
print(s[:-1])          # abcdefgh
print(s[-1:])          # j
print(s[-1:-4:-1])     # jih