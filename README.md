# AUTO

Локальний реєстр автомобілів та іншої колісної техніки. Python 3.12,
Streamlit, SQLModel, SQLite, Alembic. Реальні дані до репозиторію не додаються.

## Windows PowerShell

Усі команди виконуються з `C:\Users\dpetr\Projects\AUTO`; активація venv не потрібна.

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
& .\.venv\Scripts\python.exe -m auto.cli migrate
& .\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

`migrate` створює нову порожню БД і початкові класифікатори. Автомобілі,
марки, моделі, підрозділи й персонал автоматично не створюються.
Типова БД: `data/auto.sqlite3`. Інший шлях задається змінною `AUTO_DB_PATH`.
Запуск UI не змінює схему: міграції виконуються окремою командою.

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
```

Залежності та інструменти тестування зафіксовано в `requirements.lock`.
Тести створюють окремі тимчасові БД та не використовують робочу базу.
Межі реалізації та план описано в [docs/architecture.md](docs/architecture.md).
