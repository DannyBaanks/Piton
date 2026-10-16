# corpus_v4 tutorial page: stdlib
# source: https://docs.python.org/3.12/tutorial/stdlib.html
# sha256(HTML): e44b16728e02179b59e3281827f4f6cc346da5330c07b917388afb5f7bbdb00d
# ejemplos doctest: 55 (excluidos 3 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    import os
except BaseException:
    print('EXC')
try:
    os.getcwd()      # Return the current working directory
except BaseException:
    print('EXC')
try:
    os.chdir('/server/accesslogs')   # Change current working directory
except BaseException:
    print('EXC')
try:
    os.system('mkdir today')   # Run the command mkdir in the system shell
except BaseException:
    print('EXC')
try:
    import os
except BaseException:
    print('EXC')
try:
    dir(os)
except BaseException:
    print('EXC')
try:
    help(os)
except BaseException:
    print('EXC')
try:
    import shutil
except BaseException:
    print('EXC')
try:
    shutil.copyfile('data.db', 'archive.db')
except BaseException:
    print('EXC')
try:
    shutil.move('/build/executables', 'installdir')
except BaseException:
    print('EXC')
try:
    import glob
except BaseException:
    print('EXC')
try:
    glob.glob('*.py')
except BaseException:
    print('EXC')
try:
    sys.stderr.write('Warning, log file not found starting a new one\n')
except BaseException:
    print('EXC')
try:
    import re
except BaseException:
    print('EXC')
try:
    re.findall(r'\bf[a-z]*', 'which foot or hand fell fastest')
except BaseException:
    print('EXC')
try:
    re.sub(r'(\b[a-z]+) \1', r'\1', 'cat in the the hat')
except BaseException:
    print('EXC')
try:
    'tea for too'.replace('too', 'two')
except BaseException:
    print('EXC')
try:
    import math
except BaseException:
    print('EXC')
try:
    math.cos(math.pi / 4)
except BaseException:
    print('EXC')
try:
    math.log(1024, 2)
except BaseException:
    print('EXC')
try:
    import random
except BaseException:
    print('EXC')
try:
    random.choice(['apple', 'pear', 'banana'])
except BaseException:
    print('EXC')
try:
    random.sample(range(100), 10)   # sampling without replacement
except BaseException:
    print('EXC')
try:
    random.random()    # random float
except BaseException:
    print('EXC')
try:
    random.randrange(6)    # random integer chosen from range(6)
except BaseException:
    print('EXC')
try:
    import statistics
except BaseException:
    print('EXC')
try:
    data = [2.75, 1.75, 1.25, 0.25, 0.5, 1.25, 3.5]
except BaseException:
    print('EXC')
try:
    statistics.mean(data)
except BaseException:
    print('EXC')
try:
    statistics.median(data)
except BaseException:
    print('EXC')
try:
    statistics.variance(data)
except BaseException:
    print('EXC')
try:
    from urllib.request import urlopen
except BaseException:
    print('EXC')
try:
    import smtplib
except BaseException:
    print('EXC')
try:
    server = smtplib.SMTP('localhost')
except BaseException:
    print('EXC')
try:
    server.sendmail('soothsayer@example.org', 'jcaesar@example.org',
    """To: jcaesar@example.org
    From: soothsayer@example.org
    
    Beware the Ides of March.
    """)
except BaseException:
    print('EXC')
try:
    server.quit()
except BaseException:
    print('EXC')
try:
    from datetime import date
except BaseException:
    print('EXC')
try:
    now = date.today()
except BaseException:
    print('EXC')
try:
    now
except BaseException:
    print('EXC')
try:
    now.strftime("%m-%d-%y. %d %b %Y is a %A on the %d day of %B.")
except BaseException:
    print('EXC')
try:
    birthday = date(1964, 7, 31)
except BaseException:
    print('EXC')
try:
    age = now - birthday
except BaseException:
    print('EXC')
try:
    age.days
except BaseException:
    print('EXC')
try:
    import zlib
except BaseException:
    print('EXC')
try:
    s = b'witch which has which witches wrist watch'
except BaseException:
    print('EXC')
try:
    len(s)
except BaseException:
    print('EXC')
try:
    t = zlib.compress(s)
except BaseException:
    print('EXC')
try:
    len(t)
except BaseException:
    print('EXC')
try:
    zlib.decompress(t)
except BaseException:
    print('EXC')
try:
    zlib.crc32(s)
except BaseException:
    print('EXC')
try:
    from timeit import Timer
except BaseException:
    print('EXC')
try:
    Timer('t=a; a=b; b=t', 'a=1; b=2').timeit()
except BaseException:
    print('EXC')
try:
    Timer('a,b = b,a', 'a=1; b=2').timeit()
except BaseException:
    print('EXC')
