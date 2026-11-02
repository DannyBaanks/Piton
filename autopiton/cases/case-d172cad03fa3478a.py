def f1(v1):
    for i in range(v1):
        try:
            if i == 3:
                raise ValueError('gen_stop')
            yield 9
        except ValueError:
            yield 44

print(list(zip(range(4), f1(4), range(10, 10 + 4))))

