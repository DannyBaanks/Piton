try:
    raise ValueError("inner")
except ValueError:
    raise TypeError("outer") from None
