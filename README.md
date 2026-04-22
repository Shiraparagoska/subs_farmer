# VK Помощник (Desktop)

## 1) Установка зависимостей

```powershell
cd "C:\Users\Владислав Местяшов\Desktop\subs_farmer"
python -m pip install -r requirements.txt
```

## 2) Запуск приложения (тест входа по токену)

```powershell
cd "C:\Users\Владислав Местяшов\Desktop\subs_farmer"
.\run.ps1
```

Или напрямую:

```powershell
python -m app.main
```

## 3) Проверка токена в интерфейсе

1. Убедитесь, что токен лежит в `access_tokens.txt` (1 токен на строку).
2. Во вкладке `Аккаунты` нажмите `Обновить список`.
3. Нажмите `Проверить токены`.
4. В колонке `Статус`/`Ошибка` увидите результат проверки через VK API.

## 4) Сборка `.exe` для Windows

```powershell
cd "C:\Users\Владислав Местяшов\Desktop\subs_farmer"
.\build.ps1
```

После сборки исполняемый файл будет здесь:

- `dist\vk-helper\vk-helper.exe`

## Примечания

- Токены не публикуйте и не коммитьте в репозиторий.
- Проверка токена использует официальный вызов VK API: `users.get`.
