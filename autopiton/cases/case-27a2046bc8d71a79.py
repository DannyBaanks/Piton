try:
    raise ValueError("test error")
except ValueError as e:
    print("caught", str(e))
