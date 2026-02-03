import asyncio
import json
import logging
import time
import os
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram.ext import JobQueue
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временная зона Москвы
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Состояния для ConversationHandler
ADD_TEXT, ADD_DAY, ADD_TIME, ADD_INTERVAL, ADD_USERS = range(5)
ADD_DAY_CUSTOM, ADD_DAY_CALENDAR = range(5, 7)
RECIPE_NAME, RECIPE_INGREDIENTS = range(2)
MEAL_DAY, MEAL_RECIPE, INGREDIENT_ASSIGNMENT = range(3)
DELETE_CONFIRM = range(1)
EDIT_RECIPE_NAME, EDIT_RECIPE_INGREDIENTS = range(2, 4)
EDIT_PLAN_ASSIGNMENT = range(4)

def get_main_keyboard():
    """Возвращает основную клавиатуру"""
    keyboard = [
        [
            InlineKeyboardButton("➕ Добавить напоминание", callback_data="add_reminder"),
            InlineKeyboardButton("📋 Все напоминания", callback_data="list_reminders")
        ],
        [
            InlineKeyboardButton("👥 Пользователи", callback_data="list_users"),
            InlineKeyboardButton("🍽 Рецепты", callback_data="recipes"),
            InlineKeyboardButton("🗑 Мои напоминания", callback_data="my_reminders_delete")  # Новая кнопка
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def load_users():
    """Загрузка пользователей из файла"""
    file_path = 'users.json'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"Файл {file_path} не найден, создается новый")
        users = {}
        save_users(users)  # Создаем пустой файл
        return users
    except Exception as e:
        logger.error(f"Ошибка загрузки пользователей из {file_path}: {e}")
        return {}

def save_users(users):
    """Сохранение пользователей в файл"""
    file_path = 'users.json'
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        logger.info(f"Пользователи успешно сохранены в {file_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователей в {file_path}: {e}")
        return False

def save_reminders(reminders):
    """Сохранение напоминаний в файл с детальным логированием"""
    file_path = 'reminders.json'
    try:
        data = {}
        urgent_count = 0

        for rid, reminder in reminders.items():
            # Создаем глубокую копию
            data[rid] = reminder.copy()

            # Преобразуем set в list
            for field in ['confirmed_by', 'postponed_by', 'delete_confirmed_by']:
                if field in data[rid] and isinstance(data[rid][field], set):
                    data[rid][field] = list(data[rid][field])

            # Считаем срочные напоминания
            if data[rid].get('urgent_reminders'):
                urgent_count += 1
                logger.info(f"💾 СРОЧНОЕ ДЛЯ СОХРАНЕНИЯ: {rid} - urgent_reminders={data[rid].get('urgent_reminders')}, urgent_until={data[rid].get('urgent_until')}")

        logger.info(f"💾 Сохранение {len(data)} напоминаний, из них срочных: {urgent_count}")

        # Сохраняем в файл
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Проверяем запись
        with open(file_path, 'r', encoding='utf-8') as f:
            saved_data = json.load(f)
            saved_urgent_count = sum(1 for rem in saved_data.values() if rem.get('urgent_reminders'))
            logger.info(f"📖 ПРОВЕРКА: в файле {saved_urgent_count} срочных напоминаний")

            # Детальная проверка конкретного напоминания
            for rid in data.keys():
                if rid in saved_data:
                    saved_rem = saved_data[rid]
                    if saved_rem.get('urgent_reminders'):
                        logger.info(f"📖 ПРОВЕРКА {rid}: urgent_reminders={saved_rem.get('urgent_reminders')}, urgent_until={saved_rem.get('urgent_until')}")

        logger.info(f"✅ Напоминания успешно сохранены в {file_path}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения напоминаний в {file_path}: {e}")
        return False

def load_reminders():
    """Загрузка напоминаний из файла"""
    file_path = 'reminders.json'
    max_retries = 3
    retry_delay = 0.1

    for attempt in range(max_retries):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for reminder in data.values():
                    # Преобразуем списки обратно в set
                    for field in ['confirmed_by', 'postponed_by', 'delete_confirmed_by']:
                        if field in reminder and isinstance(reminder[field], list):
                            reminder[field] = set(reminder[field])

                    # Гарантируем наличие полей для срочного режима
                    if 'urgent_reminders' not in reminder:
                        reminder['urgent_reminders'] = False
                    if 'urgent_until' not in reminder:
                        reminder['urgent_until'] = None
                    if 'last_sent' not in reminder:
                        reminder['last_sent'] = None
                    if 'not_bought_count' not in reminder:
                        reminder['not_bought_count'] = 0

                logger.info(f"📖 Загружено {len(data)} напоминаний из {file_path}")
                return data

        except FileNotFoundError:
            logger.info(f"Файл {file_path} не найден, создается новый")
            reminders = {}
            save_reminders(reminders)
            return reminders

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON в {file_path}: {e}")
            if attempt == max_retries - 1:
                # Создаем резервную копию и новый файл
                backup_path = f"{file_path}.backup.{int(datetime.now().timestamp())}"
                try:
                    import shutil
                    shutil.copy2(file_path, backup_path)
                    logger.info(f"Создана резервная копия: {backup_path}")
                except Exception as backup_error:
                    logger.error(f"Не удалось создать резервную копию: {backup_error}")

                reminders = {}
                save_reminders(reminders)
                return reminders
            else:
                time.sleep(retry_delay)

        except Exception as e:
            logger.error(f"Ошибка загрузки напоминаний из {file_path} (попытка {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return {}
            else:
                time.sleep(retry_delay)

    return {}

def load_message_ids():
    """Загружает сохраненные ID сообщений"""
    try:
        with open('message_ids.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info("Файл message_ids.json не найден, создается новый")
        save_message_ids_to_file({})
        return {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки message_ids: {e}")
        return {}

def save_message_ids_to_file(message_ids):
    """Сохраняет ID сообщений в файл"""
    try:
        with open('message_ids.json', 'w', encoding='utf-8') as f:
            json.dump(message_ids, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Сохранено {len(message_ids)} message_ids в файл")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения message_ids в файл: {e}")

def save_message_id(reminder_id, user_id, message_id):
    """Сохраняет ID сообщения для напоминания и пользователя"""
    try:
        message_ids = load_message_ids()

        # Нормализуем user_id - убеждаемся, что это число
        try:
            user_id_int = int(user_id)
        except (ValueError, TypeError):
            logger.error(f"❌ Неверный формат user_id: {user_id}")
            return

        # Создаем ключ в правильном формате: reminderId_userId
        key = f"{reminder_id}_{user_id_int}"

        message_ids[key] = message_id
        save_message_ids_to_file(message_ids)
        logger.info(f"💾 Сохранен message_id {message_id} для reminder {reminder_id} и пользователя {user_id_int}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения message_id: {e}")

async def delete_old_reminder_messages(application, reminder_id):
    """Удаляет старые сообщения для указанного reminder_id"""
    try:
        message_ids = load_message_ids()
        deleted_count = 0

        # Ищем все сообщения для этого reminder_id
        keys_to_delete = []
        for key, message_id in message_ids.items():
            try:
                # Разделяем ключ на части по последнему подчеркиванию
                # Формат: reminderId_userId
                last_underscore_index = key.rfind('_')
                if last_underscore_index == -1:
                    continue

                key_reminder_id = key[:last_underscore_index]
                user_id_str = key[last_underscore_index + 1:]

                if key_reminder_id == str(reminder_id):
                    try:
                        # Преобразуем user_id в число
                        user_id = int(user_id_str)
                        await application.bot.delete_message(
                            chat_id=user_id,
                            message_id=message_id
                        )
                        keys_to_delete.append(key)
                        deleted_count += 1
                        logger.info(f"🗑 Удалено старое сообщение {message_id} для пользователя {user_id}")
                    except Exception as e:
                        if "Chat not found" in str(e):
                            logger.info(f"🗑 Чат не найден для пользователя {user_id}, удаляем запись из базы")
                            keys_to_delete.append(key)
                        elif "Message to delete not found" in str(e):
                            logger.info(f"🗑 Сообщение уже удалено для пользователя {user_id}, удаляем запись из базы")
                            keys_to_delete.append(key)
                        else:
                            logger.error(f"❌ Ошибка удаления сообщения {message_id} для пользователя {user_id}: {e}")
                            # Если сообщение не найдено (уже удалено и т.д.), все равно удаляем из базы
                            keys_to_delete.append(key)

            except Exception as e:
                logger.error(f"❌ Ошибка обработки ключа {key}: {e}")
                continue

        # Удаляем обработанные записи из базы
        for key in keys_to_delete:
            del message_ids[key]

        if keys_to_delete:
            save_message_ids_to_file(message_ids)

        logger.info(f"✅ Удалено {deleted_count} старых сообщений для reminder {reminder_id}")
        return deleted_count

    except Exception as e:
        logger.error(f"❌ Ошибка в delete_old_reminder_messages: {e}")
        return 0

def load_recipes():
    """Загрузка рецептов из файла"""
    file_path = 'recipes.json'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"Файл {file_path} не найден, создается новый")
        recipes = {}
        save_recipes(recipes)  # Создаем пустой файл
        return recipes
    except Exception as e:
        logger.error(f"Ошибка загрузки рецептов из {file_path}: {e}")
        return {}

def save_recipes(recipes):
    """Сохранение рецептов в файл"""
    file_path = 'recipes.json'
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(recipes, f, ensure_ascii=False, indent=2)
        logger.info(f"Рецепты успешно сохранены в {file_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения рецептов в {file_path}: {e}")
        return False

def load_meal_plans():
    """Загрузка планов питания из файла"""
    file_path = 'meal_plans.json'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"Файл {file_path} не найден, создается новый")
        meal_plans = {}
        save_meal_plans(meal_plans)  # Создаем пустой файл
        return meal_plans
    except Exception as e:
        logger.error(f"Ошибка загрузки планов питания из {file_path}: {e}")
        return {}

def save_meal_plans(meal_plans):
    """Сохранение планов питания в файл"""
    file_path = 'meal_plans.json'
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(meal_plans, f, ensure_ascii=False, indent=2)
        logger.info(f"Планы питания успешно сохранены в {file_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения планов питания в {file_path}: {e}")
        return False

WEEK_DAYS = {
    'mon': 'Понедельник',
    'tue': 'Вторник',
    'wed': 'Среда',
    'thu': 'Четверг',
    'fri': 'Пятница',
    'sat': 'Суббота',
    'sun': 'Воскресенье'
}

NOTIFICATION_TIMES = {
    '1_day': 'За 1 день',
    '2_days': 'За 2 дня',
    '3_days': 'За 3 дня',
    '1_week': 'За неделю'
}

async def cleanup_old_messages(application, current_reminders):
    """Удаляет сообщения для напоминаний, которых больше нет в актуальном списке"""
    try:
        message_ids = load_message_ids()
        if not message_ids:
            return 0

        deleted_count = 0
        current_reminder_ids = set(current_reminders.keys())

        # Создаем копию ключей для безопасного удаления
        keys_to_check = list(message_ids.keys())

        for key in keys_to_check:
            try:
                # Разделяем ключ на части
                parts = key.split('_')
                if len(parts) < 2:
                    logger.warning(f"⚠️ Неверный формат ключа: {key}")
                    # Удаляем некорректную запись
                    del message_ids[key]
                    continue

                # Последняя часть - user_id
                reminder_id = '_'.join(parts[:-1])  # Все части кроме последней
                user_id_str = parts[-1]  # Последняя часть

                # Пытаемся преобразовать user_id в число
                try:
                    user_id = int(user_id_str)
                except ValueError:
                    logger.warning(f"⚠️ Неверный формат user_id в ключе {key}: {user_id_str}")
                    # Удаляем некорректную запись
                    del message_ids[key]
                    continue

                # Если напоминание больше не существует в актуальном списке
                if reminder_id not in current_reminder_ids:
                    try:
                        message_id = message_ids[key]
                        await application.bot.delete_message(
                            chat_id=user_id,
                            message_id=message_id
                        )
                        del message_ids[key]
                        deleted_count += 1
                        logger.info(f"🗑 Удалено неактуальное сообщение {message_id} для пользователя {user_id} (reminder {reminder_id} не существует)")
                    except Exception as e:
                        if "Chat not found" in str(e):
                            # Чат не найден - просто удаляем запись из базы
                            logger.info(f"🗑 Чат не найден для пользователя {user_id}, удаляем запись из базы")
                            del message_ids[key]
                        elif "Message to delete not found" in str(e):
                            # Сообщение уже удалено - удаляем запись из базы
                            logger.info(f"🗑 Сообщение уже удалено для пользователя {user_id}, удаляем запись из базы")
                            del message_ids[key]
                        else:
                            logger.error(f"❌ Ошибка удаления неактуального сообщения {message_ids[key]} для пользователя {user_id}: {e}")
                            # Удаляем запись из базы в любом случае
                            del message_ids[key]

            except Exception as e:
                logger.error(f"❌ Ошибка обработки ключа {key} в cleanup_old_messages: {e}")
                continue

        # Сохраняем изменения
        if deleted_count > 0:
            save_message_ids_to_file(message_ids)
            logger.info(f"✅ Удалено {deleted_count} неактуальных сообщений")

        return deleted_count

    except Exception as e:
        logger.error(f"❌ Ошибка в cleanup_old_messages: {e}")
        return 0

async def cleanup_invalid_message_ids(application):
    """Очищает базу message_ids от некорректных записей"""
    try:
        message_ids = load_message_ids()
        original_count = len(message_ids)
        deleted_count = 0

        keys_to_delete = []

        for key, message_id in message_ids.items():
            try:
                # Проверяем формат ключа
                parts = key.split('_')
                if len(parts) < 2:
                    logger.warning(f"⚠️ Неверный формат ключа: {key}")
                    keys_to_delete.append(key)
                    continue

                # Проверяем user_id
                user_id_str = parts[-1]
                try:
                    user_id = int(user_id_str)
                    # Проверяем, что user_id разумной длины (не более 20 символов)
                    if len(user_id_str) > 20:
                        logger.warning(f"⚠️ Слишком длинный user_id: {user_id_str} в ключе {key}")
                        keys_to_delete.append(key)
                        continue
                except ValueError:
                    logger.warning(f"⚠️ Неверный формат user_id: {user_id_str} в ключе {key}")
                    keys_to_delete.append(key)
                    continue

            except Exception as e:
                logger.error(f"❌ Ошибка проверки ключа {key}: {e}")
                keys_to_delete.append(key)
                continue

        # Удаляем некорректные записи
        for key in keys_to_delete:
            del message_ids[key]
            deleted_count += 1

        if deleted_count > 0:
            save_message_ids_to_file(message_ids)
            logger.info(f"✅ Очищено {deleted_count} некорректных записей из {original_count}")

        return deleted_count

    except Exception as e:
        logger.error(f"❌ Ошибка в cleanup_invalid_message_ids: {e}")
        return 0

async def cleanup_message_ids_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для очистки базы message_ids"""
    try:
        message_ids = load_message_ids()
        original_count = len(message_ids)

        # Удаляем все записи с некорректными ключами
        keys_to_delete = []
        for key in message_ids.keys():
            parts = key.split('_')
            if len(parts) < 2:
                keys_to_delete.append(key)
                logger.info(f"🗑 Удален некорректный ключ: {key}")
                continue

            # Проверяем user_id
            user_id_str = parts[-1]
            try:
                user_id = int(user_id_str)
                # Проверяем разумную длину user_id
                if len(user_id_str) > 20:
                    keys_to_delete.append(key)
                    logger.info(f"🗑 Удален ключ с длинным user_id: {key}")
            except ValueError:
                keys_to_delete.append(key)
                logger.info(f"🗑 Удален ключ с нечисловым user_id: {key}")

        for key in keys_to_delete:
            del message_ids[key]

        save_message_ids_to_file(message_ids)

        # Также запускаем очистку некорректных записей
        invalid_cleaned = await cleanup_invalid_message_ids(context.application)

        await update.message.reply_text(
            f"✅ База message_ids очищена! Удалено {len(keys_to_delete)} из {original_count} записей.\n"
            f"🧹 Дополнительно очищено {invalid_cleaned} некорректных записей."
        )

    except Exception as e:
        logger.error(f"❌ Ошибка очистки message_ids: {e}")
        await update.message.reply_text("❌ Ошибка при очистке базы message_ids")

async def cleanup_past_meal_plans_and_reminders(application):
    """Автоматически удаляет напоминания с прошедшей датой приготовления и создает новые планы"""
    try:
        reminders = load_reminders()
        meal_plans = load_meal_plans()
        current_time = datetime.now(MOSCOW_TZ)

        deleted_count = 0
        created_count = 0

        # Находим ВСЕ напоминания с прошедшей датой приготовления (не только ингредиенты)
        past_reminders = []
        for reminder_id, reminder in reminders.items():
            try:
                # Для ингредиентов проверяем meal_date
                if reminder.get('type') == 'ingredient':
                    meal_date_str = reminder.get('meal_date')
                    if meal_date_str:
                        try:
                            # Парсим дату приготовления (формат: DD.MM.YYYY)
                            meal_date = datetime.strptime(meal_date_str, '%d.%m.%Y').replace(tzinfo=MOSCOW_TZ)

                            # Если дата приготовления уже прошла (учитываем начало дня)
                            if meal_date.date() < current_time.date():
                                past_reminders.append((reminder_id, reminder))
                        except ValueError as e:
                            logger.error(f"❌ Ошибка парсинга даты приготовления {meal_date_str}: {e}")
                            continue

                # Для обычных напоминаний проверяем, не прошло ли 24 часа с последней отправки (для однократных)
                else:
                    # Если напоминание однократное и время прошло более 24 часов назад
                    if reminder.get('interval_days', 0) == 0:
                        last_sent = reminder.get('last_sent')
                        if last_sent:
                            last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                            hours_passed = (current_time - last_sent_time).total_seconds() / 3600
                            if hours_passed >= 24:
                                past_reminders.append((reminder_id, reminder))

            except Exception as e:
                logger.error(f"❌ Ошибка проверки напоминания {reminder_id}: {e}")
                continue

        # Собираем уникальные meal_plan_id для создания новых планов
        processed_plans = set()

        for reminder_id, reminder in past_reminders:
            try:
                # УДАЛЯЕМ СООБЩЕНИЯ ПЕРЕД УДАЛЕНИЕМ НАПОМИНАНИЯ
                await delete_old_reminder_messages(application, reminder_id)

                # Удаляем напоминание
                del reminders[reminder_id]
                deleted_count += 1
                logger.info(f"🗑 Удалено напоминание с прошедшей датой: {reminder_id}")

                # Для ингредиентов добавляем meal_plan_id в обработанные
                if reminder.get('type') == 'ingredient':
                    meal_plan_id = reminder.get('meal_plan_id')
                    if meal_plan_id and meal_plan_id not in processed_plans:
                        processed_plans.add(meal_plan_id)

            except Exception as e:
                logger.error(f"❌ Ошибка удаления напоминания {reminder_id}: {e}")
                continue

        # СОЗДАЕМ НОВЫЕ ПЛАНЫ ДЛЯ УДАЛЕННЫХ НАПОМИНАНИЙ ИНГРЕДИЕНТОВ
        # ИСПОЛЬЗУЕМ КОПИЮ processed_plans ДЛЯ ИТЕРАЦИИ, ТАК КАК МНОЖЕСТВО БУДЕТ ИЗМЕНЯТЬСЯ
        for meal_plan_id in list(processed_plans):
            if meal_plans.get(meal_plan_id):
                new_plan_id = await create_next_week_meal_plan(application, meal_plan_id)
                if new_plan_id:
                    created_count += 1
                    logger.info(f"📅 Создан новый план на следующую неделю: {new_plan_id}")
                    # УДАЛЯЕМ ИЗ ОБРАБОТАННЫХ, ЧТОБЫ ИЗБЕЖАТЬ ПОВТОРНОЙ ОБРАБОТКИ
                    processed_plans.discard(meal_plan_id)

        # Сохраняем изменения
        if deleted_count > 0:
            if not save_reminders(reminders):
                logger.error("❌ Ошибка при сохранении напоминаний после очистки")
            else:
                logger.info(f"✅ Автоматическая очистка: удалено {deleted_count} напоминаний, создано {created_count} новых планов")

        return deleted_count

    except Exception as e:
        logger.error(f"❌ Ошибка в cleanup_past_meal_plans_and_reminders: {e}")
        return 0

async def main():
    logger.info("🚀 Запуск бота...")

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан")

    job_queue = JobQueue()

    application = (
        Application.builder()
        .token(token)
        .job_queue(job_queue)
        .build()
    )
    logger.info("✅ Приложение создано, начинаем регистрацию обработчиков...")

    # Создаем файлы, если они отсутствуют
    for load_func, file_name in [
        (load_users, 'users.json'),
        (load_reminders, 'reminders.json'),
        (load_recipes, 'recipes.json'),
        (load_meal_plans, 'meal_plans.json'),
        (load_message_ids, 'message_ids.json')

    ]:
        load_func()

    # СНАЧАЛА регистрируем ConversationHandler
    application.add_handler(remind_conv_handler)
    application.add_handler(add_conv_handler)
    application.add_handler(recipe_conv_handler)
    application.add_handler(meal_plan_conv_handler)
    application.add_handler(delete_conv_handler)
    application.add_handler(edit_recipe_conv_handler)
    application.add_handler(edit_plan_conv_handler)

    # ПОТОМ обычные обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("remind", start_add_reminder))
    application.add_handler(CommandHandler("recipes", recipes_command))
    application.add_handler(CommandHandler("cleanup_ids", cleanup_message_ids_command))

    # Обработчики для кнопок "Все рецепты" и "Все планы"
    application.add_handler(CallbackQueryHandler(list_recipes, pattern="^list_recipes$"))
    application.add_handler(CallbackQueryHandler(list_meal_plans, pattern="^list_meal_plans$"))

    # И другие callback обработчики
    application.add_handler(CallbackQueryHandler(handle_notification_selection, pattern="^notify_"))
    application.add_handler(CallbackQueryHandler(handle_assignment_completion, pattern="^(setup_notifications|save_without_notifications|continue_assignment)$"))
    application.add_handler(CallbackQueryHandler(handle_reminders_pagination, pattern="^(regular_page_|ingredients_page_|current_page)"))
    application.add_handler(CallbackQueryHandler(handle_reminders_list_switch, pattern="^switch_to_"))
    application.add_handler(CallbackQueryHandler(handle_delete_reminder, pattern="^delete_reminder_"))
    application.add_handler(CallbackQueryHandler(handle_custom_day_selection, pattern="^(show_calendar|input_days|back_to_day_selection)$"))
    application.add_handler(CallbackQueryHandler(handle_calendar_selection, pattern="^(cal_|back_to_custom_menu)"))
    application.add_handler(CallbackQueryHandler(ignore_callback, pattern="^ignore$"))

    # Обработчик для кнопки "Мои напоминания"
    application.add_handler(CallbackQueryHandler(my_reminders_for_deletion, pattern="^my_reminders_delete$"))

    # Обработчик для выбора напоминания для удаления
    application.add_handler(CallbackQueryHandler(handle_delete_reminder, pattern="^delete_reminder_"))

    # Обработчик для подтверждения удаления
    application.add_handler(CallbackQueryHandler(handle_confirm_delete, pattern="^confirm_delete_"))

    # ВАЖНО: Обработчик для кнопок "Купил" и "Еще не купил" должен быть ДО главного меню
    application.add_handler(CallbackQueryHandler(handle_bought_not_bought, pattern="^(bought_|not_bought_)"))
    application.add_handler(CallbackQueryHandler(handle_back_to_calendar_from_time, pattern="^back_to_calendar_from_time$"))

    # Главный обработчик меню должен быть одним из последних
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^(add_reminder|list_reminders|list_users|recipes|back_to_main|back_to_text_input|back_to_day_selection|back_to_interval|back_to_user_selection|back_to_recipe_name|back_to_recipes)$"))

    # Обработчик отмены
    application.add_handler(CallbackQueryHandler(cancel_reminder, pattern="^cancel_reminder$"))

    # Остальные обработчики...
    application.add_handler(CallbackQueryHandler(handle_user_selection_for_ingredient, pattern="^(select_user_|back_to_assignment)$"))
    application.add_handler(CallbackQueryHandler(handle_ingredient_assignment, pattern="^(assign_ing_|back_to_recipe_selection|finish_assignment)$"))
    application.add_handler(CallbackQueryHandler(edit_recipes_menu, pattern="^edit_recipes$"))
    application.add_handler(CallbackQueryHandler(manage_meal_plans, pattern="^manage_plans$"))
    application.add_handler(CallbackQueryHandler(manage_day_plans, pattern="^manage_day_"))
    application.add_handler(CallbackQueryHandler(edit_meal_plan, pattern="^edit_plan_"))
    application.add_handler(CallbackQueryHandler(start_edit_plan_assignment, pattern="^change_assignees_"))
    application.add_handler(CallbackQueryHandler(handle_edit_plan_assignment, pattern="^(edit_assign_ing_|back_to_edit_plan|finish_edit_assignment)$"))
    application.add_handler(CallbackQueryHandler(handle_change_plan_day, pattern="^change_plan_day_"))
    application.add_handler(CallbackQueryHandler(handle_update_plan_day, pattern="^update_day_"))
    application.add_handler(CallbackQueryHandler(back_to_recipe_name_handler, pattern="^back_to_recipe_name$"))
    application.add_handler(CallbackQueryHandler(back_to_edit_recipe_menu, pattern="^back_to_edit_recipe_menu_"))
    application.add_handler(CallbackQueryHandler(back_to_edit_recipe_menu, pattern="^back_to_edit_recipe_menu$"))
    application.add_handler(CallbackQueryHandler(back_to_edit_plan_handler, pattern="^back_to_edit_plan$"))
    application.add_handler(CallbackQueryHandler(handle_delete_plan, pattern="^delete_plan_"))
    application.add_handler(CallbackQueryHandler(lambda update, context: update.callback_query.answer(), pattern="^ignore$"))

    # ЗАПУСКАЕМ ПРОВЕРКУ ПРОПУЩЕННЫХ НАПОМИНАНИЙ ПРИ СТАРТЕ
    async def send_missed_on_startup(application):
        """Отправляет пропущенные напоминания при запуске бота"""
        try:
            logger.info("🔍 Запуск проверки пропущенных напоминаний при старте...")
            missed_count = await send_missed_reminders(application)
            if missed_count > 0:
                logger.info(f"🚀 При старте отправлено {missed_count} пропущенных напоминаний")
            else:
                logger.info("✅ Пропущенных напоминаний при старте не найдено")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке пропущенных напоминаний при старте: {e}")

    # Запускаем проверку пропущенных напоминаний через 10 секунд после старта
    application.job_queue.run_once(
        lambda context: send_missed_on_startup(application),
        when=10
    )

    application.job_queue.run_once(
        lambda context: cleanup_invalid_message_ids(application),
        when=5
    )

    # Обычная периодическая проверка каждую минуту
    application.job_queue.run_repeating(check_all_reminders, interval=60, first=10)

    # В функции main() добавьте:
    application.job_queue.run_repeating(cleanup_past_meal_plans_and_reminders, interval=3600, first=300)  # Каждый час

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("✅ Бот успешно запущен!")

        # Бесконечный цикл для поддержания работы бота
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")
        raise
    finally:
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            logger.info("🛑 Бот остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")

    return application

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    # ПРОСТАЯ ПРОВЕРКА ДОСТУПА С СООБЩЕНИЕМ:
    if update.effective_user.id not in {721250728, 344934889}:
        await update.message.reply_text("❌ Доступ запрещен")
        return

    """Обработка команды /start"""
    user = update.effective_user
    users = load_users()

    users[str(user.id)] = {
        'username': user.username or user.first_name,
        'first_name': user.first_name,
        'last_name': user.last_name or ''
    }
    if not save_users(users):
        await update.message.reply_text("❌ Ошибка при сохранении пользователя. Проверьте права доступа к файлу users.json.")
        return

    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n"
        "Я бот для совместных покупок и планирования питания.\n\n"
        "Выберите действие:",
        reply_markup=get_main_keyboard()
    )

def parse_datetime(time_str: str, base_date: datetime) -> datetime:
    """Парсинг строки времени в объект datetime с учетом базовой даты и проверкой на прошедшее время"""
    formats = ['%H:%M', '%H.%M', '%H:%M:%S', '%H %M']

    # Нормализуем строку времени
    time_str = time_str.replace('.', ':').replace(' ', ':')

    # Добавляем ведущие нули если нужно
    if len(time_str.split(':')[0]) == 1:
        time_str = '0' + time_str

    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            dt = dt.replace(
                year=base_date.year,
                month=base_date.month,
                day=base_date.day,
                tzinfo=MOSCOW_TZ
            )

            # Проверяем, не прошло ли время сегодня
            current_time = datetime.now(MOSCOW_TZ)
            if dt < current_time:
                # Если время уже прошло сегодня, устанавливаем на завтра
                dt += timedelta(days=1)
                logger.info(f"⏰ Время {time_str} уже прошло, установлено на завтра: {dt.strftime('%d.%m.%Y %H:%M')}")

            return dt
        except ValueError:
            continue

    # Если ни один формат не подошел, выбрасываем понятное исключение
    raise ValueError("Неверный формат времени. Используйте формат ЧЧ:ММ (например, 14:30), ЧЧ.ММ или ЧЧ ММ")

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка callback-запросов главного меню"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "add_reminder":
        return await start_add_reminder(update, context)
    elif data == "list_reminders":
        # Сбрасываем состояние пагинации при входе в список
        context.user_data['reminders_list_type'] = 'regular'
        context.user_data['regular_page'] = 0
        context.user_data['ingredients_page'] = 0
        await list_reminders(update, context)
        return ConversationHandler.END
    elif data == "list_users":
        await list_users(update, context)
        return ConversationHandler.END
    elif data == "recipes":
        await recipes_command(update, context)
        return ConversationHandler.END
    elif data == "back_to_main":
        # Удаляем предыдущее сообщение с инструкцией если оно есть
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END
    elif data == "back_to_text_input":
        # Возврат к вводу текста напоминания
        await query.edit_message_text(
            "📝 Введите текст напоминания:\n(Для отмены введите /cancel)",
            parse_mode='Markdown'
        )
        return ADD_TEXT
    elif data == "back_to_day_selection":
        # ВОЗВРАТ К ВЫБОРУ ДНЯ: только кнопка "Отмена"
        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        await query.edit_message_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_DAY
    elif data == "back_to_interval":
        # Возврат к выбору интервала: кнопки "Назад" и "Отмена"
        keyboard = [
            [InlineKeyboardButton("Однократно", callback_data="interval_0")],
            [InlineKeyboardButton("Каждый день", callback_data="interval_1")],
            [InlineKeyboardButton("Каждые 3 дня", callback_data="interval_3")],
            [InlineKeyboardButton("Каждую неделю", callback_data="interval_7")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_day_selection")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        await query.edit_message_text(
            "🔄 Выберите интервал повторения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_INTERVAL
    elif data == "back_to_user_selection":
        # ВОЗВРАТ К ВЫБОРУ ПОЛЬЗОВАТЕЛЕЙ: кнопки "Назад" и "Отмена"
        await show_user_selection(query, context)
        return ADD_USERS
    elif data == "back_to_recipe_name":
        # Возврат к вводу названия рецепта
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        await query.edit_message_text(
            "🍽 *Создание рецепта*\n\nВведите название рецепта:\n(Для отмены введите /cancel)",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RECIPE_NAME
    elif data == "back_to_recipes":
        # Возврат к меню рецептов
        await recipes_command(update, context)
        return ConversationHandler.END

async def start_add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало создания напоминания с кнопкой отмены"""
    # Очищаем предыдущие данные
    context.user_data.clear()

    if update.callback_query:
        query = update.callback_query
        await query.answer()

        keyboard = [
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        message = await query.edit_message_text(
            "📝 *Создание напоминания*\n\nВведите текст напоминания:\n(Для отмены введите /cancel)",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        logger.info(f"Создали напоминание, instruction_message_id: {message.message_id}")
    else:
        keyboard = [
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        message = await update.message.reply_text(
            "📝 *Создание напоминания*\n\nВведите текст напоминания:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        logger.info(f"Создали напоминание, instruction_message_id: {message.message_id}")

    return ADD_TEXT

async def handle_reminder_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка текста напоминания с удалением предыдущих сообщений"""
    try:
        # Удаляем предыдущее сообщение с инструкцией (сообщение бота "Создание напоминания")
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
                logger.info(f"Удалили сообщение с инструкцией: {instruction_message_id}")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Удаляем сообщение пользователя с текстом напоминания
        try:
            await update.message.delete()
            logger.info("Удалили сообщение пользователя с текстом напоминания")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        context.user_data['reminder_text'] = update.message.text.strip()
        logger.info(f"Текст напоминания получен: {context.user_data['reminder_text']}")

        # Клавиатура для выбора дня (только кнопка "Отмена")
        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        # Отправляем новое сообщение и сохраняем его ID
        message = await update.message.reply_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        logger.info(f"Сохранили новое instruction_message_id: {message.message_id}")

        return ADD_DAY

    except Exception as e:
        logger.error(f"Ошибка в handle_reminder_text: {e}")
        # Если не удалось удалить сообщения, все равно продолжаем
        context.user_data['reminder_text'] = update.message.text.strip()

        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        message = await update.message.reply_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id

        return ADD_DAY
    except Exception as e:
        logger.error(f"Ошибка в handle_reminder_text: {e}")
        # Если не удалось удалить сообщения, все равно продолжаем
        context.user_data['reminder_text'] = update.message.text.strip()

        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_text_input")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        message = await update.message.reply_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id

        return ADD_DAY

async def skip_to_next_available_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка команды /skip для установки времени на +1 минуту с учетом выбранной даты и ночного режима"""
    try:
        # Проверяем, есть ли выбранная дата
        if 'reminder_date' not in context.user_data:
            await update.message.reply_text(
                "❌ Сначала выберите дату напоминания.\n"
                "Используйте кнопки для выбора дня или календарь."
            )
            return ADD_TIME

        current_time = datetime.now(MOSCOW_TZ)
        selected_date = context.user_data['reminder_date']  # Это дата, выбранная пользователем

        # Получаем время для напоминания: текущее время +1 минута
        reminder_time_candidate = current_time + timedelta(minutes=1)

        # Если выбранная дата сегодня - используем вычисленное время
        if selected_date.date() == current_time.date():
            next_available_time = reminder_time_candidate

            # ПРОВЕРКА НОЧНОГО РЕЖИМА (23:00 - 9:00) только если дата сегодня
            if next_available_time.hour >= 23 or next_available_time.hour < 9:
                # Если ночное время, устанавливаем на 9:00 сегодня или завтра
                next_available_time = next_available_time.replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
                if next_available_time <= current_time:
                    next_available_time += timedelta(days=1)

                time_description = f"9:00 {next_available_time.strftime('%d.%m.%Y')}"
            else:
                time_description = f"{next_available_time.strftime('%H:%M')} (через 1 минуту)"

        # Если выбранная дата в будущем - устанавливаем на 10:00 выбранной даты
        else:
            next_available_time = selected_date.replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            time_description = f"10:00 {selected_date.strftime('%d.%m.%Y')}"

        # Сохраняем время в контекст (не меняем дату!)
        context.user_data['reminder_time'] = next_available_time
        logger.info(f"⏰ Время установлено через /skip: {next_available_time} (дата сохранена: {selected_date})")

        # УДАЛЯЕМ СООБЩЕНИЕ С КОМАНДОЙ /SKIP
        try:
            await update.message.delete()
            logger.info("Удалили сообщение с командой /skip")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения /skip: {e}")

        # УДАЛЯЕМ ПРЕДЫДУЩЕЕ СООБЩЕНИЕ С ИНСТРУКЦИЕЙ (ЕСЛИ ЕСТЬ)
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
                logger.info(f"Удалили сообщение с инструкцией: {instruction_message_id}")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Переходим к выбору интервала
        keyboard = [
            [InlineKeyboardButton("Однократно", callback_data="interval_0")],
            [InlineKeyboardButton("Каждый день", callback_data="interval_1")],
            [InlineKeyboardButton("Каждые 3 дня", callback_data="interval_3")],
            [InlineKeyboardButton("Каждую неделю", callback_data="interval_7")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_day_selection")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        # ОТПРАВЛЯЕМ НОВОЕ СООБЩЕНИЕ И СОХРАНЯЕМ ЕГО ID
        message = await update.message.reply_text(
            f"✅ Время установлено на {time_description}\n\n"
            "🔄 Выберите интервал повторения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        logger.info(f"Сохранили новое instruction_message_id: {message.message_id}")

        return ADD_INTERVAL

    except Exception as e:
        logger.error(f"❌ Ошибка в обработке /skip: {e}")

        # ВОССТАНАВЛИВАЕМ ИНТЕРФЕЙС ПРИ ОШИБКЕ
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await update.message.reply_text(
            "❌ Ошибка при установке времени. Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id

        return ADD_TIME

async def ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Игнорирует ненужные callback'и"""
    query = update.callback_query
    await query.answer()
async def handle_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка времени напоминания с защитой от ложных срабатываний"""
    # Проверяем, что это текстовое сообщение от пользователя
    if not update.message or not update.message.text:
        return ADD_TIME

    try:
        time_str = update.message.text.strip()

        # Дополнительная проверка - убеждаемся, что у нас есть reminder_date
        if 'reminder_date' not in context.user_data:
            logger.error("handle_reminder_time: reminder_date not found in context")
            # Показываем сообщение об ошибке
            keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
            await update.message.reply_text(
                "❌ Ошибка: дата не установлена. Начните создание напоминания заново.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END

        base_date = context.user_data['reminder_date']

        # Удаляем сообщение пользователя
        try:
            await update.message.delete()
            logger.info("Удалили сообщение пользователя с временем")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        if time_str.lower() == '/skip':
            # Используем функцию для установки ближайшего времени
            return await skip_to_next_available_time(update, context)
        else:
            try:
                reminder_time = parse_datetime(time_str, base_date)
                context.user_data['reminder_time'] = reminder_time
                logger.info(f"Время напоминания получено: {reminder_time}")

                # Снимаем флаг ожидания ввода времени
                context.user_data.pop('waiting_for_time_input', None)

            except ValueError as e:
                logger.error(f"Неверный формат времени: {update.message.text}")

                # Удаляем предыдущее сообщение с инструкцией
                instruction_message_id = context.user_data.get('instruction_message_id')
                if instruction_message_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=instruction_message_id
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

                # Отправляем новое сообщение с ошибкой
                keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
                message = await update.message.reply_text(
                    f"❌ Неверный формат времени: {str(e)}\n\n"
                    "⏰ Введите время напоминания (например, 14:30) или используйте /skip для установки на ближайшее доступное время:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['instruction_message_id'] = message.message_id
                return ADD_TIME

        # Переходим к выбору интервала
        keyboard = [
            [InlineKeyboardButton("Однократно", callback_data="interval_0")],
            [InlineKeyboardButton("Каждый день", callback_data="interval_1")],
            [InlineKeyboardButton("Каждые 3 дня", callback_data="interval_3")],
            [InlineKeyboardButton("Каждую неделю", callback_data="interval_7")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_day_selection")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        # Удаляем предыдущее сообщение с инструкцией
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Отправляем новое сообщение
        message = await update.message.reply_text(
            "🔄 Выберите интервал повторения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id

        return ADD_INTERVAL

    except Exception as e:
        logger.error(f"Ошибка в handle_reminder_time: {e}")

        # Удаляем предыдущее сообщение с инструкцией
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Отправляем сообщение с ошибкой
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await update.message.reply_text(
            f"❌ Ошибка при обработке времени: {str(e)}\n\n"
            "⏰ Введите время напоминания (например, 14:30) или используйте /skip для установки на ближайшее доступное время:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        return ADD_TIME

def generate_single_month_calendar(year, month):
    """Генерирует клавиатуру календаря для ОДНОГО месяца"""
    keyboard = []

    # Заголовок с месяцем и годом
    month_name = calendar.month_name[month]
    header = f"{month_name} {year}"
    keyboard.append([InlineKeyboardButton(header, callback_data="ignore")])

    # Дни недели
    week_days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    keyboard.append([InlineKeyboardButton(day, callback_data="ignore") for day in week_days])

    # Получаем календарь на месяц
    cal = calendar.monthcalendar(year, month)
    today = datetime.now(MOSCOW_TZ).date()

    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                current_date = datetime(year, month, day).date()
                if current_date < today:
                    # Прошедшие дни делаем неактивными
                    row.append(InlineKeyboardButton(" ", callback_data="ignore"))
                else:
                    row.append(InlineKeyboardButton(str(day), callback_data=f"cal_day_{year}_{month}_{day}"))
        keyboard.append(row)

    return keyboard

def get_calendar_navigation(year, month):
    """Генерирует кнопки навигации для календаря"""
    # Вычисляем предыдущий и следующий месяц
    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year = year - 1

    next_month = month + 1
    next_year = year
    if next_month == 13:
        next_month = 1
        next_year = year + 1

    # Текущая дата для ограничений
    current_date = datetime.now(MOSCOW_TZ)
    current_year = current_date.year
    current_month = current_date.month

    # Проверяем, можно ли листать назад (только начиная с текущего месяца)
    can_go_prev = (prev_year > current_year) or (prev_year == current_year and prev_month >= current_month)

    # Ограничиваем календарь 12 месяцами вперед
    max_future_month = current_month + 12
    max_future_year = current_year
    if max_future_month > 12:
        max_future_year += (max_future_month - 1) // 12
        max_future_month = (max_future_month - 1) % 12 + 1

    can_go_next = (next_year < max_future_year) or (next_year == max_future_year and next_month <= max_future_month)

    # Создаем навигацию
    navigation_buttons = []

    if can_go_prev:
        navigation_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"cal_prev_{prev_year}_{prev_month}"))

    navigation_buttons.append(InlineKeyboardButton("🔙 Назад", callback_data="back_to_custom_menu"))

    if can_go_next:
        navigation_buttons.append(InlineKeyboardButton("➡️", callback_data=f"cal_next_{next_year}_{next_month}"))

    return [navigation_buttons]

async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, year=None, month=None):
    """Показывает календарь для выбора даты - ТОЛЬКО ОДИН МЕСЯЦ"""
    query = update.callback_query
    await query.answer()

    # Получаем текущую дату
    current_date = datetime.now(MOSCOW_TZ)
    if not year or not month:
        year = current_date.year
        month = current_date.month

    # Сохраняем текущие год и месяц в контексте для навигации
    context.user_data['calendar_year'] = year
    context.user_data['calendar_month'] = month

    # Удаляем предыдущее сообщение с инструкцией
    instruction_message_id = context.user_data.get('instruction_message_id')
    if instruction_message_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=instruction_message_id
            )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

    # Генерируем календарь для ОДНОГО месяца
    calendar_keyboard = generate_single_month_calendar(year, month)

    # Добавляем навигацию
    navigation = get_calendar_navigation(year, month)
    calendar_keyboard.extend(navigation)

    # Отправляем сообщение с ОДНИМ календарем
    message = await query.message.reply_text(
        "📅 *Календарь*\n\nВыберите дату напоминания:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(calendar_keyboard)
    )
    context.user_data['instruction_message_id'] = message.message_id

    return ADD_DAY_CALENDAR

async def show_custom_day_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает меню выбора способа указания дня"""
    query = update.callback_query
    await query.answer()

    # Удаляем предыдущее сообщение с инструкцией
    instruction_message_id = context.user_data.get('instruction_message_id')
    if instruction_message_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=instruction_message_id
            )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

    keyboard = [
        [InlineKeyboardButton("📅 Выбрать дату из календаря", callback_data="show_calendar")],
        [InlineKeyboardButton("🔢 Ввести количество дней", callback_data="input_days")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_day_selection")]
    ]

    message = await query.message.reply_text(
        "Выберите способ указания дня напоминания:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['instruction_message_id'] = message.message_id

    return ADD_DAY_CUSTOM

async def handle_calendar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор даты из календаря"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("cal_day_"):
        # Обработка выбора дня
        _, _, year_str, month_str, day_str = data.split('_')
        year = int(year_str)
        month = int(month_str)
        day = int(day_str)

        selected_date = datetime(year, month, day).replace(tzinfo=MOSCOW_TZ)
        today = datetime.now(MOSCOW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

        # Вычисляем количество дней до выбранной даты
        days_difference = (selected_date - today).days

        if days_difference < 0:
            await query.answer("❌ Нельзя выбрать прошедшую дату!", show_alert=True)
            return ADD_DAY_CALENDAR

        # Сохраняем вычисленное количество дней
        context.user_data['reminder_date'] = selected_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # Показываем подтверждение выбора даты
        selected_date_str = selected_date.strftime('%d.%m.%Y')

        # Удаляем сообщение с календарем
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения с календарем: {e}")

        # Переходим к вводу времени с кнопкой "Назад"
        await proceed_to_time_selection_calendar(query, context, selected_date_str)
        return ADD_TIME

    elif data.startswith("cal_prev_") or data.startswith("cal_next_"):
        # Навигация по календарю - РЕДАКТИРУЕМ текущее сообщение
        _, direction, year_str, month_str = data.split('_')
        year = int(year_str)
        month = int(month_str)

        # Обновляем сохраненные значения
        context.user_data['calendar_year'] = year
        context.user_data['calendar_month'] = month

        # Генерируем новый календарь для ОДНОГО месяца
        calendar_keyboard = generate_single_month_calendar(year, month)
        navigation = get_calendar_navigation(year, month)
        calendar_keyboard.extend(navigation)

        # Редактируем текущее сообщение
        await query.edit_message_text(
            "📅 *Календарь*\n\nВыберите дату напоминания:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(calendar_keyboard)
        )
        return ADD_DAY_CALENDAR

    elif data == "back_to_custom_menu":
        # Возврат к выбору метода ввода
        keyboard = [
            [InlineKeyboardButton("📅 Выбрать дату из календаря", callback_data="show_calendar")],
            [InlineKeyboardButton("🔢 Ввести количество дней", callback_data="input_days")],
            [InlineKeyboardButton("🔙 Назад к выбору дня", callback_data="back_to_day_selection")]
        ]

        await query.edit_message_text(
            "Выберите способ указания дня напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_DAY_CUSTOM

    return ADD_DAY_CALENDAR

async def proceed_to_time_selection_calendar(query, context, selected_date_str):
    """Переход к выбору времени после выбора даты из календаря"""
    # Удаляем предыдущее сообщение с инструкцией
    instruction_message_id = context.user_data.get('instruction_message_id')
    if instruction_message_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=instruction_message_id
            )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

    # Отправляем сообщение с кнопками "Назад" и "Отмена"
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к календарю", callback_data="back_to_calendar_from_time")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
    ]

    message = await query.message.reply_text(
        f"✅ Выбрана дата: *{selected_date_str}*\n\n"
        "⏰ Введите время напоминания (например, 14:30) или используйте /skip для установки на ближайшее доступное время:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['instruction_message_id'] = message.message_id
    context.user_data['selected_date_str'] = selected_date_str

    # Устанавливаем флаг ожидания ввода времени
    context.user_data['waiting_for_time_input'] = True

    # Явно устанавливаем состояние ADD_TIME
    return ADD_TIME

async def handle_back_to_calendar_from_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возврата из ввода времени обратно в календарь"""
    query = update.callback_query
    await query.answer()

    # Сбрасываем флаг ожидания ввода времени
    context.user_data.pop('waiting_for_time_input', None)

    # Получаем сохраненные год и месяц календаря
    year = context.user_data.get('calendar_year')
    month = context.user_data.get('calendar_month')

    if not year or not month:
        # Если нет сохраненных значений, используем текущий месяц
        current_date = datetime.now(MOSCOW_TZ)
        year = current_date.year
        month = current_date.month

    # Удаляем сообщение с вводом времени
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения с вводом времени: {e}")

    # Генерируем календарь для ОДНОГО месяца
    calendar_keyboard = generate_single_month_calendar(year, month)
    navigation = get_calendar_navigation(year, month)
    calendar_keyboard.extend(navigation)

    message = await query.message.reply_text(
        "📅 *Календарь*\n\nВыберите дату напоминания:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(calendar_keyboard)
    )
    context.user_data['instruction_message_id'] = message.message_id

    return ADD_DAY_CALENDAR

async def handle_reminder_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора дня для напоминания с удалением сообщений об ошибках"""
    # Определяем, откуда пришел запрос - от callback или сообщения
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data
        is_callback = True
    else:
        # Это текстовое сообщение с количеством дней
        data = update.message.text
        is_callback = False
        query = None

    logger.info(f"Обработка выбора дня: {data}")

    # Обработка отмены
    if data == "cancel_reminder":
        if query:
            await cancel_reminder(query, context)
        else:
            await cancel_reminder(update, context)
        return ConversationHandler.END

    # Обработка выбора предопределенных дней
    today = datetime.now(MOSCOW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    if data in ["day_today", "day_tomorrow", "day_after_tomorrow"]:
        days_to_add = {
            "day_today": 0,
            "day_tomorrow": 1,
            "day_after_tomorrow": 2
        }
        context.user_data['reminder_date'] = today + timedelta(days=days_to_add[data])
        logger.info(f"Выбран день: {data.replace('day_', '').replace('_', ' ').title()}")

    elif data == "day_custom":
        return await show_custom_day_menu(update, context)

    else:
        # Обработка ввода количества дней
        return await handle_days_input(update, context, data, is_callback, query)

    # Переходим к выбору времени
    return await proceed_to_time_selection(update, context, is_callback, query)

async def handle_days_input(update, context, data, is_callback, query):
    """Обработка ввода количества дней"""
    # Проверяем, ожидаем ли мы ввод дней
    if not context.user_data.get('waiting_for_days_input'):
        return await handle_invalid_day_selection(update, context, data, is_callback)

    try:
        days = int(data.strip())
        if days < 0:
            raise ValueError("Количество дней не может быть отрицательным")

        today = datetime.now(MOSCOW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        context.user_data['reminder_date'] = today + timedelta(days=days)
        context.user_data.pop('waiting_for_days_input', None)  # Снимаем флаг
        logger.info(f"Выбран день: через {days} дней")

        # УДАЛЯЕМ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ С КОЛИЧЕСТВОМ ДНЕЙ
        if not is_callback:
            try:
                await update.message.delete()
                logger.info("Удалили сообщение пользователя с количеством дней")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        return await proceed_to_time_selection(update, context, is_callback, query)

    except ValueError:
        logger.error(f"Неверный формат количества дней: {data}")
        return await handle_invalid_days_input(update, context, is_callback, query)

async def handle_invalid_day_selection(update, context, data, is_callback):
    """Обработка неверного выбора дня"""
    if is_callback:
        await update.callback_query.answer("❌ Неверный выбор дня")
        return ADD_DAY
    else:
        # УДАЛЯЕМ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ С ОШИБКОЙ
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        # УДАЛЯЕМ ПРЕДЫДУЩЕЕ СООБЩЕНИЕ С ИНСТРУКЦИЕЙ (ЕСЛИ ЕСТЬ)
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Показываем основное меню выбора дня с ошибкой
        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        message = await update.message.reply_text(
            "❌ Неверный выбор. Выберите день из списка:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        return ADD_DAY

async def handle_invalid_days_input(update, context, is_callback, query):
    """Обработка неверного ввода количества дней"""
    # УДАЛЯЕМ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ С ОШИБОЧНЫМ ВВОДОМ
    if not is_callback:
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

    # УДАЛЯЕМ ПРЕДЫДУЩЕЕ СООБЩЕНИЕ С ИНСТРУКЦИЕЙ (ЕСЛИ ЕСТЬ)
    instruction_message_id = context.user_data.get('instruction_message_id')
    if instruction_message_id:
        try:
            if is_callback:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=instruction_message_id
                )
            else:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

    # ОШИБКА ВВОДА КОЛИЧЕСТВА ДНЕЙ - показываем сообщение для этого конкретного случая
    if is_callback:
        # Для callback (кнопка "Отмена" в сообщении об ошибке)
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await query.message.reply_text(
            "❌ Неверный формат. Введите число дней (например, 5):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
    else:
        # Для текстового ввода
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await update.message.reply_text(
            "❌ Неверный формат. Введите число дней (например, 5):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
    return ADD_DAY

async def proceed_to_time_selection(update, context, is_callback, query):
    """Переход к выбору времени"""
    # Снимаем флаг ожидания ввода дней (если был установлен)
    context.user_data.pop('waiting_for_days_input', None)

    # Устанавливаем флаг, что мы перешли к вводу времени
    context.user_data['waiting_for_time_input'] = True

    if is_callback:
        # Удаляем предыдущее сообщение с инструкцией
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Отправляем сообщение с кнопкой "Отмена"
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await query.message.reply_text(
            "⏰ Введите время напоминания (например, 14:30) или используйте /skip для установки на ближайшее доступное время:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
    else:
        # Удаляем предыдущее сообщение с инструкцией
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        # Отправляем сообщение с кнопкой "Отмена"
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await update.message.reply_text(
            "⏰ Введите время напоминания (например, 14:30) или используйте /skip для установки на ближайшее доступное время:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id

    return ADD_TIME

async def handle_reminder_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка интервала напоминания с редактированием сообщения при возврате"""
    query = update.callback_query
    await query.answer()

    data = query.data

    # Обработка отмены
    if data == "cancel_reminder":
        await cancel_reminder(query, context)
        return ConversationHandler.END

    # Обработка возврата к выбору дня - РЕДАКТИРУЕМ СООБЩЕНИЕ
    if data == "back_to_day_selection":
        # РЕДАКТИРУЕМ текущее сообщение вместо отправки нового
        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        await query.edit_message_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Обновляем ID сообщения с инструкцией
        context.user_data['instruction_message_id'] = query.message.message_id
        return ADD_DAY

    # Обработка интервалов
    if data.startswith("interval_"):
        try:
            interval = int(data.replace("interval_", ""))
            context.user_data['reminder_interval'] = interval
            logger.info(f"Интервал напоминания выбран: {interval} дней")

            # Переход к выбору пользователей - редактируем сообщение
            await show_user_selection(query, context)
            return ADD_USERS
        except (ValueError, TypeError) as e:
            logger.error(f"Ошибка преобразования интервала: {e}")

    # Если данные не распознаны
    logger.error(f"Неизвестный callback data в handle_reminder_interval: {data}")

    # Редактируем текущее сообщение с ошибкой
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
    await query.edit_message_text(
        "❌ Произошла ошибка. Попробуйте снова.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_INTERVAL

async def handle_reminder_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора пользователей для напоминания с чекбоксами"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "save_reminder":
        try:
            reminders = load_reminders()
            reminder_id = str(int(datetime.now().timestamp()))

            # Проверяем, что выбран хотя бы один пользователь
            selected_users = context.user_data.get('reminder_users', [])
            if not selected_users:
                await query.edit_message_text(
                    "❌ Не выбран ни один пользователь. Пожалуйста, выберите хотя бы одного пользователя.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад к выбору", callback_data="back_to_user_selection")]
                    ])
                )
                return ADD_USERS

            reminder = {
                'id': reminder_id,
                'text': context.user_data.get('reminder_text', 'Без текста'),
                'datetime': context.user_data['reminder_time'].isoformat(),
                'interval_days': context.user_data.get('reminder_interval', 0),
                'users': selected_users,
                'created_by': str(query.from_user.id),
                'created_at': datetime.now(MOSCOW_TZ).isoformat(),
                'type': 'personal',
                'confirmed_by': set(),
                'postponed_by': set(),
                'delete_confirmed_by': set(),
                'not_bought_count': 0,
                'frequency_multiplier': 1
            }

            reminders[reminder_id] = reminder
            if not save_reminders(reminders):
                logger.error("Ошибка при записи напоминаний в файл reminders.json")
                await query.edit_message_text(
                    "❌ Ошибка при создании напоминания. Проверьте права доступа к файлу reminders.json.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
                    ])
                )
                return ConversationHandler.END

            await query.edit_message_text(
                "✅ Напоминание создано!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
                ])
            )
            context.user_data.clear()
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"Ошибка сохранения напоминания: {e}")
            await query.edit_message_text(
                "❌ Ошибка при создании напоминания.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
                ])
            )
            return ConversationHandler.END

    elif data == "back_to_interval":
        # Возврат к выбору интервала - РЕДАКТИРУЕМ СООБЩЕНИЕ
        keyboard = [
            [InlineKeyboardButton("Однократно", callback_data="interval_0")],
            [InlineKeyboardButton("Каждый день", callback_data="interval_1")],
            [InlineKeyboardButton("Каждые 3 дня", callback_data="interval_3")],
            [InlineKeyboardButton("Каждую неделю", callback_data="interval_7")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_day_selection")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]

        await query.edit_message_text(
            "🔄 Выберите интервал повторения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_INTERVAL

    elif data == "back_to_user_selection":
        await show_user_selection(query, context)
        return ADD_USERS

    elif data == "cancel_reminder":
        await cancel_reminder(query, context)
        return ConversationHandler.END

    else:
        # Обработка переключения пользователя
        user_id = data.replace("toggle_user_", "")
        selected_users = context.user_data.get('reminder_users', [])

        if user_id in selected_users:
            # Убираем пользователя из выбранных
            selected_users.remove(user_id)
        else:
            # Добавляем пользователя в выбранных
            selected_users.append(user_id)

        context.user_data['reminder_users'] = selected_users

        # Обновляем сообщение с новым состоянием чекбоксов
        await show_user_selection(query, context)
        return ADD_USERS

async def handle_reminders_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка пагинации списка напоминаний с учетом типа"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Обработка пагинации: {data}")

    if data == "current_page":
        # Просто обновляем сообщение без изменений
        await query.answer()
        return

    if data.startswith('regular_page_'):
        page = int(data.replace("regular_page_", ""))
        context.user_data['regular_page'] = page
        context.user_data['reminders_list_type'] = 'regular'
    elif data.startswith('ingredients_page_'):
        page = int(data.replace("ingredients_page_", ""))
        context.user_data['ingredients_page'] = page
        context.user_data['reminders_list_type'] = 'ingredients'

    await list_reminders(update, context)

async def handle_reminders_list_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка переключения между списками напоминаний"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Переключение списка: {data}")

    if data == "switch_to_regular":
        context.user_data['reminders_list_type'] = 'regular'
        context.user_data['regular_page'] = 0
    elif data == "switch_to_ingredients":
        context.user_data['reminders_list_type'] = 'ingredients'
        context.user_data['ingredients_page'] = 0

    await list_reminders(update, context)

async def show_user_selection(query, context):
    """Показать выбор пользователей для напоминания с чекбоксами в одном сообщении"""
    users = load_users()

    # Получаем текущий список выбранных пользователей
    selected_users = context.user_data.get('reminder_users', [])

    keyboard = []
    for user_id, user_data in users.items():
        # Проверяем, выбран ли уже этот пользователь
        is_selected = user_id in selected_users
        icon = "✅" if is_selected else "◻️"

        keyboard.append([
            InlineKeyboardButton(
                f"{icon} {user_data['username']}",
                callback_data=f"toggle_user_{user_id}"
            )
        ])

    # Клавиатура с кнопками "Назад" и "Отмена"
    keyboard.append([
        InlineKeyboardButton("💾 Сохранить напоминание", callback_data="save_reminder")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Назад к интервалу", callback_data="back_to_interval")
    ])
    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")
    ])

    selected_count = len(selected_users)
    text = f"👥 *Выберите пользователей для напоминания:*\n(Выбрано: {selected_count})\n\n"
    text += "Нажмите на пользователя, чтобы выбрать/снять выбор\n"
    text += "Когда закончите, нажмите 'Сохранить напоминание'"

    # РЕДАКТИРУЕМ текущее сообщение вместо отправки нового
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Обновляем ID сообщения с инструкцией
    context.user_data['instruction_message_id'] = query.message.message_id

async def my_reminders_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список напоминаний пользователя для удаления"""
    query = update.callback_query
    await query.answer()

    reminders = load_reminders()
    user_id = str(query.from_user.id)

    # Фильтруем напоминания: только обычные и только созданные текущим пользователем
    user_reminders = {
        rid: rem for rid, rem in reminders.items()
        if rem.get('created_by') == user_id and rem.get('type') != 'ingredient'
    }

    if not user_reminders:
        await query.edit_message_text(
            "❌ У вас нет напоминаний для удаления.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать напоминание", callback_data="add_reminder")],
                [InlineKeyboardButton("📋 Все напоминания", callback_data="list_reminders")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    text = "🗑 *Ваши напоминания для удаления:*\n\n"
    keyboard = []

    for rid, reminder in user_reminders.items():
        # Обрезаем длинный текст для кнопки
        button_text = reminder['text'][:35] + "..." if len(reminder['text']) > 35 else reminder['text']

        # Добавляем дату для информации
        reminder_time = datetime.fromisoformat(reminder['datetime']).strftime('%d.%m %H:%M')

        # Создаем кнопку с названием напоминания
        keyboard.append([
            InlineKeyboardButton(
                f"🔔 {button_text} ({reminder_time})",
                callback_data=f"delete_reminder_{rid}"
            )
        ])

    # Добавляем информационные кнопки
    keyboard.extend([
        [InlineKeyboardButton("🔙 Назад к списку напоминаний", callback_data="list_reminders")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ])

    text += f"Всего напоминаний: {len(user_reminders)}\n"
    text += "Нажмите на напоминание, чтобы удалить его."

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка удаления напоминания с подтверждением"""
    query = update.callback_query
    await query.answer()

    reminder_id = query.data.replace("delete_reminder_", "")
    reminders = load_reminders()
    reminder = reminders.get(reminder_id)

    if not reminder:
        await query.edit_message_text(
            "❌ Напоминание не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="my_reminders_delete")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Проверяем, что напоминание не является ингредиентом
    if reminder.get('type') == 'ingredient':
        await query.edit_message_text(
            "❌ Нельзя удалить напоминание для ингредиента.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="my_reminders_delete")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Проверяем, что пользователь является создателем
    if str(query.from_user.id) != reminder.get('created_by'):
        await query.edit_message_text(
            "❌ Вы можете удалять только свои напоминания.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="my_reminders_delete")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Показываем подтверждение удаления
    reminder_time = datetime.fromisoformat(reminder['datetime']).strftime('%d.%m.%Y %H:%M')
    interval_text = "однократно" if reminder.get('interval_days', 0) == 0 else f"каждые {reminder['interval_days']} дней"

    text = f"🗑 *Подтверждение удаления*\n\n"
    text += f"🔔 *{reminder['text']}*\n"
    text += f"🔄 {interval_text}\n"
    text += f"⏰ {reminder_time}\n\n"
    text += "Вы уверены, что хотите удалить это напоминание?"

    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{reminder_id}"),
            InlineKeyboardButton("❌ Нет, отменить", callback_data="my_reminders_delete")
        ],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_custom_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора способа ввода кастомной даты"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "show_calendar":
        await show_calendar(update, context)
        return ADD_DAY_CALENDAR

    elif data == "input_days":
        # Существующая логика ввода количества дней
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=instruction_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с инструкцией: {e}")

        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]]
        message = await query.message.reply_text(
            "📅 Введите количество дней до напоминания (например, 5):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['instruction_message_id'] = message.message_id
        context.user_data['waiting_for_days_input'] = True
        return ADD_DAY

    elif data == "back_to_day_selection":
        # Возврат к выбору дня - РЕДАКТИРУЕМ текущее сообщение
        keyboard = [
            [InlineKeyboardButton("Сегодня", callback_data="day_today")],
            [InlineKeyboardButton("Завтра", callback_data="day_tomorrow")],
            [InlineKeyboardButton("Послезавтра", callback_data="day_after_tomorrow")],
            [InlineKeyboardButton("Другое", callback_data="day_custom")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
        ]
        await query.edit_message_text(
            "📅 Выберите день для напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_DAY

    return ADD_DAY_CUSTOM

async def handle_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка окончательного подтверждения удаления"""
    query = update.callback_query
    await query.answer()

    reminder_id = query.data.replace("confirm_delete_", "")
    reminders = load_reminders()
    reminder = reminders.get(reminder_id)

    if not reminder:
        await query.edit_message_text(
            "❌ Напоминание не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="my_reminders_delete")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Сохраняем текст напоминания для сообщения
    reminder_text = reminder['text']

    # Удаляем напоминание
    del reminders[reminder_id]
    if not save_reminders(reminders):
        logger.error("Ошибка при удалении напоминания")
        await query.edit_message_text(
            "❌ Ошибка при удалении напоминания.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="my_reminders_delete")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Показываем подтверждение
    text = f"✅ *Напоминание удалено!*\n\n"
    text += f"🗑 '{reminder_text}'\n\n"
    text += "Напоминание было успешно удалено."

    keyboard = [
        [InlineKeyboardButton("🗑 Удалить еще напоминания", callback_data="my_reminders_delete")],
        [InlineKeyboardButton("📋 Все напоминания", callback_data="list_reminders")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_reminder(update_or_query, context):
    """Отмена создания напоминания с улучшенной обработкой ошибок редактирования и удаления"""
    logger.info("Отмена создания напоминания")

    try:
        # Очищаем все флаги
        context.user_data.pop('waiting_for_days_input', None)
        context.user_data.pop('waiting_for_time_input', None)
        context.user_data.pop('reminder_date', None)
        context.user_data.pop('reminder_time', None)
        chat_id = None
        message_to_edit = None

        # Определяем тип объекта и получаем необходимые данные
        if isinstance(update_or_query, Update):
            # Это обычное сообщение (команда /cancel)
            if update_or_query.message:
                chat_id = update_or_query.message.chat_id
                message_to_edit = None  # Нет сообщения для редактирования
            elif update_or_query.callback_query:
                # Это callback query
                query = update_or_query.callback_query
                await query.answer()
                chat_id = query.message.chat_id
                message_to_edit = query.message  # Сохраняем сообщение для возможного редактирования
        else:
            # Это CallbackQuery
            query = update_or_query
            await query.answer()
            chat_id = query.message.chat_id
            message_to_edit = query.message  # Сохраняем сообщение для возможного редактирования

        if not chat_id:
            logger.error("Не удалось определить chat_id")
            return ConversationHandler.END

        # Удаляем предыдущее сообщение с инструкцией
        instruction_message_id = context.user_data.get('instruction_message_id')
        if instruction_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=instruction_message_id
                )
                logger.info(f"✅ Удалено сообщение с инструкцией: {instruction_message_id}")
            except Exception as e:
                if "Message to delete not found" in str(e):
                    logger.info(f"ℹ️ Сообщение с инструкцией уже удалено: {instruction_message_id}")
                else:
                    logger.error(f"❌ Ошибка при удалении сообщения с инструкцией: {e}")

        # Общая функция отправки сообщения отмены
        async def send_cancel_message():
            try:
                if message_to_edit:
                    # Пытаемся отредактировать существующее сообщение
                    try:
                        await message_to_edit.edit_text(
                            "❌ Создание напоминания отменено.",
                            reply_markup=get_main_keyboard()
                        )
                        logger.info("✅ Сообщение отмены отредактировано")
                    except Exception as edit_error:
                        if "Message to edit not found" in str(edit_error):
                            logger.info("ℹ️ Сообщение для редактирования не найдено, отправляем новое")
                            # Отправляем новое сообщение если редактирование невозможно
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="❌ Создание напоминания отменено.",
                                reply_markup=get_main_keyboard()
                            )
                        else:
                            raise edit_error  # Перебрасываем другие ошибки
                else:
                    # Отправляем новое сообщение
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Создание напоминания отменено.",
                        reply_markup=get_main_keyboard()
                    )
            except Exception as e:
                logger.error(f"❌ Ошибка при отправке сообщения отмены: {e}")
                # Последняя попытка отправить сообщение
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Создание напоминания отменено.",
                        reply_markup=get_main_keyboard()
                    )
                except Exception as final_error:
                    logger.error(f"❌ Критическая ошибка при отправке сообщения: {final_error}")

        await send_cancel_message()
        context.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в cancel_reminder: {e}")

        # Аварийное восстановление
        try:
            emergency_chat_id = None
            if hasattr(update_or_query, 'message') and update_or_query.message:
                emergency_chat_id = update_or_query.message.chat_id
            elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
                emergency_chat_id = update_or_query.callback_query.message.chat_id
            elif hasattr(update_or_query, 'message'):  # Это CallbackQuery
                emergency_chat_id = update_or_query.message.chat_id

            if emergency_chat_id and context.bot:
                await context.bot.send_message(
                    chat_id=emergency_chat_id,
                    text="❌ Создание напоминания отменено.",
                    reply_markup=get_main_keyboard()
                )
        except Exception as final_error:
            logger.error(f"❌ Не удалось отправить сообщение об отмене: {final_error}")

        context.user_data.clear()
        return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать разделенные списки напоминаний с пагинацией"""
    query = update.callback_query
    await query.answer()
    reminders = load_reminders()

    if not reminders:
        await query.edit_message_text(
            "❌ Нет активных напоминаний.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать напоминание", callback_data="add_reminder")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Определяем тип списка (обычные или ингредиенты)
    list_type = context.user_data.get('reminders_list_type', 'regular')

    # Разделяем напоминания
    regular_reminders = {rid: rem for rid, rem in reminders.items() if rem.get('type') != 'ingredient'}
    ingredient_reminders = {rid: rem for rid, rem in reminders.items() if rem.get('type') == 'ingredient'}

    # Выбираем активный список
    if list_type == 'regular':
        active_reminders = list(regular_reminders.items())
        list_title = "📋 Обычные напоминания"
        page_key = 'regular_page'
    else:
        active_reminders = list(ingredient_reminders.items())
        list_title = "🍽 Напоминания для блюд"
        page_key = 'ingredients_page'

    items_per_page = 5
    page = context.user_data.get(page_key, 0)
    total_pages = max(1, (len(active_reminders) + items_per_page - 1) // items_per_page)

    # Корректируем номер страницы, если он выходит за пределы
    if page >= total_pages:
        page = total_pages - 1
        context.user_data[page_key] = page

    # Получаем напоминания для текущей страницы
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_reminders = active_reminders[start_idx:end_idx]

    text = f"{list_title} (страница {page + 1}/{total_pages})\n\n"

    # Инициализируем клавиатуру
    keyboard = []

    if not current_reminders:
        text += "❌ Нет напоминаний в этой категории.\n"
    else:
        for rid, reminder in current_reminders:
            if reminder.get('type') == 'ingredient':
                # УЛУЧШЕННЫЙ ФОРМАТ для напоминаний ингредиентов
                text += f"🍽 *{reminder.get('recipe_name', 'Неизвестно')}*\n"
                text += f"📅 *Приготовление:* {reminder.get('meal_date', 'Неизвестно')}\n"

                # Парсим информацию об ингредиенте из текста
                reminder_text = reminder['text']
                lines = reminder_text.split('\n')
                ingredient_info = None
                responsible_info = None

                for line in lines:
                    if line.strip().startswith('•'):
                        ingredient_info = line.strip()[1:].strip()  # Убираем маркер списка
                    elif line.startswith('👤 Ответственный:'):
                        responsible_info = line.replace('👤 Ответственный:', '').strip()

                if ingredient_info:
                    if ' - ' in ingredient_info:
                        ing_name, ing_quantity = ingredient_info.split(' - ', 1)
                        text += f"🛒 *Ингредиент:* {ing_name.strip()}\n"
                        text += f"⚖️ *Количество:* {ing_quantity.strip()}\n"
                    else:
                        text += f"🛒 *Ингредиент:* {ingredient_info}\n"

                if responsible_info:
                    text += f"👤 *Ответственный:* {responsible_info}\n"

                # Статус срочного напоминания
                if reminder.get('urgent_reminders'):
                    urgent_until = reminder.get('urgent_until')
                    if urgent_until:
                        urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                        time_left = urgent_until_time - datetime.now(MOSCOW_TZ)
                        hours_left = max(0, int(time_left.total_seconds() / 3600))
                        text += f"🚨 *СРОЧНОЕ* (осталось {hours_left}ч.)\n"
                    else:
                        text += "🚨 *СРОЧНОЕ* (каждые 3 часа)\n"

                text += "---\n"
            else:
                # Старый формат для обычных напоминаний
                text += f"🔔 *{reminder['text'][:80]}...*\n" if len(reminder['text']) > 80 else f"🔔 *{reminder['text']}*\n"
                interval_text = "однократно" if reminder.get('interval_days', 0) == 0 else f"каждые {reminder['interval_days']} дней"
                text += f"🔄 {interval_text}\n"
                text += f"⏰ {datetime.fromisoformat(reminder['datetime']).strftime('%d.%m.%Y %H:%M')}\n"

                # УЛУЧШЕННОЕ ОТОБРАЖЕНИЕ СРОЧНЫХ НАПОМИНАНИЙ
                if reminder.get('urgent_reminders'):
                    urgent_until = reminder.get('urgent_until')
                    if urgent_until:
                        urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                        time_left = urgent_until_time - datetime.now(MOSCOW_TZ)
                        hours_left = max(0, int(time_left.total_seconds() / 3600))
                        text += f"🚨 *СРОЧНОЕ* (осталось {hours_left}ч.)\n"
                    else:
                        text += "🚨 *СРОЧНОЕ* (каждые 3 часа)\n"

                text += "---\n"

    # Кнопки переключения между списками
    list_buttons = []
    if list_type == 'regular':
        list_buttons.append(InlineKeyboardButton("🍽 К блюдам", callback_data="switch_to_ingredients"))
    else:
        list_buttons.append(InlineKeyboardButton("📋 К обычным", callback_data="switch_to_regular"))
    keyboard.append(list_buttons)

    # Кнопки пагинации
    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{list_type}_page_{page-1}"))

    # Добавляем номер текущей страницы (опционально)
    pagination_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="current_page"))

    if end_idx < len(active_reminders):
        pagination_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"{list_type}_page_{page+1}"))

    if pagination_buttons:
        keyboard.append(pagination_buttons)

    # Основные кнопки (ДОБАВЛЕНА КНОПКА ДЛЯ УДАЛЕНИЯ)
    keyboard.extend([
        [InlineKeyboardButton("➕ Создать напоминание", callback_data="add_reminder")],
        [InlineKeyboardButton("📅 Запланировать блюдо", callback_data="plan_meal")],
        [InlineKeyboardButton("🗑 Удалить мои напоминания", callback_data="my_reminders_delete")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ])

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список пользователей"""
    query = update.callback_query
    await query.answer()
    users = load_users()

    if not users:
        await query.edit_message_text(
            "❌ Нет зарегистрированных пользователей.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    text = "👥 *Пользователи:*\n\n"
    for user_id, user_data in users.items():
        text += f"• {user_data.get('username', 'Unknown')} (ID: {user_id})\n"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ])
    )

async def recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /recipes для работы с рецептами"""
    query = update.callback_query if update.callback_query else update.message
    if update.callback_query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Создать рецепт", callback_data="create_recipe")],
        [InlineKeyboardButton("📝 Редактировать рецепты", callback_data="edit_recipes")],
        [InlineKeyboardButton("📅 Запланировать блюдо", callback_data="plan_meal")],
        [InlineKeyboardButton("📋 Все рецепты", callback_data="list_recipes")],
        [InlineKeyboardButton("📅 Все планы", callback_data="list_meal_plans")],
        [InlineKeyboardButton("⚙️ Управление планами", callback_data="manage_plans")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ]
    text = "🍽 *Меню рецептов*\n\nВыберите действие:"

    if update.callback_query:
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_recipes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка callback-запросов для системы рецептов"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "create_recipe":
        return await start_recipe_creation(update, context)
    elif data == "plan_meal":
        await show_week_days(query, context)
        return MEAL_DAY
    elif data == "list_recipes":
        await list_recipes(update, context)
        return ConversationHandler.END
    elif data == "list_meal_plans":
        await list_meal_plans(update, context)
        return ConversationHandler.END
    elif data == "back_to_recipes":
        await recipes_command(update, context)
        return ConversationHandler.END
    elif data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

async def start_recipe_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало создания рецепта с кнопкой отмены"""
    # Очищаем предыдущие данные
    context.user_data.clear()

    query = update.callback_query
    await query.answer()

    # Сохраняем ID сообщения для последующего редактирования
    context.user_data['recipe_message_id'] = query.message.message_id

    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "🍽 *Создание рецепта*\n\nВведите название рецепта:\n(Для отмены введите /cancel)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return RECIPE_NAME

async def handle_recipe_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка названия рецепта с удалением сообщений и редактированием"""
    try:
        # Удаляем сообщение пользователя с названием рецепта
        try:
            await update.message.delete()
            logger.info("Удалили сообщение пользователя с названием рецепта")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        recipe_name = update.message.text.strip()
        if not recipe_name:
            # Редактируем существующее сообщение с ошибкой
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ]

            message_id = context.user_data.get('recipe_message_id')
            if message_id:
                await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                    chat_id=update.effective_chat.id,
                    message_id=message_id,
                    text="❌ Название рецепта не может быть пустым. Введите название рецепта:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text("❌ Название рецепта не может быть пустым. Введите название:")
            return RECIPE_NAME

        context.user_data['recipe_name'] = recipe_name
        logger.info(f"Название рецепта получено: {recipe_name}")

        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipe_name")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        # Редактируем существующее сообщение вместо отправки нового
        message_id = context.user_data.get('recipe_message_id')
        if message_id:
            await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text="📋 Введите ингредиенты (через запятую, название и количество через пробел):\n"
                     "Пример: помидоры 500г, огурцы 300г, соль по вкусу\n"
                     "(Для отмены введите /cancel)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Если нет сохраненного ID сообщения, отправляем новое
            message = await update.message.reply_text(
                "📋 Введите ингредиенты (через запятую, название и количество через пробел):\n"
                "Пример: помидоры 500г, огурцы 300г, соль по вкусу\n"
                "(Для отмены введите /cancel)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['recipe_message_id'] = message.message_id

        return RECIPE_INGREDIENTS

    except Exception as e:
        logger.error(f"Ошибка в handle_recipe_name: {e}")

        # Редактируем существующее сообщение с ошибкой
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        message_id = context.user_data.get('recipe_message_id')
        if message_id:
            await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text="❌ Ошибка при обработке названия. Попробуйте снова:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ Ошибка при обработке названия. Попробуйте снова:")
        return RECIPE_NAME

async def handle_recipe_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода ингредиентов для рецепта с удалением сообщений и редактированием"""
    try:
        # Удаляем сообщение пользователя с ингредиентами
        try:
            await update.message.delete()
            logger.info("Удалили сообщение пользователя с ингредиентами")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        ingredients_str = update.message.text.strip()
        ingredients = []
        for ing in ingredients_str.split(','):
            ing = ing.strip()
            if not ing:
                continue
            if ' ' in ing:
                name, quantity = ing.rsplit(' ', 1)
                ingredients.append({
                    'id': len(ingredients),
                    'name': name.strip(),
                    'quantity': quantity.strip()
                })
            else:
                ingredients.append({
                    'id': len(ingredients),
                    'name': ing,
                    'quantity': 'не указано'
                })

        if not ingredients:
            # Редактируем существующее сообщение с ошибкой
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipe_name")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ]

            message_id = context.user_data.get('recipe_message_id')
            if message_id:
                await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                    chat_id=update.effective_chat.id,
                    message_id=message_id,
                    text="❌ Список ингредиентов пуст. Введите ингредиенты в формате: помидоры 500г, огурцы 300г",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    "❌ Список ингредиентов пуст. Введите ингредиенты в формате: помидоры 500г, огурцы 300г"
                )
            return RECIPE_INGREDIENTS

        context.user_data['ingredients'] = ingredients
        logger.info(f"Ингредиенты получены: {ingredients}")

        # Формируем текст для подтверждения
        text = f"🍽 *Рецепт: {context.user_data['recipe_name']}*\n\n"
        text += "📋 *Ингредиенты:*\n"
        for ing in ingredients:
            text += f"• {ing['name']} - {ing['quantity']}\n"

        keyboard = [
            [InlineKeyboardButton("💾 Сохранить рецепт", callback_data="save_recipe")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_recipe")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel_recipe")]
        ]

        # Редактируем существующее сообщение вместо отправки нового
        message_id = context.user_data.get('recipe_message_id')
        if message_id:
            await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Если нет сохраненного ID сообщения, отправляем новое
            message = await update.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['recipe_message_id'] = message.message_id

        return RECIPE_INGREDIENTS

    except Exception as e:
        logger.error(f"Ошибка в handle_recipe_ingredients: {e}")

        # Редактируем существующее сообщение с ошибкой
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipe_name")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        message_id = context.user_data.get('recipe_message_id')
        if message_id:
            await context.bot.edit_message_text(  # ИСПРАВЛЕНО: context.bot вместо update.message.bot
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text="❌ Ошибка при обработке ингредиентов. Введите в формате: помидоры 500г, огурцы 300г",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при обработке ингредиентов. Введите в формате: помидоры 500г, огурцы 300г"
            )
        return RECIPE_INGREDIENTS

async def back_to_recipe_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик возврата к вводу названия рецепта"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
        [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "🍽 *Создание рецепта*\n\nВведите название рецепта:\n(Для отмены введите /cancel)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return RECIPE_NAME

async def handle_recipe_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка подтверждения рецепта (сохранение, редактирование, отмена)"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "save_recipe":
        try:
            recipes = load_recipes()
            recipe_id = str(int(datetime.now().timestamp()))

            recipe = {
                'id': recipe_id,
                'name': context.user_data.get('recipe_name', 'Без названия'),
                'ingredients': context.user_data.get('ingredients', []),
                'created_by': str(query.from_user.id),
                'created_at': datetime.now(MOSCOW_TZ).isoformat()
            }

            if not recipe['name'] or not recipe['ingredients']:
                logger.error("Попытка сохранить пустой рецепт")
                await query.edit_message_text(
                    "❌ Ошибка: название или ингредиенты не указаны.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")]
                    ])
                )
                return RECIPE_INGREDIENTS

            recipes[recipe_id] = recipe
            if save_recipes(recipes):
                logger.info(f"Рецепт сохранен: {recipe['name']} (ID: {recipe_id})")
                await query.edit_message_text(
                    "✅ Рецепт успешно сохранен!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            else:
                logger.error("Ошибка при записи рецепта в файл recipes.json")
                await query.edit_message_text(
                    "❌ Ошибка при сохранении рецепта. Проверьте права доступа к файлу recipes.json.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")]
                    ])
                )

            # Очищаем данные пользователя
            context.user_data.pop('recipe_name', None)
            context.user_data.pop('ingredients', None)
            context.user_data.pop('recipe_message_id', None)
            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Критическая ошибка при сохранении рецепта: {e}")
            await query.edit_message_text(
                "❌ Произошла ошибка при сохранении рецепта.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")]
                ])
            )
            return ConversationHandler.END

    elif data == "edit_recipe":
        # Возврат к вводу названия рецепта - редактируем сообщение
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        await query.edit_message_text(
            "✏️ *Редактирование рецепта*\n\nВведите новое название рецепта:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RECIPE_NAME

    elif data == "cancel_recipe":
        await cancel_recipe_creation(query, context)
        return ConversationHandler.END

async def cancel_recipe_creation(query, context):
    """Отмена создания рецепта"""
    logger.info("Отмена создания рецепта")

    # Очищаем данные пользователя
    context.user_data.pop('recipe_name', None)
    context.user_data.pop('ingredients', None)
    context.user_data.pop('recipe_message_id', None)

    await query.edit_message_text(
        "❌ Создание рецепта отменено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
        ])
    )
    return ConversationHandler.END

async def cancel_recipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена создания рецепта через команду /cancel"""
    logger.info("Отмена создания рецепта через команду /cancel")

    # Очищаем данные пользователя
    context.user_data.pop('recipe_name', None)
    context.user_data.pop('ingredients', None)
    context.user_data.pop('recipe_message_id', None)

    await update.message.reply_text(
        "❌ Создание рецепта отменено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
        ])
    )
    return ConversationHandler.END

async def cancel_meal_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена создания плана питания"""
    logger.info("Отмена создания плана питания")
    await update.message.reply_text(
        "❌ Создание плана питания отменено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
        ])
    )
    context.user_data.clear()
    return ConversationHandler.END


async def list_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список всех рецептов"""
    query = update.callback_query
    await query.answer()
    recipes = load_recipes()

    if not recipes:
        await query.edit_message_text(
            "❌ Нет сохраненных рецептов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать рецепт", callback_data="create_recipe")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    text = "🍽 *Все рецепты:*\n\n"
    for recipe_id, recipe in recipes.items():
        text += f"📝 *{recipe['name']}*\n"
        text += "📋 Ингредиенты:\n"
        for ing in recipe['ingredients']:
            text += f"• {ing['name']} - {ing['quantity']}\n"
        text += f"👤 Создатель: {recipe.get('created_by', 'Unknown')}\n"
        text += "---\n"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать новый", callback_data="create_recipe")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ])
    )

async def list_meal_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список всех планов питания"""
    query = update.callback_query
    await query.answer()
    meal_plans = load_meal_plans()

    if not meal_plans:
        await query.edit_message_text(
            "❌ Нет запланированных блюд.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Запланировать блюдо", callback_data="plan_meal")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    text = "📅 *Все планы питания:*\n\n"
    for plan_id, plan in meal_plans.items():
        text += f"🍽 *{plan['recipe_name']}*\n"
        text += f"📅 Дата: {plan['date_str']}\n"
        text += f"👥 Распределено: {sum(1 for ing in plan['ingredients'] if ing.get('assigned_to'))}/{len(plan['ingredients'])} ингредиентов\n"
        if plan.get('with_notifications'):
            text += f"🔔 Уведомления: {NOTIFICATION_TIMES.get(plan.get('notification_time', '1_day'))}\n"
        text += f"👤 Создатель: {plan.get('created_by', 'Unknown')}\n"
        text += "---\n"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Запланировать новое", callback_data="plan_meal")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ])
    )


async def cancel_meal_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена создания плана питания"""
    logger.info("Отмена создания плана питания")

    # Очищаем данные пользователя
    context.user_data.clear()

    # Если это сообщение от пользователя (команда /cancel)
    if update.message:
        await update.message.reply_text(
            "❌ Создание плана питания отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
    # Если это callback query
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "❌ Создание плана питания отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )

    return ConversationHandler.END

    text = "📅 *Все планы питания:*\n\n"
    for plan_id, plan in meal_plans.items():
        text += f"🍽 *{plan['recipe_name']}*\n"
        text += f"📅 Дата: {plan['date_str']}\n"
        text += f"👥 Распределено: {sum(1 for ing in plan['ingredients'] if ing.get('assigned_to'))}/{len(plan['ingredients'])} ингредиентов\n"
        if plan.get('with_notifications'):
            text += f"🔔 Уведомления: {NOTIFICATION_TIMES.get(plan.get('notification_time', '1_day'))}\n"
        text += f"👤 Создатель: {plan.get('created_by', 'Unknown')}\n"
        text += "---\n"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Запланировать новое", callback_data="plan_meal")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ])
    )

async def show_week_days(query, context):
    """Показать выбор дней недели"""
    keyboard = []
    for day_key, day_name in WEEK_DAYS.items():
        keyboard.append([InlineKeyboardButton(day_name, callback_data=f"day_{day_key}")])
    keyboard.append([InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")])

    await query.edit_message_text(
        "📅 Выберите день недели для планирования:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора дня недели для планирования"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Обработка выбора дня недели: {data}")

    if data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    day_key = data.replace("day_", "")

    if day_key not in WEEK_DAYS:
        await query.edit_message_text(
            "❌ Ошибка выбора дня.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    day_name = WEEK_DAYS[day_key]

    today = datetime.now(MOSCOW_TZ)
    current_weekday = today.weekday()
    target_weekday = list(WEEK_DAYS.keys()).index(day_key)

    days_ahead = (target_weekday - current_weekday + 7) % 7
    if days_ahead == 0:
        days_ahead = 7

    meal_date = today + timedelta(days=days_ahead)
    date_str = meal_date.strftime('%d.%m.%Y')

    context.user_data['meal_day'] = day_name
    context.user_data['meal_date'] = meal_date
    context.user_data['meal_date_str'] = date_str

    await query.edit_message_text(
        f"📅 Выбрано: {day_name} ({date_str})\n\n"
        "Теперь выберите рецепт для планирования."
    )

    await show_available_recipes(query, context)
    return MEAL_RECIPE

async def show_available_recipes(query, context):
    """Показать доступные рецепты"""
    recipes = load_recipes()

    if not recipes:
        await query.edit_message_text(
            "❌ Нет рецептов для планирования.\n"
            "Сначала создайте рецепт!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать рецепт", callback_data="create_recipe")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    keyboard = []
    for recipe_id, recipe in recipes.items():
        keyboard.append([
            InlineKeyboardButton(
                f"🍽 {recipe['name']}",
                callback_data=f"recipe_{recipe_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад к дням", callback_data="back_to_days")])
    keyboard.append([InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")])

    await query.edit_message_text(
        "Выберите рецепт для планирования:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_recipe_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора рецепта для планирования"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "back_to_days":
        await show_week_days(query, context)
        return MEAL_DAY
    elif data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    recipe_id = data.replace("recipe_", "")
    recipes = load_recipes()
    recipe = recipes.get(recipe_id)

    if not recipe:
        await query.edit_message_text(
            "❌ Рецепт не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    meal_plan = {
        'recipe_id': recipe_id,
        'recipe_name': recipe['name'],
        'date': context.user_data['meal_date'],
        'date_str': context.user_data['meal_date_str'],
        'ingredients': recipe['ingredients'].copy(),
        'day': context.user_data['meal_day']
    }

    context.user_data['meal_plan'] = meal_plan

    await show_ingredient_assignment(query, context)
    return INGREDIENT_ASSIGNMENT

async def show_ingredient_assignment(query, context):
    """Показать распределение ингредиентов"""
    meal_plan = context.user_data['meal_plan']
    users = load_users()

    text = f"🍽 *Распределение ингредиентов*\n\n"
    text += f"Блюдо: *{meal_plan['recipe_name']}*\n"
    text += f"Дата: *{meal_plan['date_str']}*\n\n"
    text += "*Ингредиенты:*\n"

    keyboard = []

    for i, ingredient in enumerate(meal_plan['ingredients']):
        assigned_to = ingredient.get('assigned_to')
        assigned_name = "Не назначено"
        if assigned_to:
            user_data = users.get(assigned_to, {})
            assigned_name = user_data.get('username', 'Unknown')

        text += f"• {ingredient['name']} - {ingredient['quantity']} → {assigned_name}\n"

        keyboard.append([
            InlineKeyboardButton(
                f"📝 Назначить: {ingredient['name']}",
                callback_data=f"assign_ing_{i}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("✅ Завершить распределение", callback_data="finish_assignment")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")
    ])

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_ingredient_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка распределения ингредиентов"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    if data == "back_to_recipe_selection":
        await show_available_recipes(query, context)
        return MEAL_RECIPE

    if data == "finish_assignment":
        await finish_ingredient_assignment(query, context)
        return ConversationHandler.END

    if data.startswith("assign_ing_"):
        ing_index = int(data.replace("assign_ing_", ""))
        context.user_data['current_ing_index'] = ing_index
        await show_user_selection_for_ingredient(query, context)
        return INGREDIENT_ASSIGNMENT

async def show_user_selection_for_ingredient(query, context):
    """Показать выбор пользователя для ингредиента"""
    users = load_users()
    ing_index = context.user_data['current_ing_index']
    meal_plan = context.user_data['meal_plan']
    ingredient = meal_plan['ingredients'][ing_index]

    keyboard = []

    for user_id, user_data in users.items():
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {user_data['username']}",
                callback_data=f"select_user_{user_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Никто", callback_data="select_user_none")
    ])

    keyboard.append([
        InlineKeyboardButton("🔙 Назад к распределению", callback_data="back_to_assignment")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")
    ])

    await query.edit_message_text(
        f"👤 *Назначение покупателя*\n\n"
        f"Ингредиент: *{ingredient['name']} - {ingredient['quantity']}*\n\n"
        f"Выберите, кто будет покупать этот ингредиент:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_user_selection_for_ingredient(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора пользователя для ингредиента с поддержкой многократного редактирования"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "back_to_assignment":
        # Определяем, в каком режиме мы находимся
        if context.user_data.get('editing_plan_id'):
            await show_edit_ingredient_assignment(query, context)
        else:
            await show_ingredient_assignment(query, context)
        return

    if data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return

    ing_index = context.user_data.get('current_ing_index')
    if ing_index is None:
        await query.edit_message_text(
            "❌ Ошибка: индекс ингредиента не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Определяем, в каком режиме мы находимся
    if context.user_data.get('editing_plan_id'):
        meal_plan = context.user_data['meal_plan']
    else:
        meal_plan = context.user_data.get('meal_plan', {})

    if not meal_plan:
        await query.edit_message_text(
            "❌ Ошибка: план питания не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    ingredient = meal_plan['ingredients'][ing_index]

    if data == "select_user_none":
        if 'assigned_to' in ingredient:
            del ingredient['assigned_to']
    else:
        user_id = data.replace("select_user_", "")
        users = load_users()

        ingredient['assigned_to'] = user_id

    meal_plan['ingredients'][ing_index] = ingredient
    context.user_data['meal_plan'] = meal_plan

    # Сохраняем изменения сразу для режима редактирования
    if context.user_data.get('editing_plan_id'):
        plan_id = context.user_data['editing_plan_id']
        meal_plans = load_meal_plans()
        if plan_id in meal_plans:
            meal_plans[plan_id]['ingredients'] = meal_plan['ingredients']
            meal_plans[plan_id]['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()
            save_meal_plans(meal_plans)

    # Возвращаемся к соответствующему экрану
    if context.user_data.get('editing_plan_id'):
        await show_edit_ingredient_assignment(query, context)
    else:
        await show_ingredient_assignment(query, context)

async def finish_ingredient_assignment(query, context):
    """Завершение распределения ингредиентов"""
    meal_plan = context.user_data['meal_plan']

    assigned_count = sum(1 for ing in meal_plan['ingredients'] if ing.get('assigned_to'))
    total_ingredients = len(meal_plan['ingredients'])

    text = f"📅 *Планирование завершено!*\n\n"
    text += f"🍽 *{meal_plan['recipe_name']}*\n"
    text += f"📅 Дата: *{meal_plan['date_str']}*\n"
    text += f"👥 Распределено: {assigned_count}/{total_ingredients} ингредиентов\n\n"

    if assigned_count < total_ingredients:
        text += "⚠️ *Внимание:* Не все ингредиенты распределены!\n"
        text += "Вы можете продолжить распределение или сохранить как есть.\n\n"

    keyboard = [
        [InlineKeyboardButton("⏰ Настроить уведомления", callback_data="setup_notifications")],
        [InlineKeyboardButton("💾 Сохранить без уведомлений", callback_data="save_without_notifications")],
        [InlineKeyboardButton("✏️ Продолжить распределение", callback_data="continue_assignment")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
async def handle_assignment_completion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка завершения распределения ингредиентов"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Обработка завершения распределения: {data}")

    if data == "continue_assignment":
        await show_ingredient_assignment(query, context)
        return

    elif data == "setup_notifications":
        await show_notification_options(query, context)
        return

    elif data == "save_without_notifications":
        await save_meal_plan_without_notifications(query, context)
        return

async def show_notification_options(query, context):
    """Показать варианты уведомлений"""
    keyboard = []

    for time_key, time_text in NOTIFICATION_TIMES.items():
        keyboard.append([
            InlineKeyboardButton(f"⏰ {time_text}", callback_data=f"notify_{time_key}")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_assignment_completion")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")
    ])

    await query.edit_message_text(
        "🔔 *Настройка уведомлений*\n\n"
        "Выберите, за сколько времени присылать уведомления о необходимости покупки ингредиентов:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_notification_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора времени уведомлений"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Обработка выбора уведомления: {data}")

    if data == "back_to_assignment_completion":
        await finish_ingredient_assignment(query, context)
        return

    if data.startswith("notify_"):
        notification_time = data.replace("notify_", "")
        context.user_data['meal_plan']['notification_time'] = notification_time
        logger.info(f"Выбрано время уведомления: {notification_time}")

        await save_meal_plan_with_notifications(query, context)

async def save_meal_plan_without_notifications(update_or_query, context):
    """Сохранение плана питания без уведомлений"""
    try:
        meal_plan = context.user_data['meal_plan']
        meal_plan_id = str(int(datetime.now().timestamp()))

        # Преобразуем datetime в строку для сохранения в JSON
        meal_date = meal_plan['date']
        if hasattr(meal_date, 'strftime'):
            meal_date_str = meal_date.isoformat()
        else:
            meal_date_str = meal_date

        meal_plan['id'] = meal_plan_id

        # Определяем ID создателя
        if hasattr(update_or_query, 'from_user'):
            meal_plan['created_by'] = str(update_or_query.from_user.id)
        else:
            meal_plan['created_by'] = str(update_or_query.message.from_user.id)

        meal_plan['created_at'] = datetime.now(MOSCOW_TZ).isoformat()
        meal_plan['with_notifications'] = False
        meal_plan['date'] = meal_date_str  # Сохраняем как строку

        meal_plans = load_meal_plans()
        meal_plans[meal_plan_id] = meal_plan

        if not save_meal_plans(meal_plans):
            logger.error("Ошибка при записи плана питания в файл meal_plans.json")
            text = "❌ Ошибка при сохранении плана питания. Проверьте права доступа к файлу meal_plans.json."
            keyboard = [[InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]]

            if hasattr(update_or_query, 'edit_message_text'):
                await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = f"📅 *План питания сохранен!*\n\n"
        text += f"🍽 *{meal_plan['recipe_name']}*\n"
        text += f"📅 Дата: {meal_plan['date_str']}\n\n"

        assigned_count = sum(1 for ing in meal_plan['ingredients'] if ing.get('assigned_to'))
        text += f"👥 Распределено ингредиентов: {assigned_count}/{len(meal_plan['ingredients'])}\n\n"
        text += "ℹ️ *Уведомления не настроены.* Вы можете добавить их позже через меню управления планами."

        keyboard = [
            [InlineKeyboardButton("📅 Запланировать еще", callback_data="plan_meal")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update_or_query.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        # Очищаем данные пользователя
        context.user_data.pop('meal_plan', None)
        context.user_data.pop('current_ing_index', None)
        context.user_data.pop('meal_day', None)
        context.user_data.pop('meal_date', None)
        context.user_data.pop('meal_date_str', None)

    except Exception as e:
        logger.error(f"Ошибка сохранения плана без уведомлений: {e}")
        text = "❌ Произошла ошибка при сохранении плана питания."
        keyboard = [[InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]]

        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def save_meal_plan_with_notifications(update_or_query, context):
    """Сохранение плана питания с уведомлениями"""
    try:
        meal_plan = context.user_data['meal_plan']
        meal_plan_id = str(int(datetime.now().timestamp()))

        # Преобразуем datetime в строку для сохранения в JSON
        meal_date = meal_plan['date']
        if hasattr(meal_date, 'strftime'):
            meal_date_str = meal_date.isoformat()
        else:
            meal_date_str = meal_date

        meal_plan['id'] = meal_plan_id

        # Определяем ID создателя
        if hasattr(update_or_query, 'from_user'):
            meal_plan['created_by'] = str(update_or_query.from_user.id)
        else:
            meal_plan['created_by'] = str(update_or_query.message.from_user.id)

        meal_plan['created_at'] = datetime.now(MOSCOW_TZ).isoformat()
        meal_plan['with_notifications'] = True
        meal_plan['notification_time'] = meal_plan.get('notification_time', '1_day')
        meal_plan['date'] = meal_date_str  # Сохраняем как строку

        meal_plans = load_meal_plans()
        meal_plans[meal_plan_id] = meal_plan

        if not save_meal_plans(meal_plans):
            logger.error("Ошибка при записи плана питания в файл meal_plans.json")
            text = "❌ Ошибка при сохранении плана питания. Проверьте права доступа к файлу meal_plans.json."
            keyboard = [[InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]]

            if hasattr(update_or_query, 'edit_message_text'):
                await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        reminders_created = await create_ingredient_reminders(meal_plan, context)

        text = f"🎉 *План питания сохранен!*\n\n"
        text += f"🍽 *{meal_plan['recipe_name']}*\n"
        text += f"📅 Дата: {meal_plan['date_str']}\n"
        text += f"🔔 Уведомления: *{NOTIFICATION_TIMES[meal_plan['notification_time']]}*\n\n"

        assigned_count = sum(1 for ing in meal_plan['ingredients'] if ing.get('assigned_to'))
        text += f"👥 Распределено ингредиентов: {assigned_count}/{len(meal_plan['ingredients'])}\n"
        text += f"🔔 Создано напоминаний: {reminders_created}\n\n"
        text += "*Все участники получат уведомления о необходимости покупки своих ингредиентов в указанное время!*"

        keyboard = [
            [InlineKeyboardButton("📅 Запланировать еще", callback_data="plan_meal")],
            [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
        ]

        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update_or_query.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        # Очищаем данные пользователя
        context.user_data.pop('meal_plan', None)
        context.user_data.pop('current_ing_index', None)
        context.user_data.pop('meal_day', None)
        context.user_data.pop('meal_date', None)
        context.user_data.pop('meal_date_str', None)

    except Exception as e:
        logger.error(f"Ошибка сохранения плана с уведомлениями: {e}")
        text = "❌ Произошла ошибка при сохранении плана питания."
        keyboard = [[InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]]

        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def create_ingredient_reminders(meal_plan, application):
    """Создание напоминаний для ингредиентов с привязкой к плану питания"""
    try:
        reminders_created = 0
        reminders = load_reminders()
        users = load_users()

        # Восстанавливаем дату из строки если нужно
        meal_date = meal_plan['date']
        if isinstance(meal_date, str):
            meal_date = datetime.fromisoformat(meal_date)

        notification_time = meal_plan.get('notification_time', '1_day')

        days_before = {
            '1_day': 1,
            '2_days': 2,
            '3_days': 3,
            '1_week': 7
        }.get(notification_time, 1)

        # ВАЖНОЕ ИЗМЕНЕНИЕ: Рассчитываем дату напоминания
        reminder_date = meal_date - timedelta(days=days_before)

        # Текущее время для сравнения
        current_time = datetime.now(MOSCOW_TZ)

        # Если дата напоминания уже прошла, устанавливаем на сегодня в удобное время
        if reminder_date.date() < current_time.date():
            # Устанавливаем напоминание на сегодня, но не раньше чем через 5 минут
            reminder_datetime = current_time + timedelta(minutes=5)
            logger.info(f"⏰ Дата напоминания прошла, установлено на сегодня: {reminder_datetime.strftime('%d.%m.%Y %H:%M')}")
        elif reminder_date.date() == current_time.date():
            # Если напоминание должно быть сегодня, проверяем время
            reminder_datetime = reminder_date.replace(hour=10, minute=0, second=0)
            if reminder_datetime < current_time:
                # Если 10:00 уже прошло, устанавливаем на ближайшие 5 минут
                reminder_datetime = current_time + timedelta(minutes=5)
                logger.info(f"⏰ Время напоминания прошло, установлено на ближайшие минуты: {reminder_datetime.strftime('%d.%m.%Y %H:%M')}")
            else:
                logger.info(f"⏰ Напоминание установлено на сегодня в 10:00: {reminder_datetime.strftime('%d.%m.%Y %H:%M')}")
        else:
            # Напоминание в будущем - устанавливаем на 10:00
            reminder_datetime = reminder_date.replace(hour=10, minute=0, second=0)
            logger.info(f"⏰ Напоминание установлено на будущее: {reminder_datetime.strftime('%d.%m.%Y %H:%M')}")

        # УДАЛЯЕМ СТАРЫЕ НАПОМИНАНИЯ ДЛЯ ЭТОГО ПЛАНА (если они есть)
        reminders_to_delete = []
        for reminder_id, reminder in reminders.items():
            if reminder.get('meal_plan_id') == meal_plan['id'] and reminder.get('type') == 'ingredient':
                reminders_to_delete.append(reminder_id)

        for reminder_id in reminders_to_delete:
            del reminders[reminder_id]
            logger.info(f"Удалено старое напоминание ингредиента {reminder_id}")

        # СОЗДАЕМ НОВЫЕ НАПОМИНАНИЯ
        for ingredient in meal_plan['ingredients']:
            if ingredient.get('assigned_to'):
                reminder_id = f"ingredient_{meal_plan['id']}_{ingredient['id']}_{int(datetime.now().timestamp())}"

                assigned_user = users.get(ingredient['assigned_to'], {})
                assigned_username = assigned_user.get('username', 'Unknown')

                # УЛУЧШЕННЫЙ ФОРМАТ ТЕКСТА НАПОМИНАНИЯ
                reminder_text = (
                    f"• {ingredient['name']} - {ingredient['quantity']}\n"
                    f"📅 Дата приготовления: {meal_plan['date_str']}\n"
                    f"🍽 Блюдо: {meal_plan['recipe_name']}\n"
                    f"👤 Ответственный: {assigned_username}"
                )

                # Добавляем пометку, если напоминание срочное или автоматически созданное
                if reminder_datetime.date() == current_time.date() and reminder_datetime > current_time:
                    reminder_text += f"\n\n🚨 *СРОЧНОЕ* (напоминание создано позже запланированного времени)"

                if meal_plan.get('is_auto_created'):
                    reminder_text += f"\n\n🔄 *АВТОМАТИЧЕСКИ СОЗДАНО* (план на следующую неделю)"

                reminder = {
                    'id': reminder_id,
                    'text': reminder_text,
                    'datetime': reminder_datetime.isoformat(),
                    'interval_days': 0,
                    'users': [ingredient['assigned_to']],
                    'created_by': meal_plan['created_by'],
                    'created_at': datetime.now(MOSCOW_TZ).isoformat(),
                    'type': 'ingredient',
                    'meal_plan_id': meal_plan['id'],
                    'ingredient_id': ingredient['id'],
                    'recipe_name': meal_plan['recipe_name'],
                    'meal_date': meal_plan['date_str'],
                    'frequency_multiplier': 1,
                    'not_bought_count': 0,
                    'confirmed_by': set(),
                    'postponed_by': set(),
                    'delete_confirmed_by': set(),
                    'urgent_reminders': False,
                    'urgent_until': None,
                    'last_sent': None
                }

                reminders[reminder_id] = reminder
                reminders_created += 1

                logger.info(f"Создано напоминание для ингредиента: {ingredient['name']} → {assigned_username} (время: {reminder_datetime.strftime('%d.%m.%Y %H:%M')})")

        if reminders_created > 0:
            if not save_reminders(reminders):
                logger.error("Ошибка при записи напоминаний в файл reminders.json")
                return 0
            logger.info(f"Создано {reminders_created} напоминаний для плана питания {meal_plan['id']}")

        return reminders_created

    except Exception as e:
        logger.error(f"Ошибка создания напоминаний для ингредиентов: {e}")
        return 0

async def create_next_week_meal_plan(application, current_plan_id):
    """Создает копию плана питания на следующую неделю с СОХРАНЕНИЕМ распределения ингредиентов"""
    try:
        meal_plans = load_meal_plans()
        current_plan = meal_plans.get(current_plan_id)

        # Если план не найден, возможно он уже был удален при обработке другого ингредиента
        # Проверим, не был ли уже создан план на следующую неделю для этого рецепта
        if not current_plan:
            logger.warning(f"⚠️ План питания {current_plan_id} не найден, возможно уже удален")

            # Попробуем найти план на следующую неделю для этого рецепта
            # Для этого нам нужно знать recipe_id, но у нас его нет
            # Вместо этого просто вернем успех, так как план уже должен быть создан
            return "plan_already_exists"

        # Проверяем обязательные поля
        required_fields = ['recipe_id', 'recipe_name', 'date', 'date_str', 'ingredients']
        missing_fields = [field for field in required_fields if field not in current_plan]
        if missing_fields:
            logger.error(f"❌ Отсутствуют обязательные поля в плане {current_plan_id}: {missing_fields}")
            return None

        # Получаем текущую дату плана
        current_plan_date = current_plan['date']
        if isinstance(current_plan_date, str):
            current_plan_date = datetime.fromisoformat(current_plan_date)

        # Вычисляем дату на следующую неделю (тот же день недели)
        next_week_date = current_plan_date + timedelta(days=7)
        next_week_date_str = next_week_date.strftime('%d.%m.%Y')

        # Проверяем, существует ли уже план на следующую неделю для этого рецепта
        existing_plan_id = None
        for plan_id, plan in meal_plans.items():
            if (plan.get('recipe_id') == current_plan.get('recipe_id') and
                plan.get('date_str') == next_week_date_str and
                plan_id != current_plan_id):
                existing_plan_id = plan_id
                logger.info(f"✅ План на следующую неделю уже существует: {existing_plan_id}")
                break

        # Если план уже существует, просто удаляем текущий и возвращаем успех
        if existing_plan_id:
            # УДАЛЯЕМ ТЕКУЩИЙ ПЛАН (если он еще существует)
            if current_plan_id in meal_plans:
                del meal_plans[current_plan_id]
                if save_meal_plans(meal_plans):
                    logger.info(f"🗑 Старый план {current_plan_id} удален, используется существующий {existing_plan_id}")
                else:
                    logger.error(f"❌ Ошибка при удалении старого плана {current_plan_id}")
            return "plan_already_exists"

        # Создаем новый план на следующую неделю
        new_plan_id = str(int(datetime.now().timestamp()))

        # ГЛУБОКОЕ КОПИРОВАНИЕ плана с сохранением распределения ингредиентов
        new_plan = {
            'id': new_plan_id,
            'recipe_id': current_plan['recipe_id'],
            'recipe_name': current_plan['recipe_name'],
            'date': next_week_date.isoformat(),
            'date_str': next_week_date_str,
            'day': current_plan['day'],
            'ingredients': [],
            'created_by': current_plan.get('created_by', 'unknown'),
            'created_at': datetime.now(MOSCOW_TZ).isoformat(),
            'is_auto_created': True,
            'with_notifications': current_plan.get('with_notifications', False),
            'notification_time': current_plan.get('notification_time', '1_day')
        }

        # ГЛУБОКОЕ КОПИРОВАНИЕ ингредиентов с сохранением назначений
        for ingredient in current_plan['ingredients']:
            new_ingredient = ingredient.copy()
            # Сохраняем назначение пользователя если оно было
            if 'assigned_to' in ingredient:
                new_ingredient['assigned_to'] = ingredient['assigned_to']
            new_plan['ingredients'].append(new_ingredient)

        logger.info(f"🔄 Создан новый план с сохранением распределения: {len(new_plan['ingredients'])} ингредиентов")

        # УДАЛЯЕМ ТЕКУЩИЙ ПЛАН ПЕРЕД СОЗДАНИЕМ НОВОГО
        if current_plan_id in meal_plans:
            del meal_plans[current_plan_id]

        # Сохраняем новый план
        meal_plans[new_plan_id] = new_plan

        if not save_meal_plans(meal_plans):
            logger.error("❌ Ошибка при сохранении плана на следующую неделю")
            return None

        logger.info(f"✅ Создан план на следующую неделю: {new_plan['recipe_name']} на {next_week_date_str}")
        logger.info(f"🗑 Старый план {current_plan_id} удален")

        # Создаем напоминания для ингредиентов нового плана (только если были уведомления)
        reminders_created = 0
        if new_plan.get('with_notifications'):
            try:
                reminders_created = await create_ingredient_reminders(new_plan, application)
                logger.info(f"✅ Создано напоминаний для плана на следующую неделю: {reminders_created}")
            except Exception as e:
                logger.error(f"⚠️ Ошибка при создании напоминаний, но план создан: {e}")
        else:
            logger.info("ℹ️ Уведомления отключены, напоминания не созданы")

        return new_plan_id

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при создании плана на следующую неделю: {e}")
        return None

async def check_ingredient_reminders(application):
    """Проверка и отправка напоминаний для ингредиентов с замещением срочных сообщений"""
    try:
        reminders = load_reminders()
        current_time = datetime.now(MOSCOW_TZ)

        # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ
        current_hour = current_time.hour
        is_night_time = current_hour >= 23 or current_hour < 9

        ingredient_reminders = {rid: rem for rid, rem in reminders.items()
                              if rem.get('type') == 'ingredient'}

        if not ingredient_reminders:
            return 0

        sent_count = 0
        reminders_to_remove = []

        for reminder_id, reminder in ingredient_reminders.items():
            try:
                # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ ДЛЯ СРОЧНЫХ НАПОМИНАНИЙ ИНГРЕДИЕНТОВ
                if is_night_time and reminder.get('urgent_reminders'):
                    logger.info(f"🌙 Пропущена проверка срочного ингредиента в ночное время: {reminder_id}")
                    continue
                # ПРОВЕРКА: УДАЛЕНИЕ ИНГРЕДИЕНТОВ ПОСЛЕ НАСТУПЛЕНИЯ ДНЯ ПРИГОТОВЛЕНИЯ
                meal_date_str = reminder.get('meal_date')
                if meal_date_str:
                    try:
                        # Парсим дату приготовления (формат: DD.MM.YYYY)
                        meal_date = datetime.strptime(meal_date_str, '%d.%m.%Y').replace(tzinfo=MOSCOW_TZ)

                        # Если дата приготовления уже прошла (учитываем начало дня)
                        if meal_date.date() < current_time.date():
                            # УДАЛЯЕМ НАПОМИНАНИЕ И СООБЩЕНИЯ ВНЕ ЗАВИСИМОСТИ ОТ СРОЧНОГО РЕЖИМА
                            reminders_to_remove.append(reminder_id)
                            await delete_old_reminder_messages(application, reminder_id)
                            logger.info(f"🗑 Напоминание ингредиента {reminder_id} удалено после наступления дня приготовления")
                            continue
                    except ValueError as e:
                        logger.error(f"❌ Ошибка парсинга даты приготовления {meal_date_str}: {e}")

                reminder_time = datetime.fromisoformat(reminder['datetime']).replace(tzinfo=MOSCOW_TZ)
                time_diff_minutes = (reminder_time - current_time).total_seconds() / 60

                # ПРОВЕРКА ИСТЕЧЕНИЯ СРОЧНОГО РЕЖИМА ДЛЯ ИНГРЕДИЕНТОВ (только если день приготовления еще не наступил)
                urgent_until = reminder.get('urgent_until')
                if urgent_until:
                    urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                    if current_time > urgent_until_time:
                        # СРОЧНЫЙ РЕЖИМ ИСТЕК - но для ингредиентов мы НЕ удаляем напоминание,
                        # а только снимаем срочный режим, так как удаление происходит по дате приготовления
                        reminder['urgent_reminders'] = False
                        reminder['urgent_until'] = None
                        reminder['last_sent'] = None
                        logger.info(f"🔄 Срочный режим истек для ингредиента {reminder_id}, но напоминание остается до дня приготовления")
                        # Продолжаем обработку для обычного режима

                should_send = False
                send_reason = ""
                is_urgent_update = False  # Флаг для замещения сообщений

                # Для срочных напоминаний ингредиентов (только если день приготовления еще не наступил)
                if reminder.get('urgent_reminders'):
                    last_sent = reminder.get('last_sent')

                    if not last_sent:
                        should_send = True
                        send_reason = "первое срочное напоминание ингредиента"
                        is_urgent_update = True
                    else:
                        last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                        hours_since_last = (current_time - last_sent_time).total_seconds() / 3600

                        if hours_since_last >= 3:
                            should_send = True
                            send_reason = f"срочное напоминание ингредиента (прошло {hours_since_last:.1f} ч.)"
                            is_urgent_update = True  # ВКЛЮЧАЕМ ЗАМЕЩЕНИЕ ДЛЯ ПОВТОРНЫХ СРОЧНЫХ

                # Для обычных напоминаний ингредиентов
                elif not reminder.get('urgent_reminders'):
                    last_sent = reminder.get('last_sent')

                    # Если напоминание уже отправлялось сегодня, пропускаем
                    if last_sent:
                        last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                        if last_sent_time.date() == current_time.date():
                            continue

                    # Отправляем если время напоминания в пределах ±30 минут
                    if -30 <= time_diff_minutes <= 30:
                        should_send = True
                        send_reason = "обычное напоминание ингредиента"

                if should_send:
                    logger.info(f"⏰ ОТПРАВКА ИНГРЕДИЕНТА ({send_reason}): {reminder['text'][:50]}...")

                    # ОТПРАВЛЯЕМ С ФЛАГОМ ЗАМЕЩЕНИЯ ДЛЯ СРОЧНЫХ НАПОМИНАНИЙ
                    await send_ingredient_reminder_notification(application, reminder, is_urgent_update=is_urgent_update)
                    sent_count += 1

                    reminder['last_sent'] = current_time.isoformat()

                    # Для срочных напоминаний планируем следующее
                    if reminder.get('urgent_reminders'):
                        next_time = current_time + timedelta(hours=3)
                        if next_time.hour >= 23 or next_time.hour < 9:
                            next_time = next_time.replace(hour=9, minute=0, second=0)
                            if next_time <= current_time:
                                next_time += timedelta(days=1)
                        reminder['datetime'] = next_time.isoformat()
                        logger.info(f"🔁 Следующее срочное напоминание ингредиента через 3 часа: {next_time.strftime('%d.%m.%Y %H:%M')}")

            except Exception as e:
                logger.error(f"Ошибка обработки напоминания ингредиента {reminder_id}: {e}")
                continue

        # Удаляем напоминания с наступившей датой приготовления
        for reminder_id in reminders_to_remove:
            if reminder_id in reminders:
                del reminders[reminder_id]
                logger.info(f"✅ Удален ингредиент с наступившей датой приготовления: {reminder_id}")

        # Сохраняем изменения
        if sent_count > 0 or reminders_to_remove:
            if not save_reminders(reminders):
                logger.error("Ошибка при записи напоминаний в файл reminders.json")
            logger.info(f"📤 Отправлено напоминаний ингредиентов: {sent_count}, удалено: {len(reminders_to_remove)}")

        return sent_count

    except Exception as e:
        logger.error(f"Ошибка в check_ingredient_reminders: {e}")
        return 0

async def send_ingredient_reminder_notification(application, reminder, is_urgent_update=False, is_missed=False):
    """Отправка уведомления о необходимости покупки ингредиента с проверкой ночного времени"""
    try:
        current_time = datetime.now(MOSCOW_TZ)

        # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ ДЛЯ ВСЕХ ТИПОВ НАПОМИНАНИЙ
        current_hour = current_time.hour
        is_night_time = current_hour >= 23 or current_hour < 9

        # Если ночное время и это не пропущенное напоминание, пропускаем отправку
        if is_night_time and not is_missed:
            logger.info(f"🌙 Пропущена отправка ингредиента в ночное время (сейчас {current_time.strftime('%H:%M')})")
            return

        # ЕСЛИ ЭТО ОБНОВЛЕНИЕ СРОЧНОГО НАПОМИНАНИЯ - УДАЛЯЕМ СТАРЫЕ СООБЩЕНИЯ
        if is_urgent_update:
            await delete_old_reminder_messages(application, reminder['id'])
            logger.info(f"🗑 Удалены старые сообщения для срочного напоминания ингредиента {reminder['id']}")

        keyboard = [
            [
                InlineKeyboardButton("✅ Купил", callback_data=f"bought_{reminder['id']}"),
                InlineKeyboardButton("❌ Еще не купил", callback_data=f"not_bought_{reminder['id']}")
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Базовый текст
        if is_missed:
            message_text = f"⏰ *ПРОПУЩЕННОЕ НАПОМИНАНИЕ О ПОКУПКЕ!*\n\n"
        else:
            message_text = f"🛒 *НАПОМИНАНИЕ О ПОКУПКЕ!*\n\n"

        # Текст напоминания
        message_text += f"{reminder['text']}\n\n"

        # Информация о срочности
        if reminder.get('urgent_reminders'):
            urgent_until = reminder.get('urgent_until')
            if urgent_until:
                urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                time_left = urgent_until_time - current_time
                hours_left = max(0, int(time_left.total_seconds() / 3600))
                message_text += f"🚨 *СРОЧНОЕ* (осталось {hours_left}ч.)\n\n"
            else:
                message_text += "🚨 *СРОЧНОЕ* (повтор каждые 3 часа)\n\n"
        else:
            message_text += "\n"

        # Дополнительная информация для пропущенных напоминаний
        if is_missed:
            message_text += "💡 *Примечание:* Это напоминание должно было прийти ранее, но было пропущено.\n\n"

        # Совет
        message_text += "💡 *Совет:* Купите ингредиент заранее, чтобы все было готово к приготовлению!"

        # Отправляем каждому пользователю и сохраняем ID сообщений
        for user_id in reminder['users']:
            try:
                # Преобразуем user_id в int
                try:
                    user_id_int = int(user_id)
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ Неверный формат user_id: {user_id}, ошибка: {e}")
                    continue

                # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ (23:00 - 9:00)
                current_hour = current_time.hour

                # Если ночное время (23:00 - 9:00) и это не срочное напоминание, пропускаем отправку
                if not reminder.get('urgent_reminders') and (current_hour >= 23 or current_hour < 9):
                    logger.info(f"🌙 Пропущена отправка в ночное время для пользователя {user_id_int} (сейчас {current_time.strftime('%H:%M')})")
                    continue

                message = await application.bot.send_message(
                    chat_id=user_id_int,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )

                # Сохраняем ID нового сообщения с правильным форматом
                save_message_id(reminder['id'], user_id_int, message.message_id)

                logger.info(f"✅ Уведомление ингредиента отправлено пользователю {user_id_int} с message_id {message.message_id}")

            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")

    except Exception as e:
        logger.error(f"❌ Ошибка в send_ingredient_reminder_notification: {e}")

async def edit_recipes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Меню редактирования рецептов"""
    query = update.callback_query
    await query.answer()

    recipes = load_recipes()

    if not recipes:
        await query.edit_message_text(
            "❌ Нет рецептов для редактирования.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать рецепт", callback_data="create_recipe")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    keyboard = []
    for recipe_id, recipe in recipes.items():
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {recipe['name']}",
                callback_data=f"edit_recipe_{recipe_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")])

    await query.edit_message_text(
        "📝 *Редактирование рецептов*\n\nВыберите рецепт для редактирования:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def send_missed_reminders(application):
    """Отправляет напоминания, которые должны были прийти за последние 24 часа, включая ингредиенты"""
    try:
        reminders = load_reminders()
        users = load_users()
        current_time = datetime.now(MOSCOW_TZ)

        # Время, с которого проверяем пропущенные напоминания (24 часа назад)
        check_from_time = current_time - timedelta(hours=24)

        sent_count = 0
        reminders_to_update = []

        logger.info(f"🔍 Поиск пропущенных напоминаний за последние 24 часа (с {check_from_time.strftime('%d.%m.%Y %H:%M')})")

        for reminder_id, reminder in reminders.items():
            try:
                reminder_time = datetime.fromisoformat(reminder['datetime']).replace(tzinfo=MOSCOW_TZ)

                # Проверяем, должно ли было напоминание прийти в последние 24 часа
                if check_from_time <= reminder_time <= current_time:
                    last_sent = reminder.get('last_sent')

                    # Если напоминание еще не отправлялось
                    if not last_sent:
                        logger.info(f"⏰ Найдено пропущенное напоминание: {reminder['text'][:50]}... (время: {reminder_time.strftime('%d.%m.%Y %H:%M')})")

                        # Для ингредиентов
                        if reminder.get('type') == 'ingredient':
                            await send_ingredient_reminder_notification(application, reminder, is_missed=True)
                        else:
                            # Для обычных напоминаний
                            await send_reminder_notification(application, reminder, users, is_missed=True)

                        sent_count += 1

                        # Обновляем время последней отправки
                        reminder['last_sent'] = current_time.isoformat()

                        # Для интервальных напоминаний планируем следующее
                        if reminder.get('type') != 'ingredient':  # Ингредиенты однократные
                            interval_days = reminder.get('interval_days', 0)
                            if interval_days > 0:
                                next_reminder_time = reminder_time + timedelta(days=interval_days)

                                # Если следующее напоминание тоже в прошлом, вычисляем ближайшее будущее
                                while next_reminder_time <= current_time:
                                    next_reminder_time += timedelta(days=interval_days)

                                reminder['datetime'] = next_reminder_time.isoformat()
                                logger.info(f"🔄 Интервальное напоминание перенесено на: {next_reminder_time.strftime('%d.%m.%Y %H:%M')}")

                        reminders_to_update.append(reminder_id)

            except Exception as e:
                logger.error(f"❌ Ошибка обработки пропущенного напоминания {reminder_id}: {e}")
                continue

        # Сохраняем изменения
        if reminders_to_update:
            if not save_reminders(reminders):
                logger.error("❌ Ошибка при сохранении обновленных напоминаний")
            else:
                logger.info(f"✅ Сохранены обновления для {len(reminders_to_update)} напоминаний")

        if sent_count > 0:
            logger.info(f"📤 Отправлено пропущенных напоминаний: {sent_count}")
        else:
            logger.info("✅ Пропущенных напоминаний не найдено")

        return sent_count

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в send_missed_reminders: {e}")
        return 0

async def check_all_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Объединенная проверка всех типов напоминаний с автоматической очисткой"""
    try:
        application = context.application
        total_sent = 0

        # Загружаем текущие напоминания для проверки актуальности
        current_reminders = load_reminders()

        # СНАЧАЛА автоматически очищаем прошедшие напоминания и создаем новые планы
        cleaned_count = await cleanup_past_meal_plans_and_reminders(application)
        if cleaned_count > 0:
            logger.info(f"🧹 Автоматически очищено {cleaned_count} прошедших напоминаний")

        # УДАЛЯЕМ НЕАКТУАЛЬНЫЕ СООБЩЕНИЯ (которых нет в current_reminders)
        old_messages_deleted = await cleanup_old_messages(application, current_reminders)
        if old_messages_deleted > 0:
            logger.info(f"🗑 Удалено {old_messages_deleted} неактуальных сообщений")

        # ПОТОМ проверяем и отправляем пропущенные напоминания
        missed_sent = await send_missed_reminders(application)
        total_sent += missed_sent

        # Проверка обычных напоминаний
        regular_sent = await check_regular_reminders(application)
        total_sent += regular_sent

        # Проверка напоминаний ингредиентов (включая срочные)
        ingredient_sent = await check_ingredient_reminders(application)
        total_sent += ingredient_sent

        if total_sent > 0:
            logger.info(f"✅ Всего отправлено напоминаний: {total_sent} (пропущенные: {missed_sent}, обычные: {regular_sent}, ингредиенты: {ingredient_sent})")

        return total_sent

    except Exception as e:
        logger.error(f"❌ Ошибка в check_all_reminders: {e}")
        return 0

async def start_recipe_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало редактирования рецепта"""
    query = update.callback_query
    await query.answer()

    recipe_id = query.data.replace("edit_recipe_", "")

    # Надежно сохраняем editing_recipe_id
    context.user_data['editing_recipe_id'] = recipe_id

    recipes = load_recipes()
    recipe = recipes.get(recipe_id)

    if not recipe:
        await query.edit_message_text(
            "❌ Рецепт не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="edit_recipes")]
            ])
        )
        return ConversationHandler.END

    text = f"✏️ *Редактирование рецепта: {recipe['name']}*\n\n"
    text += "📋 *Текущие ингредиенты:*\n"
    for ing in recipe['ingredients']:
        text += f"• {ing['name']} - {ing['quantity']}\n"

    keyboard = [
        [InlineKeyboardButton("📝 Изменить название", callback_data="edit_recipe_name")],
        [InlineKeyboardButton("📋 Изменить ингредиенты", callback_data="edit_recipe_ingredients")],
        [InlineKeyboardButton("🗑 Удалить рецепт", callback_data="delete_recipe")],
        [InlineKeyboardButton("🔙 Назад", callback_data="edit_recipes")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_RECIPE_NAME

async def handle_recipe_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка редактирования рецепта"""
    query = update.callback_query
    await query.answer()

    data = query.data

    # Убедимся, что editing_recipe_id сохранен
    recipe_id = context.user_data.get('editing_recipe_id')
    if not recipe_id and data not in ["edit_recipes", "back_to_recipes"]:
        await query.edit_message_text(
            "❌ Не удалось определить рецепт для редактирования.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
        return ConversationHandler.END

    if data == "edit_recipe_name":
        # Сохраняем ID сообщения с инструкцией для последующего редактирования
        context.user_data['edit_instruction_message_id'] = query.message.message_id

        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_edit_recipe_menu_{recipe_id}")]
        ]

        await query.edit_message_text(
            "✏️ Введите новое название рецепта:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_RECIPE_NAME

    elif data == "edit_recipe_ingredients":
        # Сохраняем ID сообщения с инструкцией для последующего редактирования
        context.user_data['edit_instruction_message_id'] = query.message.message_id

        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_edit_recipe_menu_{recipe_id}")]
        ]

        await query.edit_message_text(
            "📋 Введите новые ингредиенты (через запятую, название и количество через пробел):\n"
            "Пример: помидоры 500г, огурцы 300г, соль по вкусу",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_RECIPE_INGREDIENTS

    elif data == "delete_recipe":
        recipe_id = context.user_data['editing_recipe_id']
        recipes = load_recipes()

        # Удаляем рецепт
        if recipe_id in recipes:
            # УДАЛЯЕМ ВСЕ СВЯЗАННЫЕ ПЛАНЫ ПИТАНИЯ И ИХ НАПОМИНАНИЯ
            meal_plans = load_meal_plans()
            meal_plans_to_delete = []
            total_reminders_deleted = 0

            for plan_id, plan in meal_plans.items():
                if plan.get('recipe_id') == recipe_id:
                    meal_plans_to_delete.append(plan_id)
                    # Удаляем напоминания для этого плана
                    total_reminders_deleted += delete_meal_plan_reminders(plan_id)
                    # Удаляем сообщения из чатов
                    reminders = load_reminders()
                    for reminder_id, reminder in reminders.items():
                        if reminder.get('meal_plan_id') == plan_id:
                            await delete_old_reminder_messages(context.application, reminder_id)

            # Удаляем планы питания
            for plan_id in meal_plans_to_delete:
                del meal_plans[plan_id]

            # Удаляем рецепт
            del recipes[recipe_id]

            # Сохраняем изменения
            save_recipes(recipes)
            save_meal_plans(meal_plans)

            await query.edit_message_text(
                f"✅ Рецепт и связанные планы питания удалены.\n"
                f"🗑 Удалено планов: {len(meal_plans_to_delete)}, напоминаний: {total_reminders_deleted}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                ])
            )
        else:
            await query.edit_message_text(
                "❌ Ошибка при удалении рецепта.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                ])
            )

        context.user_data.clear()
        return ConversationHandler.END

    elif data == "edit_recipes":
        await edit_recipes_menu(update, context)
        return ConversationHandler.END



async def back_to_edit_recipe_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возврат в меню редактирования рецепта"""
    query = update.callback_query
    await query.answer()

    # Извлекаем recipe_id из callback_data если он там есть
    data = query.data
    if data.startswith("back_to_edit_recipe_menu_"):
        recipe_id = data.replace("back_to_edit_recipe_menu_", "")
        context.user_data['editing_recipe_id'] = recipe_id
    else:
        recipe_id = context.user_data.get('editing_recipe_id')

    if not recipe_id:
        await query.edit_message_text(
            "❌ Не удалось определить рецепт для редактирования.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
        return ConversationHandler.END

    # Показываем меню редактирования рецепта
    recipes = load_recipes()
    recipe = recipes.get(recipe_id)

    if not recipe:
        await query.edit_message_text(
            "❌ Рецепт не найден. Возможно, он был удален.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
        return ConversationHandler.END

    text = f"✏️ *Редактирование рецепта: {recipe['name']}*\n\n"
    text += "📋 *Текущие ингредиенты:*\n"
    for ing in recipe['ingredients']:
        text += f"• {ing['name']} - {ing['quantity']}\n"

    keyboard = [
        [InlineKeyboardButton("📝 Изменить название", callback_data="edit_recipe_name")],
        [InlineKeyboardButton("📋 Изменить ингредиенты", callback_data="edit_recipe_ingredients")],
        [InlineKeyboardButton("🗑 Удалить рецепт", callback_data="delete_recipe")],
        [InlineKeyboardButton("🔙 Назад", callback_data="edit_recipes")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

def delete_meal_plan_reminders(plan_id):
    """Удаляет все напоминания, связанные с планом питания"""
    reminders = load_reminders()
    reminders_to_delete = []

    for reminder_id, reminder in reminders.items():
        if reminder.get('meal_plan_id') == plan_id:
            reminders_to_delete.append(reminder_id)

    # Удаляем найденные напоминания
    for reminder_id in reminders_to_delete:
        del reminders[reminder_id]

    if reminders_to_delete:
        save_reminders(reminders)

    return len(reminders_to_delete)


async def handle_edit_recipe_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка нового названия рецепта при редактировании с удалением сообщений"""
    try:
        # Удаляем сообщение пользователя с новым названием
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        new_name = update.message.text.strip()
        if not new_name:
            # Редактируем сообщение с инструкцией вместо отправки нового
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Название рецепта не может быть пустым. Введите название:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_edit_recipe_menu_{context.user_data.get('editing_recipe_id', '')}")]
                    ])
                )
            else:
                await update.message.reply_text("❌ Название рецепта не может быть пустым. Введите название:")
            return EDIT_RECIPE_NAME

        # Сохраняем изменения
        recipe_id = context.user_data.get('editing_recipe_id')
        if not recipe_id:
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Не удалось определить рецепт для редактирования.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Не удалось определить рецепт для редактирования.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            return ConversationHandler.END

        recipes = load_recipes()
        recipe = recipes.get(recipe_id)

        if not recipe:
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Рецепт не найден. Возможно, он был удален.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Рецепт не найден. Возможно, он был удален.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            return ConversationHandler.END

        old_name = recipe['name']
        recipe['name'] = new_name
        recipe['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()

        if save_recipes(recipes):
            # Редактируем сообщение с инструкцией, превращая его в меню редактирования
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                # Показываем обновленное меню редактирования
                text = f"✏️ *Редактирование рецепта: {new_name}*\n\n"
                text += "📋 *Текущие ингредиенты:*\n"
                for ing in recipe['ingredients']:
                    text += f"• {ing['name']} - {ing['quantity']}\n\n"
                text += f"✅ Название изменено с '{old_name}' на '{new_name}'"

                keyboard = [
                    [InlineKeyboardButton("📝 Изменить название", callback_data="edit_recipe_name")],
                    [InlineKeyboardButton("📋 Изменить ингредиенты", callback_data="edit_recipe_ingredients")],
                    [InlineKeyboardButton("🗑 Удалить рецепт", callback_data="delete_recipe")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="edit_recipes")]
                ]

                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    f"✅ Название рецепта изменено на: {new_name}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
        else:
            await update.message.reply_text(
                "❌ Ошибка при сохранении изменений.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                ])
            )

        # Очищаем временные данные
        context.user_data.pop('edit_instruction_message_id', None)
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка при изменении названия рецепта: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при изменении названия.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
        return ConversationHandler.END

async def handle_edit_recipe_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка новых ингредиентов рецепта при редактировании с удалением сообщений"""
    try:
        # Удаляем сообщение пользователя с новыми ингредиентами
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя: {e}")

        ingredients_str = update.message.text.strip()
        ingredients = []
        for ing in ingredients_str.split(','):
            ing = ing.strip()
            if not ing:
                continue
            if ' ' in ing:
                name, quantity = ing.rsplit(' ', 1)
                ingredients.append({
                    'id': len(ingredients),
                    'name': name.strip(),
                    'quantity': quantity.strip()
                })
            else:
                ingredients.append({
                    'id': len(ingredients),
                    'name': ing,
                    'quantity': 'не указано'
                })

        if not ingredients:
            # Редактируем сообщение с инструкцией вместо отправки нового
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Список ингредиентов пуст. Введите ингредиенты в формате: помидоры 500г, огурцы 300г",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_edit_recipe_menu_{context.user_data.get('editing_recipe_id', '')}")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Список ингредиентов пуст. Введите ингредиенты в формате: помидоры 500г, огурцы 300г"
                )
            return EDIT_RECIPE_INGREDIENTS

        # Сохраняем изменения
        recipe_id = context.user_data.get('editing_recipe_id')
        if not recipe_id:
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Не удалось определить рецепт для редактирования.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Не удалось определить рецепт для редактирования.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            return ConversationHandler.END

        recipes = load_recipes()
        recipe = recipes.get(recipe_id)

        if not recipe:
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text="❌ Рецепт не найден. Возможно, он был удален.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Рецепт не найден. Возможно, он был удален.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Обновить", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
            return ConversationHandler.END

        old_ingredients_count = len(recipe['ingredients'])
        recipe['ingredients'] = ingredients
        recipe['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()

        if save_recipes(recipes):
            # Редактируем сообщение с инструкцией, превращая его в меню редактирования
            instruction_message_id = context.user_data.get('edit_instruction_message_id')
            if instruction_message_id:
                # Показываем обновленное меню редактирования
                text = f"✏️ *Редактирование рецепта: {recipe['name']}*\n\n"
                text += "📋 *Обновленные ингредиенты:*\n"
                for ing in ingredients:
                    text += f"• {ing['name']} - {ing['quantity']}\n\n"
                text += f"✅ Ингредиенты обновлены! Было: {old_ingredients_count}, стало: {len(ingredients)}"

                keyboard = [
                    [InlineKeyboardButton("📝 Изменить название", callback_data="edit_recipe_name")],
                    [InlineKeyboardButton("📋 Изменить ингредиенты", callback_data="edit_recipe_ingredients")],
                    [InlineKeyboardButton("🗑 Удалить рецепт", callback_data="delete_recipe")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="edit_recipes")]
                ]

                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=instruction_message_id,
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    "✅ Ингредиенты рецепта успешно обновлены!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_recipe_{recipe_id}")],
                        [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                    ])
                )
        else:
            await update.message.reply_text(
                "❌ Ошибка при сохранении изменений.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
                ])
            )

        # Очищаем временные данные
        context.user_data.pop('edit_instruction_message_id', None)
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка при изменении ингредиентов рецепта: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при изменении ингредиентов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍽 К рецептам", callback_data="back_to_recipes")]
            ])
        )
        return ConversationHandler.END

async def manage_meal_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Меню управления планами питания"""
    query = update.callback_query
    await query.answer()

    meal_plans = load_meal_plans()

    if not meal_plans:
        await query.edit_message_text(
            "❌ Нет планов питания для управления.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Запланировать блюдо", callback_data="plan_meal")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return

    # Группируем планы по дням недели
    plans_by_day = {}
    for plan_id, plan in meal_plans.items():
        day = plan.get('day', 'Неизвестно')
        if day not in plans_by_day:
            plans_by_day[day] = []
        plans_by_day[day].append(plan)

    keyboard = []
    for day, plans in plans_by_day.items():
        keyboard.append([
            InlineKeyboardButton(
                f"📅 {day} ({len(plans)} блюд)",
                callback_data=f"manage_day_{day}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_recipes")])

    await query.edit_message_text(
        "📅 *Управление планами питания*\n\nВыберите день для управления:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def manage_day_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Управление планами конкретного дня"""
    query = update.callback_query
    await query.answer()

    day = query.data.replace("manage_day_", "")
    meal_plans = load_meal_plans()

    day_plans = {pid: plan for pid, plan in meal_plans.items() if plan.get('day') == day}

    if not day_plans:
        await query.edit_message_text(
            f"❌ Нет планов питания для {day}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")]
            ])
        )
        return

    keyboard = []
    for plan_id, plan in day_plans.items():
        keyboard.append([
            InlineKeyboardButton(
                f"🍽 {plan['recipe_name']}",
                callback_data=f"edit_plan_{plan_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")])

    await query.edit_message_text(
        f"📅 *Планы питания: {day}*\n\nВыберите блюдо для редактирования:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def edit_meal_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Редактирование плана питания"""
    query = update.callback_query
    await query.answer()

    plan_id = query.data.replace("edit_plan_", "")
    meal_plans = load_meal_plans()
    plan = meal_plans.get(plan_id)

    if not plan:
        await query.edit_message_text(
            "❌ План питания не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")]
            ])
        )
        return

    context.user_data['editing_plan_id'] = plan_id

    assigned_count = sum(1 for ing in plan['ingredients'] if ing.get('assigned_to'))

    text = f"✏️ *Редактирование плана питания*\n\n"
    text += f"🍽 *{plan['recipe_name']}*\n"
    text += f"📅 День: {plan['day']}\n"
    text += f"📅 Дата: {plan['date_str']}\n"
    text += f"👥 Распределено: {assigned_count}/{len(plan['ingredients'])} ингредиентов\n\n"

    keyboard = [
        [InlineKeyboardButton("👥 Изменить исполнителей", callback_data=f"change_assignees_{plan_id}")],
        [InlineKeyboardButton("📅 Изменить день", callback_data=f"change_plan_day_{plan_id}")],
        [InlineKeyboardButton("🗑 Удалить план", callback_data=f"delete_plan_{plan_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def start_edit_plan_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало редактирования исполнителей плана питания"""
    query = update.callback_query
    await query.answer()

    plan_id = query.data.replace("change_assignees_", "")
    meal_plans = load_meal_plans()
    plan = meal_plans.get(plan_id)

    if not plan:
        await query.edit_message_text(
            "❌ План питания не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")]
            ])
        )
        return ConversationHandler.END

    context.user_data['editing_plan_id'] = plan_id
    context.user_data['meal_plan'] = plan

    # Показываем распределение ингредиентов для редактирования
    await show_edit_ingredient_assignment(query, context)
    return EDIT_PLAN_ASSIGNMENT

async def show_edit_ingredient_assignment(query, context):
    """Показать распределение ингредиентов для редактирования"""
    meal_plan = context.user_data['meal_plan']
    users = load_users()

    text = f"🍽 *Редактирование распределения ингредиентов*\n\n"
    text += f"Блюдо: *{meal_plan['recipe_name']}*\n"
    text += f"Дата: *{meal_plan['date_str']}*\n\n"
    text += "*Ингредиенты:*\n"

    keyboard = []

    for i, ingredient in enumerate(meal_plan['ingredients']):
        assigned_to = ingredient.get('assigned_to')
        assigned_name = "Не назначено"
        if assigned_to:
            user_data = users.get(assigned_to, {})
            assigned_name = user_data.get('username', 'Unknown')

        text += f"• {ingredient['name']} - {ingredient['quantity']} → {assigned_name}\n"

        keyboard.append([
            InlineKeyboardButton(
                f"📝 Назначить: {ingredient['name']}",
                callback_data=f"edit_assign_ing_{i}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("✅ Завершить редактирование", callback_data="finish_edit_assignment")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Назад к плану", callback_data="back_to_edit_plan")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")
    ])

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_edit_plan_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка редактирования исполнителей плана питания"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "back_to_main":
        await query.edit_message_text(
            "🔙 Вернулись на главную",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    if data == "back_to_edit_plan":
        await back_to_edit_plan_handler(update, context)
        return ConversationHandler.END

    if data == "finish_edit_assignment":
        await finish_edit_assignment(query, context)
        return ConversationHandler.END

    if data.startswith("edit_assign_ing_"):
        ing_index = int(data.replace("edit_assign_ing_", ""))
        context.user_data['current_ing_index'] = ing_index
        await show_user_selection_for_ingredient(query, context)
        return EDIT_PLAN_ASSIGNMENT

async def finish_edit_assignment(query, context):
    """Завершение редактирования распределения ингредиентов с пересозданием напоминаний"""
    try:
        meal_plan = context.user_data['meal_plan']
        plan_id = context.user_data['editing_plan_id']

        # Обновляем план питания
        meal_plans = load_meal_plans()
        if plan_id in meal_plans:
            # Полностью заменяем ингредиенты на обновленные
            meal_plans[plan_id]['ingredients'] = meal_plan['ingredients']
            meal_plans[plan_id]['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()

            if save_meal_plans(meal_plans):
                logger.info(f"План питания {plan_id} успешно обновлен")

                # ПЕРЕСОЗДАЕМ НАПОМИНАНИЯ ДЛЯ ОБНОВЛЕННОГО РАСПРЕДЕЛЕНИЯ
                if meal_plan.get('with_notifications'):
                    reminders_created = await create_ingredient_reminders(meal_plan, context)
                else:
                    # Если уведомления отключены, удаляем старые напоминания
                    reminders = load_reminders()
                    reminders_to_delete = []
                    for reminder_id, reminder in reminders.items():
                        if reminder.get('meal_plan_id') == plan_id and reminder.get('type') == 'ingredient':
                            reminders_to_delete.append(reminder_id)

                    for reminder_id in reminders_to_delete:
                        del reminders[reminder_id]

                    if reminders_to_delete:
                        save_reminders(reminders)
                    reminders_created = 0

                # Показываем подтверждение
                assigned_count = sum(1 for ing in meal_plan['ingredients'] if ing.get('assigned_to'))
                total_ingredients = len(meal_plan['ingredients'])

                text = f"✅ *Распределение ингредиентов обновлено!*\n\n"
                text += f"🍽 *{meal_plan['recipe_name']}*\n"
                text += f"📅 Дата: {meal_plan['date_str']}\n"
                text += f"👥 Распределено: {assigned_count}/{total_ingredients} ингредиентов\n"

                if meal_plan.get('with_notifications'):
                    text += f"🔔 Обновлено напоминаний: {reminders_created}\n"

                if assigned_count < total_ingredients:
                    text += "\n⚠️ *Внимание:* Не все ингредиенты распределены!\n"

                await query.edit_message_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_plan_{plan_id}")],
                        [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
                    ])
                )
            else:
                logger.error(f"Ошибка сохранения плана {plan_id}")
                await query.edit_message_text(
                    "❌ Ошибка при сохранении изменений.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
                    ])
                )
        else:
            await query.edit_message_text(
                "❌ План питания не найден.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
                ])
            )

    except Exception as e:
        logger.error(f"Ошибка в finish_edit_assignment: {e}")
        await query.edit_message_text(
            "❌ Произошла ошибка при сохранении изменений.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
            ])
        )

    context.user_data.clear()

async def update_ingredient_reminders_for_plan(plan_id, new_date, new_date_str):
    """Обновляет напоминания для ингредиентов при изменении даты плана питания"""
    try:
        reminders = load_reminders()
        meal_plans = load_meal_plans()
        plan = meal_plans.get(plan_id)

        if not plan:
            logger.error(f"План {plan_id} не найден при обновлении напоминаний")
            return 0

        updated_count = 0

        # Находим все напоминания для этого плана
        for reminder_id, reminder in reminders.items():
            if reminder.get('meal_plan_id') == plan_id and reminder.get('type') == 'ingredient':
                # Обновляем дату приготовления в тексте напоминания
                old_text = reminder['text']
                lines = old_text.split('\n')
                new_lines = []

                for line in lines:
                    if line.startswith('📅 Дата приготовления:'):
                        new_lines.append(f"📅 Дата приготовления: {new_date_str}")
                    else:
                        new_lines.append(line)

                reminder['text'] = '\n'.join(new_lines)
                reminder['meal_date'] = new_date_str

                # Пересчитываем дату напоминания на основе новой даты приготовления
                notification_time = plan.get('notification_time', '1_day')
                days_before = {
                    '1_day': 1,
                    '2_days': 2,
                    '3_days': 3,
                    '1_week': 7
                }.get(notification_time, 1)

                reminder_date = new_date - timedelta(days=days_before)
                reminder_datetime = reminder_date.replace(hour=10, minute=0, second=0)
                reminder['datetime'] = reminder_datetime.isoformat()

                # Если напоминание в срочном режиме, сбрасываем его
                if reminder.get('urgent_reminders'):
                    reminder['urgent_reminders'] = False
                    reminder['urgent_until'] = None
                    reminder['last_sent'] = None

                updated_count += 1
                logger.info(f"Обновлено напоминание ингредиента {reminder_id} для плана {plan_id}")

        if updated_count > 0:
            if not save_reminders(reminders):
                logger.error("Ошибка при сохранении обновленных напоминаний")
                return 0

        return updated_count

    except Exception as e:
        logger.error(f"Ошибка при обновлении напоминаний для плана {plan_id}: {e}")
        return 0

async def update_meal_plan_day(application, plan_id, new_day_key):
    """Обновляет день недели для плана питания и всех связанных напоминаний"""
    try:
        meal_plans = load_meal_plans()
        plan = meal_plans.get(plan_id)

        if not plan:
            logger.error(f"❌ План питания {plan_id} не найден")
            return False

        # Получаем текущую дату плана
        current_date = plan['date']
        if isinstance(current_date, str):
            current_date = datetime.fromisoformat(current_date)

        # Вычисляем новую дату на основе выбранного дня недели
        today = datetime.now(MOSCOW_TZ)
        current_weekday = today.weekday()
        target_weekday = list(WEEK_DAYS.keys()).index(new_day_key)

        days_ahead = (target_weekday - current_weekday + 7) % 7
        if days_ahead == 0:
            days_ahead = 7

        new_date = today + timedelta(days=days_ahead)
        new_date_str = new_date.strftime('%d.%m.%Y')

        # Обновляем план
        plan['day'] = WEEK_DAYS[new_day_key]
        plan['date'] = new_date.isoformat()
        plan['date_str'] = new_date_str
        plan['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()

        # Сохраняем изменения
        if not save_meal_plans(meal_plans):
            logger.error("❌ Ошибка при сохранении обновленного плана")
            return False

        # Обновляем напоминания для ингредиентов
        if plan.get('with_notifications'):
            await create_ingredient_reminders(plan, application)

        logger.info(f"✅ День плана обновлен: {plan['recipe_name']} на {new_date_str}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении дня плана: {e}")
        return False

async def update_meal_plan_reminders(plan_id, meal_plan, context):
    """Обновляет напоминания для плана питания после изменения распределения"""
    try:
        reminders = load_reminders()
        updated_count = 0

        # Удаляем старые напоминания для этого плана
        reminders_to_delete = []
        for reminder_id, reminder in reminders.items():
            if reminder.get('meal_plan_id') == plan_id:
                reminders_to_delete.append(reminder_id)

        for reminder_id in reminders_to_delete:
            del reminders[reminder_id]

        # Создаем новые напоминания на основе обновленного распределения
        if meal_plan.get('with_notifications', False):
            reminders_created = await create_ingredient_reminders(meal_plan, context)
            updated_count = reminders_created
        else:
            # Если уведомления отключены, просто сохраняем пустой список
            save_reminders(reminders)
            updated_count = 0

        logger.info(f"Обновлены напоминания для плана {plan_id}: удалено {len(reminders_to_delete)}, создано {updated_count}")
        return updated_count

    except Exception as e:
        logger.error(f"Ошибка при обновлении напоминаний плана {plan_id}: {e}")
        return 0

async def handle_change_plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка изменения дня плана питания"""
    query = update.callback_query
    await query.answer()

    plan_id = query.data.replace("change_plan_day_", "")
    meal_plans = load_meal_plans()
    plan = meal_plans.get(plan_id)

    if not plan:
        await query.edit_message_text(
            "❌ План питания не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="manage_plans")]
            ])
        )
        return

    keyboard = []
    for day_key, day_name in WEEK_DAYS.items():
        keyboard.append([InlineKeyboardButton(day_name, callback_data=f"update_day_{day_key}_{plan_id}")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"edit_plan_{plan_id}")])

    await query.edit_message_text(
        "📅 Выберите новый день недели для этого плана:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_delete_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка удаления плана питания с удалением всех связанных напоминаний и сообщений"""
    query = update.callback_query
    await query.answer()

    plan_id = query.data.replace("delete_plan_", "")
    meal_plans = load_meal_plans()

    if plan_id not in meal_plans:
        await query.edit_message_text(
            "❌ План питания не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
            ])
        )
        return

    # Сохраняем информацию о плане для сообщения
    plan_name = meal_plans[plan_id]['recipe_name']

    # УДАЛЯЕМ ВСЕ СВЯЗАННЫЕ НАПОМИНАНИЯ И СООБЩЕНИЯ
    reminders_deleted = delete_meal_plan_reminders(plan_id)

    # Дополнительно удаляем сообщения из чатов
    reminders = load_reminders()
    for reminder_id, reminder in reminders.items():
        if reminder.get('meal_plan_id') == plan_id:
            await delete_old_reminder_messages(context.application, reminder_id)

    # Удаляем план питания
    del meal_plans[plan_id]

    # Сохраняем изменения
    plans_saved = save_meal_plans(meal_plans)

    if plans_saved:
        text = f"✅ *План питания удален!*\n\n"
        text += f"🍽 *{plan_name}*\n\n"
        if reminders_deleted > 0:
            text += f"🗑 Также удалено {reminders_deleted} связанных напоминаний об ингредиентах."
        else:
            text += "ℹ️ Связанных напоминаний не найдено."

        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")],
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        logger.info(f"План питания {plan_id} удален, удалено напоминаний: {reminders_deleted}")
    else:
        await query.edit_message_text(
            "❌ Ошибка при удалении плана питания.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
            ])
        )

async def back_to_edit_plan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик возврата к редактированию плана"""
    query = update.callback_query
    await query.answer()

    plan_id = context.user_data.get('editing_plan_id')
    if plan_id:
        await edit_meal_plan(update, context)
    else:
        await query.edit_message_text(
            "❌ Ошибка: ID плана не найден.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )

async def handle_update_plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обновление дня плана питания с удалением старых напоминаний"""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split('_')
    day_key = parts[2]
    plan_id = parts[3]

    if day_key not in WEEK_DAYS:
        await query.edit_message_text(
            "❌ Ошибка выбора дня.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_plan_{plan_id}")]
            ])
        )
        return

    day_name = WEEK_DAYS[day_key]

    # Обновляем дату
    today = datetime.now(MOSCOW_TZ)
    current_weekday = today.weekday()
    target_weekday = list(WEEK_DAYS.keys()).index(day_key)

    days_ahead = (target_weekday - current_weekday + 7) % 7
    if days_ahead == 0:
        days_ahead = 7

    new_date = today + timedelta(days=days_ahead)
    new_date_str = new_date.strftime('%d.%m.%Y')

    # Обновляем план
    meal_plans = load_meal_plans()
    if plan_id in meal_plans:
        # УДАЛЯЕМ ВСЕ СТАРЫЕ НАПОМИНАНИЯ ДЛЯ ЭТОГО ПЛАНА
        deleted_reminders_count = delete_meal_plan_reminders(plan_id)

        # Также удаляем сообщения из чатов
        reminders = load_reminders()
        for reminder_id, reminder in reminders.items():
            if reminder.get('meal_plan_id') == plan_id:
                await delete_old_reminder_messages(context.application, reminder_id)

        meal_plans[plan_id]['day'] = day_name
        meal_plans[plan_id]['date'] = new_date.isoformat()
        meal_plans[plan_id]['date_str'] = new_date_str
        meal_plans[plan_id]['updated_at'] = datetime.now(MOSCOW_TZ).isoformat()

        if save_meal_plans(meal_plans):
            # СОЗДАЕМ НОВЫЕ НАПОМИНАНИЯ, если у плана включены уведомления
            plan = meal_plans[plan_id]
            reminders_created = 0
            if plan.get('with_notifications'):
                reminders_created = await create_ingredient_reminders(plan, context.application)

            text = f"✅ День плана изменен на {day_name} ({new_date_str})"
            if deleted_reminders_count > 0:
                text += f"\n🗑 Удалено старых напоминаний: {deleted_reminders_count}"
            if reminders_created > 0:
                text += f"\n🔔 Создано новых напоминаний: {reminders_created}"

            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_plan_{plan_id}")],
                    [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
                ])
            )
            logger.info(f"План {plan_id} перемещен на {day_name}, удалено напоминаний: {deleted_reminders_count}, создано: {reminders_created}")
        else:
            await query.edit_message_text(
                "❌ Ошибка при сохранении изменений.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К планам", callback_data="manage_plans")]
                ])
            )

async def check_and_send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Объединенная проверка всех типов напоминаний"""
    try:
        application = context.application
        total_sent = 0

        # Вызываем функцию для проверки обычных напоминаний
        regular_sent = await check_regular_reminders(application)
        total_sent += regular_sent

        # Вызываем функцию для проверки напоминаний ингредиентов
        ingredient_sent = await check_ingredient_reminders(application)
        total_sent += ingredient_sent

        if total_sent > 0:
            logger.info(f"✅ Всего отправлено напоминаний: {total_sent} (обычные: {regular_sent}, ингредиенты: {ingredient_sent})")

        return total_sent

    except Exception as e:
        logger.error(f"❌ Ошибка в check_and_send_reminders: {e}")
        return 0

async def check_regular_reminders(application):

    """Проверяет и отправляет обычные напоминания с удалением через 24 часа после последней отправки"""
    try:
        reminders = load_reminders()
        users = load_users()
        current_time = datetime.now(MOSCOW_TZ)

        # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ
        current_hour = current_time.hour
        is_night_time = current_hour >= 23 or current_hour < 9

        logger.info(f"🔍 Проверка обычных напоминаний в {current_time.strftime('%d.%m.%Y %H:%M:%S')} МСК")

        sent_count = 0
        reminders_to_update = []
        reminders_to_remove = []

        # ПРОВЕРКА АКТУАЛЬНОСТИ СУЩЕСТВУЮЩИХ СООБЩЕНИЙ
        await cleanup_old_messages(application, reminders)

        for reminder_id, reminder in reminders.items():
            try:
                # Пропускаем напоминания ингредиентов
                if reminder.get('type') == 'ingredient':
                    continue

                reminder_time = datetime.fromisoformat(reminder['datetime']).replace(tzinfo=MOSCOW_TZ)
                time_diff_minutes = (reminder_time - current_time).total_seconds() / 60

                # ПРОВЕРКА: УДАЛЕНИЕ ОДНОКРАТНЫХ НАПОМИНАНИЙ ЧЕРЕЗ 24 ЧАСА ПОСЛЕ ПОСЛЕДНЕЙ ОТПРАВКИ
                last_sent = reminder.get('last_sent')
                if last_sent and reminder.get('interval_days', 0) == 0:
                    last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                    hours_since_last_sent = (current_time - last_sent_time).total_seconds() / 3600

                    # Если прошло более 24 часов с последней отправки и это однократное напоминание
                    if hours_since_last_sent >= 24:
                        reminders_to_remove.append(reminder_id)
                        await delete_old_reminder_messages(application, reminder_id)
                        logger.info(f"🗑 Однократное напоминание {reminder_id} удалено через 24 часа после последней отправки")
                        continue

                # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ ДЛЯ СРОЧНЫХ НАПОМИНАНИЙ
                if is_night_time and reminder.get('urgent_reminders'):
                    logger.info(f"🌙 Пропущена проверка срочного напоминания в ночное время: {reminder_id}")
                    continue

                # Проверяем истек ли срочный режим
                urgent_until = reminder.get('urgent_until')
                if urgent_until:
                    urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                    if current_time > urgent_until_time:
                        # СРОЧНЫЙ РЕЖИМ ИСТЕК
                        interval_days = reminder.get('interval_days', 0)

                        if interval_days == 0:
                            # ОДНОКРАТНОЕ НАПОМИНАНИЕ - полное удаление
                            reminders_to_remove.append(reminder_id)
                            await delete_old_reminder_messages(application, reminder_id)
                            logger.info(f"🗑 Однократное срочное напоминание {reminder_id} удалено по истечении срочного режима")
                            continue
                        else:
                            # ИНТЕРВАЛЬНОЕ НАПОМИНАНИЕ - восстанавливаем обычный режим
                            original_interval = reminder.get('original_interval', interval_days)
                            original_datetime_str = reminder.get('original_datetime')

                            if original_datetime_str:
                                original_datetime = datetime.fromisoformat(original_datetime_str).replace(tzinfo=MOSCOW_TZ)
                                days_passed = (current_time.date() - original_datetime.date()).days
                                intervals_passed = days_passed // original_interval
                                next_interval_date = original_datetime + timedelta(days=(intervals_passed + 1) * original_interval)

                                if next_interval_date <= current_time:
                                    next_interval_date += timedelta(days=original_interval)

                                reminder['datetime'] = next_interval_date.isoformat()
                                logger.info(f"🔄 Интервальное напоминание восстановлено: {next_interval_date.strftime('%d.%m.%Y %H:%M')}")

                            # Снимаем срочный режим и удаляем старые сообщения
                            reminder['urgent_reminders'] = False
                            reminder['urgent_until'] = None
                            reminder['last_sent'] = None
                            reminders_to_update.append(reminder_id)

                            # УДАЛЯЕМ ВСЕ СТАРЫЕ СООБЩЕНИЯ ДЛЯ ЭТОГО НАПОМИНАНИЯ
                            await delete_old_reminder_messages(application, reminder_id)
                            logger.info(f"🔄 Срочный режим истек для {reminder_id}, восстановлен обычный режим")
                            continue

                should_send = False
                send_reason = ""

                # Для срочных напоминаний
                if reminder.get('urgent_reminders'):
                    if not last_sent:
                        should_send = True
                        send_reason = "первое срочное напоминание"
                    else:
                        last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                        hours_since_last = (current_time - last_sent_time).total_seconds() / 3600

                        if hours_since_last >= 3:
                            should_send = True
                            send_reason = f"срочное напоминание (прошло {hours_since_last:.1f} ч.)"

                # Для обычных напоминаний (только если не срочные)
                elif not reminder.get('urgent_reminders'):
                    # Если напоминание уже отправлялось сегодня, пропускаем
                    if last_sent:
                        last_sent_time = datetime.fromisoformat(last_sent).replace(tzinfo=MOSCOW_TZ)
                        if last_sent_time.date() == current_time.date():
                            continue

                    # ОТПРАВЛЯЕМ ДАЖЕ ЕСЛИ ПРОСРОЧЕНО (время напоминания уже прошло)
                    if time_diff_minutes <= 30:  # Отправляем если время напоминания прошло не более чем 30 минут назад
                        should_send = True
                        send_reason = "обычное напоминание"
                    elif time_diff_minutes < 0:  # Если просрочено более чем на 30 минут, все равно отправляем один раз
                        should_send = True
                        send_reason = "просроченное напоминание"

                if should_send:
                    logger.info(f"⏰ ОТПРАВКА ({send_reason}): {reminder['text'][:30]}... (тип: {reminder.get('type', 'personal')})")

                    await send_reminder_notification(application, reminder, users, is_urgent_update=reminder.get('urgent_reminders', False))
                    sent_count += 1

                    reminder['last_sent'] = current_time.isoformat()

                    # Планируем следующее напоминание
                    if reminder.get('urgent_reminders'):
                        # Срочное - через 3 часа
                        next_time = current_time + timedelta(hours=3)
                        if next_time.hour >= 23 or next_time.hour < 9:
                            next_time = next_time.replace(hour=9, minute=0, second=0)
                            if next_time <= current_time:
                                next_time += timedelta(days=1)
                        reminder['datetime'] = next_time.isoformat()
                        logger.info(f"🔁 Следующее срочное напоминание через 3 часа: {next_time.strftime('%d.%m.%Y %H:%M')}")
                    else:
                        # Обычное - по интервалу
                        interval_days = reminder.get('interval_days', 0)
                        if interval_days > 0:
                            next_time = reminder_time + timedelta(days=interval_days)
                            reminder['datetime'] = next_time.isoformat()
                            logger.info(f"🔄 Следующее интервальное напоминание через {interval_days} дней: {next_time.strftime('%d.%m.%Y %H:%M')}")
                        else:
                            # ОДНОКРАТНОЕ НАПОМИНАНИЕ - не удаляем сразу, удалим через 24 часа после отправки
                            logger.info(f"⏰ Однократное напоминание {reminder_id} отправлено, будет удалено через 24 часа")

                    reminders_to_update.append(reminder_id)

            except Exception as e:
                logger.error(f"❌ Ошибка обработки напоминания {reminder_id}: {e}")
                continue

        # УДАЛЯЕМ НАПОМИНАНИЯ, ПОМЕЧЕННЫЕ ДЛЯ УДАЛЕНИЯ
        for reminder_id in reminders_to_remove:
            if reminder_id in reminders:
                del reminders[reminder_id]
                logger.info(f"✅ Удалено напоминание {reminder_id}")

        # Сохраняем изменения
        if reminders_to_update or reminders_to_remove or sent_count > 0:
            if not save_reminders(reminders):
                logger.error("❌ Ошибка при записи напоминаний")
            else:
                logger.info(f"📤 ИТОГ: Отправлено {sent_count}, обновлено {len(reminders_to_update)}, удалено {len(reminders_to_remove)}")

        return sent_count

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в check_regular_reminders: {e}")
        return 0

async def send_reminder_notification(application, reminder, users, is_urgent_update=False, is_missed=False):
    """Отправляет уведомление-напоминание с управлением старыми сообщениями и проверкой ночного времени"""
    try:
        current_time = datetime.now(MOSCOW_TZ)

        # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ ДЛЯ ВСЕХ ТИПОВ НАПОМИНАНИЙ
        current_hour = current_time.hour
        is_night_time = current_hour >= 23 or current_hour < 9

        # Если ночное время и это не пропущенное напоминание, пропускаем отправку
        if is_night_time and not is_missed:
            logger.info(f"🌙 Пропущена отправка в ночное время (сейчас {current_time.strftime('%H:%M')})")
            return
        # Если это обновление срочного напоминания, удаляем старые сообщения
        if is_urgent_update:
            await delete_old_reminder_messages(application, reminder['id'])

        # Определяем, кто должен купить
        assigned_users = []
        for user_id in reminder['users']:
            user_data = users.get(str(user_id), {})
            username = user_data.get('username', 'Unknown')
            assigned_users.append(username)

        # Формируем текст напоминания
        current_time = datetime.now(MOSCOW_TZ)

        # Базовый текст в зависимости от типа
        if is_missed:
            message_text = f"⏰ *ПРОПУЩЕННОЕ НАПОМИНАНИЕ!*\n\n"
        else:
            message_text = f"🔔 *НАПОМИНАНИЕ!*\n\n"

        # Основной текст напоминания
        message_text += f"{reminder['text']}\n\n"

        # Информация о пользователях
        if assigned_users:
            message_text += f"👤 *Для:* {', '.join(assigned_users)}\n"

        # Информация о времени
        reminder_time = datetime.fromisoformat(reminder['datetime']).replace(tzinfo=MOSCOW_TZ)
        if is_missed:
            message_text += f"⏰ *Должно было прийти:* {reminder_time.strftime('%d.%m.%Y %H:%M')}\n"
        else:
            message_text += f"⏰ *Время:* {reminder_time.strftime('%d.%m.%Y %H:%M')}\n"

        # Информация о интервале
        interval_days = reminder.get('interval_days', 0)
        if interval_days > 0:
            interval_text = f"каждые {interval_days} дней"
        else:
            interval_text = "однократно"
        message_text += f"🔄 *Повтор:* {interval_text}\n"

        # Информация о срочности
        if reminder.get('urgent_reminders'):
            urgent_until = reminder.get('urgent_until')
            if urgent_until:
                urgent_until_time = datetime.fromisoformat(urgent_until).replace(tzinfo=MOSCOW_TZ)
                time_left = urgent_until_time - current_time
                hours_left = max(0, int(time_left.total_seconds() / 3600))
                message_text += f"🚨 *СРОЧНОЕ* (осталось {hours_left}ч.)\n\n"
            else:
                message_text += "🚨 *СРОЧНОЕ* (повтор каждые 3 часа)\n\n"
        else:
            message_text += "\n"

        # Дополнительная информация для пропущенных напоминаний
        if is_missed:
            message_text += "💡 *Примечание:* Это напоминание должно было прийти ранее, но было пропущено.\n\n"

        # Создаем кнопки
        keyboard = [
            [
                InlineKeyboardButton("✅ Купил", callback_data=f"bought_{reminder['id']}"),
                InlineKeyboardButton("❌ Еще не купил", callback_data=f"not_bought_{reminder['id']}")
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем каждому пользователю и сохраняем ID сообщений
        for user_id in reminder['users']:
            try:
                # Преобразуем user_id в int
                try:
                    user_id_int = int(user_id)
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ Неверный формат user_id: {user_id}, ошибка: {e}")
                    continue

                # ПРОВЕРКА НОЧНОГО ВРЕМЕНИ (23:00 - 9:00)
                current_hour = current_time.hour

                # Если ночное время (23:00 - 9:00) и это не срочное напоминание, пропускаем отправку
                if not reminder.get('urgent_reminders') and (current_hour >= 23 or current_hour < 9):
                    logger.info(f"🌙 Пропущена отправка в ночное время для пользователя {user_id_int} (сейчас {current_time.strftime('%H:%M')})")
                    continue

                message = await application.bot.send_message(
                    chat_id=user_id_int,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )

                # Сохраняем ID нового сообщения с правильным форматом
                save_message_id(reminder['id'], user_id_int, message.message_id)

                logger.info(f"✅ Уведомление отправлено пользователю {user_id_int} с message_id {message.message_id}")

            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")

    except Exception as e:
        logger.error(f"❌ Ошибка в send_reminder_notification: {e}")

async def handle_bought_not_bought(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка кнопок 'Купил' и 'Еще не купил' для всех типов напоминаний"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"🟢 ОБРАБОТКА КНОПКИ: {data}")

    # Извлекаем reminder_id из callback_data
    if data.startswith("bought_"):
        reminder_id = data.replace("bought_", "")
        action = "bought"
    elif data.startswith("not_bought_"):
        reminder_id = data.replace("not_bought_", "")
        action = "not_bought"
    else:
        logger.error(f"❌ Неизвестный callback_data: {data}")
        return

    logger.info(f"🟢 Извлечен reminder_id: {reminder_id}, действие: {action}")

    reminders = load_reminders()
    reminder = reminders.get(reminder_id)

    if not reminder:
        logger.error(f"❌ Напоминание с ID {reminder_id} не найдено в базе")
        await query.edit_message_text("❌ Напоминание не найдено.")
        await asyncio.sleep(3)
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
        return

    user_id = str(query.from_user.id)
    users = load_users()
    user_data = users.get(user_id, {})
    username = user_data.get('username', 'Unknown')

    logger.info(f"🟢 Обработка действия '{action}' для напоминания {reminder_id} пользователем {username}")

    if action == "bought":
        # НЕМЕДЛЕННО УДАЛЯЕМ СООБЩЕНИЕ ИЗ БАЗЫ message_ids
        message_ids = load_message_ids()
        user_message_key = f"{reminder_id}_{user_id}"
        if user_message_key in message_ids:
            del message_ids[user_message_key]
            save_message_ids_to_file(message_ids)
            logger.info(f"🗑 Удален message_id для пользователя {user_id} и reminder {reminder_id}")

        # ОБРАБОТКА "КУПИЛ" ДЛЯ ВСЕХ ТИПОВ
        reminder_type = reminder.get('type', 'personal')

        # Для ингредиентов создаем план на следующую неделю и удаляем напоминание
        if reminder_type == 'ingredient':
            meal_plan_id = reminder.get('meal_plan_id')

            # Удаляем напоминание ингредиента
            del reminders[reminder_id]
            if not save_reminders(reminders):
                logger.error("❌ Ошибка при удалении напоминания ингредиента")
                await query.edit_message_text("❌ Ошибка при обработке. Попробуйте снова.")
                return

            logger.info(f"✅ Напоминание ингредиента {reminder_id} удалено")

            # СОЗДАЕМ ПЛАН НА СЛЕДУЮЩУЮ НЕДЕЛЮ
            if meal_plan_id:
                try:
                    result = await create_next_week_meal_plan(context.application, meal_plan_id)

                    # РАЗЛИЧНЫЕ СЦЕНАРИИ УСПЕХА
                    if result == "plan_already_exists":
                        # План уже был создан ранее (при обработке другого ингредиента)
                        await query.edit_message_text(
                            f"✅ {username} подтвердил(а) покупку.\n"
                            f"🍽 Напоминание удалено.\n"
                            f"📅 План на следующую неделю уже был создан ранее!"
                        )
                        logger.info(f"✅ План на следующую неделю уже существует для {meal_plan_id}")
                    elif result:
                        # План успешно создан
                        await query.edit_message_text(
                            f"✅ {username} подтвердил(а) покупку.\n"
                            f"🍽 Напоминание удалено.\n"
                            f"📅 Автоматически создан план на следующую неделю!"
                        )
                        logger.info(f"✅ Успешно создан план на следующую неделю: {result}")
                    else:
                        # Не удалось создать план
                        await query.edit_message_text(
                            f"✅ {username} подтвердил(а) покупку.\n"
                            f"🍽 Напоминание удалено.\n"
                            f"⚠️ Не удалось создать план на следующую неделю."
                        )
                        logger.warning(f"⚠️ Не удалось создать план на следующую неделю для {meal_plan_id}")

                except Exception as e:
                    logger.error(f"❌ Исключение при создании плана на следующую неделю: {e}")
                    await query.edit_message_text(
                        f"✅ {username} подтвердил(а) покупку.\n"
                        f"🍽 Напоминание удалено.\n"
                        f"⚠️ Ошибка при создании плана на следующую неделю."
                    )
            else:
                await query.edit_message_text(
                    f"✅ {username} подтвердил(а) покупку.\n"
                    f"🍽 Напоминание удалено."
                )
        else:
            # Обычные напоминания
            interval_days = reminder.get('interval_days', 0)
            if interval_days > 0:
                # Интервальное напоминание - планируем следующее
                current_time = datetime.now(MOSCOW_TZ)
                next_reminder_time = current_time + timedelta(days=interval_days)

                # Сохраняем оригинальное время
                original_time = datetime.fromisoformat(reminder['datetime']).replace(tzinfo=MOSCOW_TZ)
                next_reminder_time = next_reminder_time.replace(
                    hour=original_time.hour,
                    minute=original_time.minute,
                    second=0,
                    microsecond=0
                )

                reminder['datetime'] = next_reminder_time.isoformat()
                # Снимаем срочный режим если был
                reminder['urgent_reminders'] = False
                reminder['urgent_until'] = None
                reminder['last_sent'] = None

                if not save_reminders(reminders):
                    logger.error("❌ Ошибка при сохранении напоминания")
                else:
                    logger.info(f"✅ Напоминание обновлено: urgent_reminders={reminder['urgent_reminders']}, urgent_until={reminder['urgent_until']}")

                next_time_str = next_reminder_time.strftime('%d.%m.%Y %H:%M')
                await query.edit_message_text(
                    f"✅ {username} подтвердил(а) покупку.\n"
                    f"🔄 Следующее напоминание будет {next_time_str}.\n"
                    f"📝 Текст: {reminder['text'][:50]}..."
                )
            else:
                # Однократное напоминание - удаляем
                del reminders[reminder_id]
                if not save_reminders(reminders):
                    logger.error("❌ Ошибка при удалении напоминания")
                else:
                    logger.info(f"✅ Однократное напоминание {reminder_id} удалено")

                await query.edit_message_text(
                    f"✅ {username} подтвердил(а) покупку.\n"
                    f"🗑 Напоминание удалено.\n"
                    f"📝 Текст: {reminder['text'][:50]}..."
                )

        await asyncio.sleep(3)
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

    elif action == "not_bought":
        # ОБРАБОТКА "ЕЩЕ НЕ КУПИЛ" ДЛЯ ВСЕХ ТИПОВ
        current_time = datetime.now(MOSCOW_TZ)
        reminder_type = reminder.get('type', 'personal')

        # ДЛЯ ИНГРЕДИЕНТОВ: срочный режим работает до дня приготовления
        if reminder_type == 'ingredient':
            # Устанавливаем срочный режим для ингредиента
            reminder['urgent_reminders'] = True

            # Устанавливаем urgent_until на день приготовления (в 00:00)
            meal_date_str = reminder.get('meal_date')
            if meal_date_str:
                try:
                    meal_date = datetime.strptime(meal_date_str, '%d.%m.%Y').replace(tzinfo=MOSCOW_TZ)
                    # Устанавливаем на начало дня приготовления
                    urgent_until = meal_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    reminder['urgent_until'] = urgent_until.isoformat()
                    logger.info(f"⏰ Срочный режим для ингредиента установлен до дня приготовления: {meal_date_str}")
                except ValueError as e:
                    logger.error(f"❌ Ошибка парсинга даты приготовления: {e}")
                    # Резервный вариант: 24 часа
                    reminder['urgent_until'] = (current_time + timedelta(days=1)).isoformat()
            else:
                # Резервный вариант: 24 часа
                reminder['urgent_until'] = (current_time + timedelta(days=1)).isoformat()

        else:
            # Обычные напоминания - 24 часа срочного режима
            reminder['urgent_reminders'] = True
            reminder['urgent_until'] = (current_time + timedelta(days=1)).isoformat()

        # Для интервальных напоминаний сохраняем оригинальные данные
        interval_days = reminder.get('interval_days', 0)
        if interval_days > 0:
            reminder['original_interval'] = interval_days
            reminder['original_datetime'] = reminder['datetime']

        # Следующее срочное напоминание через 3 часа
        next_urgent_time = current_time + timedelta(hours=3)
        if next_urgent_time.hour >= 23 or next_urgent_time.hour < 9:
            next_urgent_time = next_urgent_time.replace(hour=9, minute=0, second=0)
            if next_urgent_time <= current_time:
                next_urgent_time += timedelta(days=1)

        reminder['datetime'] = next_urgent_time.isoformat()
        reminder['not_bought_count'] = reminder.get('not_bought_count', 0) + 1
        reminder['last_sent'] = None

        # Сохраняем изменения
        if not save_reminders(reminders):
            logger.error("❌ Ошибка при сохранении напоминания после активации срочного режима")
            await query.edit_message_text("❌ Ошибка при сохранении. Попробуйте снова.")
            return

        next_time_str = next_urgent_time.strftime('%d.%m.%Y %H:%M')
        logger.info(f"✅ Срочный режим активирован для {reminder_id}. Следующее напоминание: {next_time_str}")

        # УДАЛЯЕМ ТЕКУЩЕЕ СООБЩЕНИЕ
        try:
            await query.message.delete()
            logger.info(f"🗑 Удалено текущее сообщение с кнопками для reminder {reminder_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка при удалении текущего сообщения: {e}")

        # Немедленно отправляем срочное напоминание всем пользователям С ЗАМЕЩЕНИЕМ СТАРЫХ СООБЩЕНИЙ
        try:
            # ДЛЯ ИНГРЕДИЕНТОВ: используем флаг замещения для удаления старых сообщений
            if reminder_type == 'ingredient':
                await send_ingredient_reminder_notification(context.application, reminder, is_urgent_update=True)
            else:
                await send_reminder_notification(context.application, reminder, users, is_urgent_update=True)

            # Обновляем last_sent после отправки
            reminder['last_sent'] = current_time.isoformat()
            save_reminders(reminders)
            logger.info(f"✅ Немедленно отправлено срочное напоминание для {reminder_id} с замещением старых сообщений")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке срочного напоминания: {e}")

async def start_delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало удаления напоминания"""
    query = update.callback_query
    await query.answer()

    reminder_id = query.data.replace("delete_reminder_", "")
    reminders = load_reminders()
    reminder = reminders.get(reminder_id)

    if not reminder:
        await query.edit_message_text(
            "❌ Напоминание не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить удаление", callback_data=f"confirm_delete_{reminder_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_delete")]
    ]

    await query.edit_message_text(
        f"🗑 Подтвердите удаление напоминания:\n\n{reminder['text']}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DELETE_CONFIRM

async def handle_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка подтверждения удаления напоминания"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "cancel_delete":
        await query.edit_message_text(
            "❌ Удаление отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    reminder_id = data.replace("confirm_delete_", "")
    reminders = load_reminders()
    reminder = reminders.get(reminder_id)

    if not reminder:
        await query.edit_message_text(
            "❌ Напоминание не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
        return ConversationHandler.END

    user_id = str(query.from_user.id)
    reminder['delete_confirmed_by'].add(user_id)

    if len(reminder['delete_confirmed_by']) >= len(reminder['users']):
        del reminders[reminder_id]
        if not save_reminders(reminders):
            logger.error("Ошибка при записи напоминаний в файл reminders.json")
        await query.edit_message_text(
            "🗑 Напоминание удалено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )
    else:
        if not save_reminders(reminders):
            logger.error("Ошибка при записи напоминаний в файл reminders.json")
        await query.edit_message_text(
            f"✅ Вы подтвердили удаление. Ожидается подтверждение от других пользователей ({len(reminder['delete_confirmed_by'])}/{len(reminder['users'])}).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 На главную", callback_data="back_to_main")]
            ])
        )

    return ConversationHandler.END

# Определяем ConversationHandler для всех функций
remind_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("remind", start_add_reminder)],
    states={
        ADD_TEXT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_text),
            CallbackQueryHandler(cancel_reminder, pattern="^cancel_reminder$")
        ],
        ADD_DAY: [
            CallbackQueryHandler(handle_reminder_day, pattern="^(day_|back_to_text_input|cancel_reminder)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_day)
        ],
        ADD_DAY_CUSTOM: [
            CallbackQueryHandler(handle_custom_day_selection, pattern="^(show_calendar|input_days|back_to_day_selection)$")
        ],
        ADD_DAY_CALENDAR: [
            CallbackQueryHandler(handle_calendar_selection, pattern="^(cal_|back_to_custom_menu)")
        ],
        ADD_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_time),
            CommandHandler("skip", skip_to_next_available_time),
            CallbackQueryHandler(handle_back_to_calendar_from_time, pattern="^back_to_calendar_from_time$"),
            CallbackQueryHandler(cancel_reminder, pattern="^cancel_reminder$")
        ],
        ADD_INTERVAL: [
            CallbackQueryHandler(handle_reminder_interval, pattern="^(interval_|back_to_day_selection|cancel_reminder)")
        ],
        ADD_USERS: [
            CallbackQueryHandler(handle_reminder_users, pattern="^(toggle_user_|save_reminder|back_to_interval|back_to_user_selection|cancel_reminder)")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_reminder)]
)

add_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_add_reminder, pattern="^add_reminder$")],
    states={
        ADD_TEXT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_text),
            CallbackQueryHandler(cancel_reminder, pattern="^cancel_reminder$")
        ],
        ADD_DAY: [
            CallbackQueryHandler(handle_reminder_day, pattern="^(day_|back_to_text_input|cancel_reminder)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_day)
        ],
        ADD_DAY_CUSTOM: [
            CallbackQueryHandler(handle_custom_day_selection, pattern="^(show_calendar|input_days|back_to_day_selection)$")
        ],
        ADD_DAY_CALENDAR: [
            CallbackQueryHandler(handle_calendar_selection, pattern="^(cal_|back_to_custom_menu)")
        ],
        ADD_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_time),
            CommandHandler("skip", handle_reminder_time),
            CallbackQueryHandler(cancel_reminder, pattern="^cancel_reminder$")
        ],
        ADD_INTERVAL: [
            CallbackQueryHandler(handle_reminder_interval, pattern="^(interval_|back_to_day_selection|cancel_reminder)")
        ],
        ADD_USERS: [
            CallbackQueryHandler(handle_reminder_users, pattern="^(toggle_user_|save_reminder|back_to_interval|back_to_user_selection|cancel_reminder)")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_reminder)]
)

recipe_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_recipe_creation, pattern="^create_recipe$")],
    states={
        RECIPE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recipe_name),
            CallbackQueryHandler(back_to_recipe_name_handler, pattern="^back_to_recipe_name$"),  # ДОБАВИТЬ ЭТУ СТРОЧКУ
            CallbackQueryHandler(main_menu_callback, pattern="^(back_to_recipes|back_to_main)$")
        ],
        RECIPE_INGREDIENTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recipe_ingredients),
            CallbackQueryHandler(handle_recipe_confirmation, pattern="^(save_recipe|edit_recipe|cancel_recipe)$"),
            CallbackQueryHandler(back_to_recipe_name_handler, pattern="^back_to_recipe_name$"),  # ДОБАВИТЬ ЭТУ СТРОЧКУ
            CallbackQueryHandler(main_menu_callback, pattern="^(back_to_recipes|back_to_main)$")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_recipe_command)]
)

meal_plan_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_recipes_callback, pattern="^plan_meal$")],
    states={
        MEAL_DAY: [CallbackQueryHandler(handle_day_selection, pattern="^(day_|back_to_main)")],
        MEAL_RECIPE: [CallbackQueryHandler(handle_recipe_selection, pattern="^(recipe_|back_to_days|back_to_main)")],
        INGREDIENT_ASSIGNMENT: [
            CallbackQueryHandler(handle_ingredient_assignment, pattern="^(assign_ing_|back_to_recipe_selection|finish_assignment|back_to_main)"),
            CallbackQueryHandler(handle_user_selection_for_ingredient, pattern="^(select_user_|back_to_assignment|back_to_main)")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_meal_plan)]
)

delete_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_delete_reminder, pattern="^delete_reminder_")],
    states={
        DELETE_CONFIRM: [CallbackQueryHandler(handle_delete_confirmation, pattern="^(confirm_delete_|cancel_delete)$")]
    },
    fallbacks=[CommandHandler("cancel", cancel_reminder)]
)

# Добавляем ConversationHandler для редактирования рецептов
edit_recipe_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_recipe_editing, pattern="^edit_recipe_")],
    states={
        EDIT_RECIPE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_recipe_name),
            CallbackQueryHandler(back_to_edit_recipe_menu, pattern="^back_to_edit_recipe_menu$"),
            CallbackQueryHandler(handle_recipe_editing, pattern="^(edit_recipe_name|edit_recipe_ingredients|delete_recipe|edit_recipes)$")
        ],
        EDIT_RECIPE_INGREDIENTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_recipe_ingredients),
            CallbackQueryHandler(back_to_edit_recipe_menu, pattern="^back_to_edit_recipe_menu$"),
            CallbackQueryHandler(handle_recipe_editing, pattern="^(edit_recipe_name|edit_recipe_ingredients|delete_recipe|edit_recipes)$")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_recipe_command)]
)

# Добавляем ConversationHandler для редактирования планов питания
edit_plan_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_edit_plan_assignment, pattern="^change_assignees_")],
    states={
        EDIT_PLAN_ASSIGNMENT: [
            CallbackQueryHandler(handle_edit_plan_assignment, pattern="^(edit_assign_ing_|back_to_edit_plan|finish_edit_assignment|back_to_main)"),
            CallbackQueryHandler(handle_user_selection_for_ingredient, pattern="^(select_user_|back_to_assignment|back_to_main)")
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_meal_plan)]
)

if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске программы: {e}")
    finally:
        try:
            if not loop.is_closed():
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
                logger.info("🔌 Цикл событий закрыт")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии цикла событий: {e}")
