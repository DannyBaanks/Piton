class C1:
    def __init__(self, val):
        self.val = val
    def __str__(self):
        return 'val:' + str(self.val)
    def __repr__(self):
        return '<' + 'val' + ' ' + str(self.val) + '>'

obj = C1(13)
print(str(obj), repr(obj))

