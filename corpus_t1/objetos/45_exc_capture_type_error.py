try:
    values = [1, 2]
    result = values.remove("x")
except TypeError as exc:
    print(type(exc).__name__, str(exc))
