from collections import Counter
def frecuentes(datos):
    return Counter(datos).most_common()
print(frecuentes(["a", "b", "a", "c"]))
