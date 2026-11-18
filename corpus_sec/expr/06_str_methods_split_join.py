# 06_str_methods_split_join.py - métodos de string split/join
# Token: PITONF-C1A55E77

print("a,b,c".split(","))          # ['a', 'b', 'c']
print("a,bc".split(",b"))          # ['a', 'c']
print("a".split("x"))              # ['a']
print("abc".split("b"))            # ['a', 'c']
print("a  b  c".split())            # ['a', 'b', 'c']
print("a  b  c".split(" ", 2))      # ['a', 'b', 'c']
print("a  b  c".split(" ", 1))      # ['a', 'b  c']
print("a,b,c".split(",", 1))       # ['a', 'b,c']
print("a  b  c  ".split())          # ['a', 'b', 'c']
print("---".join(["a", "b", "c"]))  # a---b---c
print(" ".join(["hello", "mundo"]))  # hello mundo
print("".join([]))                  # ''