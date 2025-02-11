import os
import logging
import datetime
import json
import re
import requests
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import (
    Updater,
    CallbackContext,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# GROUP_CHAT_ID = -1001758042522
GROUP_CHAT_ID = -1001235922002

def process_timer_request(update: Update, context: CallbackContext):
    """
    Обрабатывает сообщения от пользователя, содержащие слово "Рома".
    Если пользователь не из списка разрешённых или ИИ вернул {"timer": false}, то функция ничего не делает.
    """
    allowed_ids = [1273867987, 1534121473]
    # Я, Артикуль, гн
    user_id = update.message.from_user.id
    if user_id not in allowed_ids:
        logger.info("Пользователь %s не авторизован для использования команды", user_id)
        return

    text = update.message.text
    if not re.search(r'\Рома\b', text, re.IGNORECASE):
        return

    # Формируем запрос к ИИ
    payload = {
        "model": "Meta-Llama-3.1-70B-Instruct",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты Рома, который на основе запроса пользователя должен определить "
                    "длительность в секундах, придумать креативную подпись таймера отталкиваясь caption от контекста для сообщения и сформировать ответ answer, "
                    "который начнёт выплняться после завершения таймера. "
                    "Возвращай ответ только в формате JSON, например: "
                    '{"duration": 60, "caption": "", "answer": ""}. '
                    "Если в запросе не указано время, то верни "
                    '{"timer":false}'
                )
            },
            {
                "role": "user",
                "content": text
            }
        ],
        "stream": False
    }

    API_AI_OK = os.environ.get("API_AI")
    headers = {
        "Authorization": f"Bearer {API_AI_OK}",
        "Content-Type": "application/json"
    }
    url = "https://api.sambanova.ai/v1/chat/completions"

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        response_data = response.json()
        choices = response_data.get("choices", [])
        if choices:
            ai_output = choices[0].get("message", {}).get("content", "")
        else:
            ai_output = ""
    except Exception as e:
        logger.error("Ошибка запроса к AI: %s", e)
        ai_output = ""

    # Значения по умолчанию
    default_duration = 60
    default_caption = "ЧАТ БУДЕТ УДАЛЁН ЧЕРЕЗ"
    default_answer = "Таймер завершён!"

    try:
        result = json.loads(ai_output)
        # Если ИИ вернул {"timer": false}, ничего не делаем
        if result.get("timer") is False:
            logger.info("Ответ AI с timer=false, ничего не делаю.")
            return

        timer_seconds = int(result.get("duration", default_duration))
        caption = result.get("caption", default_caption)
        answer = result.get("answer", default_answer)
    except Exception as e:
        logger.error("Ошибка разбора ответа ИИ: %s", e)
        found = re.search(r"\d+", ai_output)
        timer_seconds = int(found.group(0)) if found else default_duration
        caption = default_caption
        answer = default_answer

    logger.info("Определено время: %s секунд, подпись: %s, ответ: %s", timer_seconds, caption, answer)

    # Вычисляем время окончания таймера (добавляем 1 сек для компенсации)
    start_time = datetime.datetime.now()
    finish_time = start_time + datetime.timedelta(seconds=timer_seconds + 1)

    # Формируем inline-клавиатуру с кнопкой, показывающей оставшееся время
    timer_text = f"{timer_seconds} сек"
    keyboard = [[InlineKeyboardButton(timer_text, callback_data="timer")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение в группу
    sent_message = context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=caption,
        reply_markup=reply_markup
    )
    # Закрепляем сообщение в группе
    context.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent_message.message_id)

    # Контекст для обновления таймера
    job_context = {
        "group_chat_id": GROUP_CHAT_ID,
        "message_id": sent_message.message_id,
        "finish_time": finish_time,
        "source_chat_id": update.effective_chat.id,
        "source_message_id": update.message.message_id,
        "answer": answer
    }

    # Определяем начальный интервал обновления: 5 сек, если таймер больше 10 сек, иначе 1 сек.
    update_interval = 5 if timer_seconds > 10 else 1
    context.job_queue.run_repeating(
        callback=update_timer,
        interval=update_interval,
        first=update_interval,
        context=job_context,
        name=str(sent_message.message_id)
    )

def update_timer(context: CallbackContext):
    """
    Обновляет оставшееся время на кнопке.
    Если оставшихся секунд меньше 10 и интервал еще не изменен, переключает обновление на каждую секунду.
    По истечении таймера удаляет задачу и отправляет ответ, который является реплаем на сообщение о запуске таймера.
    """
    job_context = context.job.context
    group_chat_id = job_context["group_chat_id"]
    message_id = job_context["message_id"]
    finish_time = job_context["finish_time"]

    now = datetime.datetime.now()
    remaining = finish_time - now
    remaining_seconds = int(remaining.total_seconds())
    if remaining_seconds < 0:
        remaining_seconds = 0

    # Если осталось меньше 10 секунд и интервал еще не переключен, пересоздаем задачу с интервалом 1 секунда
    if remaining_seconds <= 10 and not job_context.get("changed_interval", False):
        job_context["changed_interval"] = True
        context.job_queue.run_repeating(
            callback=update_timer,
            interval=1,
            first=1,
            context=job_context,
            name=str(message_id) + "_1sec"
        )
        context.job.schedule_removal()
        return

    timer_text = f"{remaining_seconds} сек"
    keyboard = [[InlineKeyboardButton(timer_text, callback_data="timer")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        context.bot.edit_message_reply_markup(
            chat_id=group_chat_id,
            message_id=message_id,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error("Ошибка при обновлении таймера: %s", e)

    if now >= finish_time:
        context.job.schedule_removal()
        source_chat_id = job_context["source_chat_id"]
        source_message_id = job_context["source_message_id"]
        answer = job_context["answer"]
        # Отправляем ответ как реплай на исходное сообщение
        context.bot.send_message(
            chat_id=source_chat_id,
            text=answer,
            reply_to_message_id=source_message_id
        )

def button_callback(update: Update, context: CallbackContext):
    """
    Обработчик нажатия на inline-кнопку (убираем "часики").
    """
    query = update.callback_query
    query.answer()

def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(MessageHandler(Filters.text, process_timer_request))
    dp.add_handler(CallbackQueryHandler(button_callback))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
