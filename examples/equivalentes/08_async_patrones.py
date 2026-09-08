import asyncio

type Nombres = list[str]

async def duplicar(valor):
    await asyncio.sleep(0)
    return valor * 2

def describir(valor):
    match valor:
        case {"nombre": nombre, "activo": True}:
            return f"activo: {nombre}"
        case [primero, *resto] if resto:
            cantidad = len(resto)
            return f"lista: {primero}+{cantidad}"
        case None:
            return "nada"
        case _:
            return "otro"

def crear_contador():
    total = 0

    def incrementar():
        nonlocal total
        total += 1
        return total

    return incrementar

async def principal():
    datos = {"temporal": True}
    assert datos["temporal"] is True
    del datos["temporal"]
    assert "temporal" not in datos

    print(describir({"nombre": "Danny", "activo": True}))
    print(describir([1, 2, 3]))
    print(describir(None))

    contador = crear_contador()
    contador()
    print(await duplicar(contador()))

asyncio.run(principal())
