class Divider:
    def divide(self, value):
        return 10 // value
try:
    print(Divider().divide(0))
except ZeroDivisionError as exc:
    print(type(exc).__name__, exc.args)
