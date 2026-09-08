import sys

def saludar(nombre):
    if not nombre:
        return "¿y tú quién eres alv?"
    else:
        return "qué onda " + nombre

personas = ["Danny", "Ada", ""]
for indice, persona in enumerate(personas):
    print(indice, saludar(persona))

print("argumentos:", sys.argv[1:])
