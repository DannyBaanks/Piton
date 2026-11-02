def f1(v1):
    for i in range(v1):
        try:
            if i == 2:
                raise ValueError('gen_stop')
            yield (1 + -1)
        except ValueError:
            yield 43

print(list(zip(range(3), f1(3), range(10, 10 + 3))))

