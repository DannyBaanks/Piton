from collections import deque
def operar(datos):
    q = deque(datos)
    ultimo = q.pop()
    primero = q.popleft()
    return list(q)
print(operar([1, 2, 3, 4]))
