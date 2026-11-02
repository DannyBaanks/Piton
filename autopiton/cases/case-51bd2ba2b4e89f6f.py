events = []
def f1():
    try:
        events.append(1)
        yield 9
        events.append(2)
        yield 19
    finally:
        events.append(94)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

