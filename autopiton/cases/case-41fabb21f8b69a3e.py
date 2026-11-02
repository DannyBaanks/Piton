class C2(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg

class C1:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, tb):
        if exc_val is not None:
            print('handled:', exc_val.code, exc_val.msg)
            return True
        return False

with C1():
    raise C2(206, 'err')
print('suppressed')

