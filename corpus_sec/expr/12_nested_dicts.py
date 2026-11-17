# 12_nested_dicts.py - diccionarios anidados y métodos
# Token: PITONF-C1A55E77

d = {"a": {"b": 1}}
print(d["a"]["b"])                    # 1

d2 = {"x": {"y": {"z": 42}}}
print(d2["x"]["y"]["z"])              # 42

print({"a": 1, "b": 2}.get("a"))       # 1
print({"a": 1, "b": 2}.get("c"))       # None
print({"a": 1, "b": 2}.get("c", 0))    # 0

d3 = {"a": 1}
d3.setdefault("a", 99)                # 1
d3.setdefault("b", 2)                 # 2
print(d3)                             # {'a': 1, 'b': 2}

d4 = {"a": 1, "b": 2}
print(d4.pop("a"))                    # 1
print(d4)                             # {'b': 2}
print(d4.pop("b", 0))                 # 2
print(d4.pop("c", 0))                 # 0

d5 = {"a": 1}
d5["a"] = d5.pop("a")                 # 1
print(d5)                             # {'a': 1}