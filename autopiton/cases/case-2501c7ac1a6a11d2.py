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
            return False
        return False

try:
    with C1():
        raise C2(353, 'err')
except C2 as e:
    print('outer caught:', e.code, e.msg)

