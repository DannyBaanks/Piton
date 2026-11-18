from functools import lru_cache
calls = {'n':0}
@lru_cache(maxsize=None)
def f(n):
    calls['n'] += 1
    return n*n
print(f(2))
print(f(2))
print(calls['n'])
