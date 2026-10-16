# corpus_v4 tutorial page: stdlib2
# source: https://docs.python.org/3.12/tutorial/stdlib2.html
# sha256(HTML): fa371c2495d35e82b877a5bee3701c0d38005a64f0eaab92d7eaae785f6d8952
# ejemplos doctest: 63 (excluidos 3 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    import reprlib
except BaseException:
    print('EXC')
try:
    reprlib.repr(set('supercalifragilisticexpialidocious'))
except BaseException:
    print('EXC')
try:
    import pprint
except BaseException:
    print('EXC')
try:
    t = [[[['black', 'cyan'], 'white', ['green', 'red']], [['magenta',
    'yellow'], 'blue']]]
    
except BaseException:
    print('EXC')
try:
    pprint.pprint(t, width=30)
except BaseException:
    print('EXC')
try:
    import textwrap
except BaseException:
    print('EXC')
try:
    doc = """The wrap() method is just like fill() except that it returns
    a list of strings instead of one big string with newlines to separate
    the wrapped lines."""
    
except BaseException:
    print('EXC')
try:
    print(textwrap.fill(doc, width=40))
except BaseException:
    print('EXC')
try:
    import locale
except BaseException:
    print('EXC')
try:
    locale.setlocale(locale.LC_ALL, 'English_United States.1252')
except BaseException:
    print('EXC')
try:
    conv = locale.localeconv()          # get a mapping of conventions
except BaseException:
    print('EXC')
try:
    x = 1234567.8
except BaseException:
    print('EXC')
try:
    locale.format_string("%d", x, grouping=True)
except BaseException:
    print('EXC')
try:
    locale.format_string("%s%.*f", (conv['currency_symbol'],
    conv['frac_digits'], x), grouping=True)
except BaseException:
    print('EXC')
try:
    from string import Template
except BaseException:
    print('EXC')
try:
    t = Template('${village}folk send $$10 to $cause.')
except BaseException:
    print('EXC')
try:
    t.substitute(village='Nottingham', cause='the ditch fund')
except BaseException:
    print('EXC')
try:
    t = Template('Return the $item to $owner.')
except BaseException:
    print('EXC')
try:
    d = dict(item='unladen swallow')
except BaseException:
    print('EXC')
try:
    t.substitute(d)
except BaseException:
    print('EXC')
try:
    t.safe_substitute(d)
except BaseException:
    print('EXC')
try:
    import time, os.path
except BaseException:
    print('EXC')
try:
    photofiles = ['img_1074.jpg', 'img_1076.jpg', 'img_1077.jpg']
except BaseException:
    print('EXC')
try:
    fmt = input('Enter rename style (%d-date %n-seqnum %f-format):  ')
except BaseException:
    print('EXC')
try:
    t = BatchRename(fmt)
except BaseException:
    print('EXC')
try:
    date = time.strftime('%d%b%y')
except BaseException:
    print('EXC')
try:
    import weakref, gc
except BaseException:
    print('EXC')
try:
    a = A(10)                   # create a reference
except BaseException:
    print('EXC')
try:
    d = weakref.WeakValueDictionary()
except BaseException:
    print('EXC')
try:
    d['primary'] = a            # does not create a reference
except BaseException:
    print('EXC')
try:
    d['primary']                # fetch the object if it is still alive
except BaseException:
    print('EXC')
try:
    del a                       # remove the one reference
except BaseException:
    print('EXC')
try:
    gc.collect()                # run garbage collection right away
except BaseException:
    print('EXC')
try:
    d['primary']                # entry was automatically removed
except BaseException:
    print('EXC')
try:
    from array import array
except BaseException:
    print('EXC')
try:
    a = array('H', [4000, 10, 700, 22222])
except BaseException:
    print('EXC')
try:
    sum(a)
except BaseException:
    print('EXC')
try:
    a[1:3]
except BaseException:
    print('EXC')
try:
    from collections import deque
except BaseException:
    print('EXC')
try:
    d = deque(["task1", "task2", "task3"])
except BaseException:
    print('EXC')
try:
    d.append("task4")
except BaseException:
    print('EXC')
try:
    print("Handling", d.popleft())
except BaseException:
    print('EXC')
try:
    import bisect
except BaseException:
    print('EXC')
try:
    scores = [(100, 'perl'), (200, 'tcl'), (400, 'lua'), (500, 'python')]
except BaseException:
    print('EXC')
try:
    bisect.insort(scores, (300, 'ruby'))
except BaseException:
    print('EXC')
try:
    scores
except BaseException:
    print('EXC')
try:
    from heapq import heapify, heappop, heappush
except BaseException:
    print('EXC')
try:
    data = [1, 3, 5, 7, 9, 2, 4, 6, 8, 0]
except BaseException:
    print('EXC')
try:
    heapify(data)                      # rearrange the list into heap order
except BaseException:
    print('EXC')
try:
    heappush(data, -5)                 # add a new entry
except BaseException:
    print('EXC')
try:
    [heappop(data) for i in range(3)]  # fetch the three smallest entries
except BaseException:
    print('EXC')
try:
    from decimal import *
except BaseException:
    print('EXC')
try:
    round(Decimal('0.70') * Decimal('1.05'), 2)
except BaseException:
    print('EXC')
try:
    round(.70 * 1.05, 2)
except BaseException:
    print('EXC')
try:
    Decimal('1.00') % Decimal('.10')
except BaseException:
    print('EXC')
try:
    1.00 % 0.10
except BaseException:
    print('EXC')
try:
    sum([Decimal('0.1')]*10) == Decimal('1.0')
except BaseException:
    print('EXC')
try:
    0.1 + 0.1 + 0.1 + 0.1 + 0.1 + 0.1 + 0.1 + 0.1 + 0.1 + 0.1 == 1.0
except BaseException:
    print('EXC')
try:
    getcontext().prec = 36
except BaseException:
    print('EXC')
try:
    Decimal(1) / Decimal(7)
except BaseException:
    print('EXC')
