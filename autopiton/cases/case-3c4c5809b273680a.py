events = []
def f1():
    try:
        events.append(1)
        yield 7
        events.append(2)
        yield 20
    finally:
        events.append(92)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

