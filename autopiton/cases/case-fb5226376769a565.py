def f1(v1):
    for i in range(v1):
        try:
            if i == 2:
                raise ValueError('gen_stop')
            yield (i + i)
        except ValueError:
            yield 14

print(list(zip(range(3), f1(3), range(10, 10 + 3))))

