from collections import Counter
def frecuentes(datos, n):
    return Counter(datos).most_common(n)
print(frecuentes(["a", "b", "a", "a", "c"], 2))
