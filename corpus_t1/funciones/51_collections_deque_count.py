from collections import deque
def contar(datos, valor):
    q = deque(datos)
    return q.count(valor)
print(contar([1, 2, 2, 3, 2], 2))
