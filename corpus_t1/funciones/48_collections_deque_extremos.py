from collections import deque
def operar(datos):
    q = deque(datos)
    q.append(4)
    q.appendleft(0)
    return list(q)
print(operar([1, 2, 3]))
