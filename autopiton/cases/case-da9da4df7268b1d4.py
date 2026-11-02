events = []
class C1:
    def __enter__(self):
        events.append('m1_enter')
        return 17
    def __exit__(self, exc_type, exc_val, tb):
        events.append('m1_exit')
        return False

class C2:
    def __enter__(self):
        events.append('m2_enter')
        return 34
    def __exit__(self, exc_type, exc_val, tb):
        events.append('m2_exit')
        return False

with C1() as a, C2() as b:
    events.append(a + b)

print(events)

