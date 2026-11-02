class C1(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg

def f1(flag):
    if flag:
        raise C1(553, 'fail')
    return 0

try:
    f1(True)
except C1 as e:
    print(e.code, e.msg)

