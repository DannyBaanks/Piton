# corpus_v4 tutorial page: inputoutput
# source: https://docs.python.org/3.12/tutorial/inputoutput.html
# sha256(HTML): dfd790d5034d2bf51f33dab1043ca70674aaa68acbbf7dfd4774b72658a35a78
# ejemplos doctest: 76 (excluidos 8 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    year = 2016
except BaseException:
    print('EXC')
try:
    event = 'Referendum'
except BaseException:
    print('EXC')
try:
    f'Results of the {year} {event}'
except BaseException:
    print('EXC')
try:
    yes_votes = 42_572_654
except BaseException:
    print('EXC')
try:
    total_votes = 85_705_149
except BaseException:
    print('EXC')
try:
    percentage = yes_votes / total_votes
except BaseException:
    print('EXC')
try:
    '{:-9} YES votes  {:2.2%}'.format(yes_votes, percentage)
except BaseException:
    print('EXC')
try:
    s = 'Hello, world.'
except BaseException:
    print('EXC')
try:
    str(s)
except BaseException:
    print('EXC')
try:
    repr(s)
except BaseException:
    print('EXC')
try:
    str(1/7)
except BaseException:
    print('EXC')
try:
    x = 10 * 3.25
except BaseException:
    print('EXC')
try:
    y = 200 * 200
except BaseException:
    print('EXC')
try:
    s = 'The value of x is ' + repr(x) + ', and y is ' + repr(y) + '...'
except BaseException:
    print('EXC')
try:
    print(s)
except BaseException:
    print('EXC')
try:
    hello = 'hello, world\n'
except BaseException:
    print('EXC')
try:
    hellos = repr(hello)
except BaseException:
    print('EXC')
try:
    print(hellos)
except BaseException:
    print('EXC')
try:
    repr((x, y, ('spam', 'eggs')))
except BaseException:
    print('EXC')
try:
    import math
except BaseException:
    print('EXC')
try:
    print(f'The value of pi is approximately {math.pi:.3f}.')
except BaseException:
    print('EXC')
try:
    table = {'Sjoerd': 4127, 'Jack': 4098, 'Dcab': 7678}
except BaseException:
    print('EXC')
try:
    animals = 'eels'
except BaseException:
    print('EXC')
try:
    print(f'My hovercraft is full of {animals}.')
except BaseException:
    print('EXC')
try:
    print(f'My hovercraft is full of {animals!r}.')
except BaseException:
    print('EXC')
try:
    bugs = 'roaches'
except BaseException:
    print('EXC')
try:
    count = 13
except BaseException:
    print('EXC')
try:
    area = 'living room'
except BaseException:
    print('EXC')
try:
    print(f'Debugging {bugs=} {count=} {area=}')
except BaseException:
    print('EXC')
try:
    print('We are the {} who say "{}!"'.format('knights', 'Ni'))
except BaseException:
    print('EXC')
try:
    print('{0} and {1}'.format('spam', 'eggs'))
except BaseException:
    print('EXC')
try:
    print('{1} and {0}'.format('spam', 'eggs'))
except BaseException:
    print('EXC')
try:
    print('This {food} is {adjective}.'.format(
    food='spam', adjective='absolutely horrible'))
except BaseException:
    print('EXC')
try:
    print('The story of {0}, {1}, and {other}.'.format('Bill', 'Manfred',
    other='Georg'))
except BaseException:
    print('EXC')
try:
    table = {'Sjoerd': 4127, 'Jack': 4098, 'Dcab': 8637678}
except BaseException:
    print('EXC')
try:
    print('Jack: {0[Jack]:d}; Sjoerd: {0[Sjoerd]:d}; '
    'Dcab: {0[Dcab]:d}'.format(table))
except BaseException:
    print('EXC')
try:
    table = {'Sjoerd': 4127, 'Jack': 4098, 'Dcab': 8637678}
except BaseException:
    print('EXC')
try:
    print('Jack: {Jack:d}; Sjoerd: {Sjoerd:d}; Dcab: {Dcab:d}'.format(**table))
except BaseException:
    print('EXC')
try:
    table = {k: str(v) for k, v in vars().items()}
except BaseException:
    print('EXC')
try:
    message = " ".join([f'{k}: ' + '{' + k +'};' for k in table.keys()])
except BaseException:
    print('EXC')
try:
    print(message.format(**table))
except BaseException:
    print('EXC')
try:
    '12'.zfill(5)
except BaseException:
    print('EXC')
try:
    '-3.14'.zfill(7)
except BaseException:
    print('EXC')
try:
    '3.14159265359'.zfill(5)
except BaseException:
    print('EXC')
try:
    import math
except BaseException:
    print('EXC')
try:
    print('The value of pi is approximately %5.3f.' % math.pi)
except BaseException:
    print('EXC')
try:
    f = open('workfile', 'w', encoding="utf-8")
except BaseException:
    print('EXC')
try:
    f.closed
except BaseException:
    print('EXC')
try:
    f.close()
except BaseException:
    print('EXC')
try:
    f.read()
except BaseException:
    print('EXC')
try:
    f.read()
except BaseException:
    print('EXC')
try:
    f.read()
except BaseException:
    print('EXC')
try:
    f.readline()
except BaseException:
    print('EXC')
try:
    f.readline()
except BaseException:
    print('EXC')
try:
    f.readline()
except BaseException:
    print('EXC')
try:
    f.write('This is a test\n')
except BaseException:
    print('EXC')
try:
    value = ('the answer', 42)
except BaseException:
    print('EXC')
try:
    s = str(value)  # convert the tuple to string
except BaseException:
    print('EXC')
try:
    f.write(s)
except BaseException:
    print('EXC')
try:
    f = open('workfile', 'rb+')
except BaseException:
    print('EXC')
try:
    f.write(b'0123456789abcdef')
except BaseException:
    print('EXC')
try:
    f.seek(5)      # Go to the 6th byte in the file
except BaseException:
    print('EXC')
try:
    f.read(1)
except BaseException:
    print('EXC')
try:
    f.seek(-3, 2)  # Go to the 3rd byte before the end
except BaseException:
    print('EXC')
try:
    f.read(1)
except BaseException:
    print('EXC')
try:
    import json
except BaseException:
    print('EXC')
try:
    x = [1, 'simple', 'list']
except BaseException:
    print('EXC')
try:
    json.dumps(x)
except BaseException:
    print('EXC')
