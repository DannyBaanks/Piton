import string
def titular(frase, sep):
    return string.capwords(frase, sep)
print(titular("hola-mundo", "-"))
