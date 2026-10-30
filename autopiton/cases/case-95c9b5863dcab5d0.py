class C:
    def __enter__(self):
        print('enter')
        return self
    def __exit__(self, kind, value, traceback):
        print('exit')

with C():
    print('body')
