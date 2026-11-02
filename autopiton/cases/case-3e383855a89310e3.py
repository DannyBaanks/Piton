events = []
def f1():
    try:
        events.append(1)
        yield 5
        events.append(2)
        yield 11
    finally:
        events.append(90)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

