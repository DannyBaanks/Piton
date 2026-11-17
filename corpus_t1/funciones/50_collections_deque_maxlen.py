from collections import deque
def acotar(datos, n):
    q = deque(datos, n)
    return list(q)
print(acotar([1, 2, 3, 4], 2))
