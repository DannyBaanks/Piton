events = []
def f1():
    try:
        events.append(1)
        yield 1
        events.append(2)
        yield 15
    finally:
        events.append(95)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

