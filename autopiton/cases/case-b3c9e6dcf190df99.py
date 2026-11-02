class C1:
    def __init__(self, val):
        self.val = val
    def __str__(self):
        return 'elem:' + str(self.val)
    def __repr__(self):
        return '<' + 'elem' + ' ' + str(self.val) + '>'

obj = C1(-2)
print(str(obj), repr(obj))

