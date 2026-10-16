# corpus_v4 tutorial page: introduction
# source: https://docs.python.org/3.12/tutorial/introduction.html
# sha256(HTML): 63d76a1173c4a909d060bd144c8d4f27340032907d41c2ba38f3bb4763ce28ff
# ejemplos doctest: 114 (excluidos 10 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    2 + 2
except BaseException:
    print('EXC')
try:
    50 - 5*6
except BaseException:
    print('EXC')
try:
    (50 - 5*6) / 4
except BaseException:
    print('EXC')
try:
    8 / 5  # division always returns a floating-point number
except BaseException:
    print('EXC')
try:
    17 / 3  # classic division returns a float
except BaseException:
    print('EXC')
try:
    17 // 3  # floor division discards the fractional part
except BaseException:
    print('EXC')
try:
    17 % 3  # the % operator returns the remainder of the division
except BaseException:
    print('EXC')
try:
    5 * 3 + 2  # floored quotient * divisor + remainder
except BaseException:
    print('EXC')
try:
    5 ** 2  # 5 squared
except BaseException:
    print('EXC')
try:
    2 ** 7  # 2 to the power of 7
except BaseException:
    print('EXC')
try:
    width = 20
except BaseException:
    print('EXC')
try:
    height = 5 * 9
except BaseException:
    print('EXC')
try:
    width * height
except BaseException:
    print('EXC')
try:
    n  # try to access an undefined variable
except BaseException:
    print('EXC')
try:
    4 * 3.75 - 1
except BaseException:
    print('EXC')
try:
    tax = 12.5 / 100
except BaseException:
    print('EXC')
try:
    price = 100.50
except BaseException:
    print('EXC')
try:
    price * tax
except BaseException:
    print('EXC')
try:
    price + _
except BaseException:
    print('EXC')
try:
    round(_, 2)
except BaseException:
    print('EXC')
try:
    'spam eggs'  # single quotes
except BaseException:
    print('EXC')
try:
    "Paris rabbit got your back :)! Yay!"  # double quotes
except BaseException:
    print('EXC')
try:
    '1975'  # digits and numerals enclosed in quotes are also strings
except BaseException:
    print('EXC')
try:
    'doesn\'t'  # use \' to escape the single quote...
except BaseException:
    print('EXC')
try:
    "doesn't"  # ...or use double quotes instead
except BaseException:
    print('EXC')
try:
    '"Yes," they said.'
except BaseException:
    print('EXC')
try:
    "\"Yes,\" they said."
except BaseException:
    print('EXC')
try:
    '"Isn\'t," they said.'
except BaseException:
    print('EXC')
try:
    s = 'First line.\nSecond line.'  # \n means newline
except BaseException:
    print('EXC')
try:
    s  # without print(), special characters are included in the string
except BaseException:
    print('EXC')
try:
    print(s)  # with print(), special characters are interpreted, so \n produces new line
except BaseException:
    print('EXC')
try:
    print('C:\some\name')  # here \n means newline!
except BaseException:
    print('EXC')
try:
    print(r'C:\some\name')  # note the r before the quote
except BaseException:
    print('EXC')
try:
    print("""\
    Usage: thingy [OPTIONS]
    -h                        Display this usage message
    -H hostname               Hostname to connect to
    """)
except BaseException:
    print('EXC')
try:
    3 * 'un' + 'ium'
except BaseException:
    print('EXC')
try:
    'Py' 'thon'
except BaseException:
    print('EXC')
try:
    text = ('Put several strings within parentheses '
    'to have them joined together.')
except BaseException:
    print('EXC')
try:
    text
except BaseException:
    print('EXC')
try:
    prefix = 'Py'
except BaseException:
    print('EXC')
try:
    prefix + 'thon'
except BaseException:
    print('EXC')
try:
    word = 'Python'
except BaseException:
    print('EXC')
try:
    word[0]  # character in position 0
except BaseException:
    print('EXC')
try:
    word[5]  # character in position 5
except BaseException:
    print('EXC')
try:
    word[-1]  # last character
except BaseException:
    print('EXC')
try:
    word[-2]  # second-last character
except BaseException:
    print('EXC')
try:
    word[-6]
except BaseException:
    print('EXC')
try:
    word[0:2]  # characters from position 0 (included) to 2 (excluded)
except BaseException:
    print('EXC')
try:
    word[2:5]  # characters from position 2 (included) to 5 (excluded)
except BaseException:
    print('EXC')
try:
    word[:2]   # character from the beginning to position 2 (excluded)
except BaseException:
    print('EXC')
try:
    word[4:]   # characters from position 4 (included) to the end
except BaseException:
    print('EXC')
try:
    word[-2:]  # characters from the second-last (included) to the end
except BaseException:
    print('EXC')
try:
    word[:2] + word[2:]
except BaseException:
    print('EXC')
try:
    word[:4] + word[4:]
except BaseException:
    print('EXC')
try:
    word[42]  # the word only has 6 characters
except BaseException:
    print('EXC')
try:
    word[4:42]
except BaseException:
    print('EXC')
try:
    word[42:]
except BaseException:
    print('EXC')
try:
    word[0] = 'J'
except BaseException:
    print('EXC')
try:
    word[2:] = 'py'
except BaseException:
    print('EXC')
try:
    'J' + word[1:]
except BaseException:
    print('EXC')
try:
    word[:2] + 'py'
except BaseException:
    print('EXC')
try:
    s = 'supercalifragilisticexpialidocious'
except BaseException:
    print('EXC')
try:
    len(s)
except BaseException:
    print('EXC')
try:
    squares = [1, 4, 9, 16, 25]
except BaseException:
    print('EXC')
try:
    squares
except BaseException:
    print('EXC')
try:
    squares[0]  # indexing returns the item
except BaseException:
    print('EXC')
try:
    squares[-1]
except BaseException:
    print('EXC')
try:
    squares[-3:]  # slicing returns a new list
except BaseException:
    print('EXC')
try:
    squares + [36, 49, 64, 81, 100]
except BaseException:
    print('EXC')
try:
    cubes = [1, 8, 27, 65, 125]  # something's wrong here
except BaseException:
    print('EXC')
try:
    4 ** 3  # the cube of 4 is 64, not 65!
except BaseException:
    print('EXC')
try:
    cubes[3] = 64  # replace the wrong value
except BaseException:
    print('EXC')
try:
    cubes
except BaseException:
    print('EXC')
try:
    cubes.append(216)  # add the cube of 6
except BaseException:
    print('EXC')
try:
    cubes.append(7 ** 3)  # and the cube of 7
except BaseException:
    print('EXC')
try:
    cubes
except BaseException:
    print('EXC')
try:
    rgb = ["Red", "Green", "Blue"]
except BaseException:
    print('EXC')
try:
    rgba = rgb
except BaseException:
    print('EXC')
try:
    id(rgb) == id(rgba)  # they reference the same object
except BaseException:
    print('EXC')
try:
    rgba.append("Alph")
except BaseException:
    print('EXC')
try:
    rgb
except BaseException:
    print('EXC')
try:
    correct_rgba = rgba[:]
except BaseException:
    print('EXC')
try:
    correct_rgba[-1] = "Alpha"
except BaseException:
    print('EXC')
try:
    correct_rgba
except BaseException:
    print('EXC')
try:
    rgba
except BaseException:
    print('EXC')
try:
    letters = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
except BaseException:
    print('EXC')
try:
    letters
except BaseException:
    print('EXC')
try:
    letters[2:5] = ['C', 'D', 'E']
except BaseException:
    print('EXC')
try:
    letters
except BaseException:
    print('EXC')
try:
    letters[2:5] = []
except BaseException:
    print('EXC')
try:
    letters
except BaseException:
    print('EXC')
try:
    letters[:] = []
except BaseException:
    print('EXC')
try:
    letters
except BaseException:
    print('EXC')
try:
    letters = ['a', 'b', 'c', 'd']
except BaseException:
    print('EXC')
try:
    len(letters)
except BaseException:
    print('EXC')
try:
    a = ['a', 'b', 'c']
except BaseException:
    print('EXC')
try:
    n = [1, 2, 3]
except BaseException:
    print('EXC')
try:
    x = [a, n]
except BaseException:
    print('EXC')
try:
    x
except BaseException:
    print('EXC')
try:
    x[0]
except BaseException:
    print('EXC')
try:
    x[0][1]
except BaseException:
    print('EXC')
try:
    a, b = 0, 1
except BaseException:
    print('EXC')
try:
    i = 256*256
except BaseException:
    print('EXC')
try:
    print('The value of i is', i)
except BaseException:
    print('EXC')
try:
    a, b = 0, 1
except BaseException:
    print('EXC')
