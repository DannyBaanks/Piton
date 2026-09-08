import asyncio

type Nombre = str
type Conteo = dict[str, int]

def saludar(nombre: Nombre) -> Nombre:
    return f"hola {nombre}"

def contar_elementos(items: list[str]) -> Conteo:
    conteo: Conteo = {}
    for item in items:
        match item:
            case "Danny":
                conteo[item] = 100
            case _:
                conteo[item] = len(item)
    return conteo

class Caja[T]:
    def __init__(self, valor: T):
        self.valor = valor

    def obtener(self) -> T:
        return self.valor

async def main():
    nombre: Nombre = "Danny"
    print(nombre)

    elementos = ["Danny", "x", "Danny", "yy"]
    conteo = contar_elementos(elementos)
    print(conteo)

    caja = Caja[int](42)
    print(caja.obtener())

    match nombre:
        case "Danny":
            print("es Danny")
        case _:
            print("otro")

asyncio.run(main())
