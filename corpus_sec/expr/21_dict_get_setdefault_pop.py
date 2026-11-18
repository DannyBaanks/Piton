# 21_dict_get_setdefault_pop.py - métodos get, setdefault, pop
# Token: PITONF-C1A55E77

d = {"a": 1, "b": 2}

# .get()
print(d.get("a"))                    # 1
print(d.get("c"))                    # None
print(d.get("c", 0))                 # 0
print(d.get("b", 99))                # 2

# .setdefault()
d1 = {"a": 1}
result = d1.setdefault("a", 99)    # 1 (no cambia)
print(d1)                            # {'a': 1}

d2 = {"a": 1}
result = d2.setdefault("b", 2)     # 2 (agrega)
print(d2)                            # {'a': 1, 'b': 2}

# .pop()
d3 = {"a": 1, "b": 2}
print(d3.pop("a"))                   # 1
print(d3)                            # {'b': 2}

d4 = {"a": 1, "b": 2}
print(d4.pop("c", 0))                # 0 (valor default)
print(d4)                            # {'a': 1, 'b': 2}

# .popitem()
d5 = {"a": 1, "b": 2}
print(d5.popitem())                  # ('b', 2) o ('a', 1) - LIFO en 3.7+
print(d5)                            # dict vacio o con un elemento