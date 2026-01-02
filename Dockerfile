# Використовуємо офіційний Python образ
FROM python:3.11-slim

# Встановлюємо робочу директорію
WORKDIR /app

# Встановлюємо системні залежності
RUN apt-get update && apt-get install -y \
    gcc \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Копіюємо файл з залежностями
COPY requirements.txt .

# Встановлюємо Python залежності
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо код бота
COPY . .

# Створюємо директорію для сесії
RUN mkdir -p /app/data

# Встановлюємо volume для збереження сесії
VOLUME ["/app/data"]

# Запускаємо бота
CMD ["python", "-u", "main.py"]