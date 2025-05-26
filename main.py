import signal
from services.bot_service import run_bot

def handle_exit(signum, frame):
    """Обработчик сигналов завершения"""
    print("\nЗавершение работы бота...")
    exit(0)

def main():
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Запускаем бота
    run_bot()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем")
    except Exception as e:
        print(f"\nПроизошла ошибка: {e}") 