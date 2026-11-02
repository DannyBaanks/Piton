def f1(v1):
    for i in range(v1):
        try:
            if i == 1:
                raise ValueError('gen_stop')
            yield (i + 14)
        except ValueError:
            yield 44

print(list(zip(range(2), f1(2), range(10, 10 + 2))))

