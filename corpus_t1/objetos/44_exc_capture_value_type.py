try:
    values = [1, 2]
    result = values.remove(3)
except ValueError as exc:
    print(type(exc).__name__, str(exc))
