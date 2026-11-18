try:
    values = list((1,))
    result = values[2]
except IndexError as exc:
    print(exc.args)
