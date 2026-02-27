import asyncio
from pymax.core import SocketMaxClient
from pymax.payloads import UserAgentPayload

async def main():
    # Для входа по номеру телефона
    phone_number = str(input('phone: '))
    
    client = SocketMaxClient(
        phone=phone_number,
        work_dir="./cache",
        headers=UserAgentPayload(device_type="DESKTOP"),
    )

    # Запускаем клиента асинхронно
    await client.start()
    await client.close()
    
    # Теперь можно обращаться к токену
    print(f"Token: {client._token}")

# Запуск программы
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass