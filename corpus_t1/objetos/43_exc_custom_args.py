class AppError(Exception):
    pass
try:
    raise AppError("bad", 7)
except AppError as exc:
    print(type(exc).__name__, exc.args)
