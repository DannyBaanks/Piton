try:
    x = 1 / 0
except ZeroDivisionError:
    print("div by zero")
except Exception:
    print("other")
