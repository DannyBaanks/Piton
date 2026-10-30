import asyncio

async def foo():
    return 42

async def main():
    print(await foo())

asyncio.run(main())
