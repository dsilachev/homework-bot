import logging
import os
import requests
import sys
import time
from http import HTTPStatus

from dotenv import load_dotenv
from telebot import TeleBot
from telebot.apihelper import ApiException

load_dotenv()

PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

RETRY_PERIOD = 600
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

file_handler = logging.FileHandler('main.log', encoding='utf-8')
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)

HOMEWORK_VERDICTS = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}


class TokenError(Exception):
    """Ошибка, если отсутствует обязательный токен."""


def check_tokens():
    """Проверяет доступность всех необходимых токенов."""
    tokens = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID
    }

    missing_tokens = [token for token, value in tokens.items() if not value]
    if missing_tokens:
        logger.critical(
            f'Отсутствуют обязательные токены: {", ".join(missing_tokens)}'
        )
        return False
    return True


def send_message(bot, message):
    """Отправляет сообщение в Telegram."""
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.debug(f'Бот отправил сообщение "{message}"')
        return True
    except ApiException as error:
        logger.error(f'Ошибка Telegram API при отправке сообщения: {error}')
        return False

    except requests.RequestException as error:
        logger.error(
            f'Сетевая ошибка при отправке сообщения в Telegram: {error}'
        )
        return False


def get_api_answer(current_timestamp):
    """Делает запрос к API."""
    params = {'from_date': current_timestamp}

    logger.info(
        f'Отправка запроса к API: {ENDPOINT}, '
        f'headers={HEADERS}, params={params}'
    )

    try:
        response = requests.get(ENDPOINT, headers=HEADERS, params=params)
    except requests.RequestException as error:
        error_msg = (
            f'Ошибка при запросе к API {ENDPOINT}, '
            f'headers={HEADERS}, params={params}. Ошибка: {error}'
        )
        raise RuntimeError(error_msg)

    if response.status_code != HTTPStatus.OK:
        error_msg = (
            f'Эндпоинт {ENDPOINT} вернул код {response.status_code}. '
            f'headers={HEADERS}, params={params}'
        )
        raise ConnectionError(error_msg)

    try:
        return response.json()
    except ValueError as error:
        error_msg = (
            f'Ошибка парсинга JSON от {ENDPOINT}, '
            f'headers={HEADERS}, params={params}. Ошибка: {error}'
        )
        raise ConnectionError(error_msg)


def check_response(response):
    """Проверяет ответ API на корректность."""
    if not isinstance(response, dict):
        error_msg = (
            f'Ответ API должен быть словарем, получен. '
            f'Получен {type(response).__name__}'
        )
        raise TypeError(error_msg)

    required_keys = ['homeworks', 'current_date']
    for key in required_keys:
        if key not in response:
            error_msg = f'В ответе API отсутствует ключ: {key}'
            raise KeyError(error_msg)

    homeworks = response['homeworks']
    if not isinstance(homeworks, list):
        error_msg = (
            f'Ключ "homeworks" должен содержать список. '
            f'Получен {type(homeworks).__name__}'
        )
        raise TypeError(error_msg)

    return homeworks


def parse_status(homework):
    """Извлекает статус работы."""
    if not isinstance(homework, dict):
        error_msg = (
            f'Домашняя работа должна быть словарем. '
            f'Получен {type(homework).__name__}'
        )
        raise TypeError(error_msg)

    required_fields = ['homework_name', 'status']
    for field in required_fields:
        if field not in homework:
            error_msg = f'В домашней работе отсутствует поле: {field}'
            raise KeyError(error_msg)

    homework_name = homework['homework_name']
    status = homework['status']

    if status not in HOMEWORK_VERDICTS:
        error_msg = f'Неожиданный статус домашней работы: {status}'
        raise ValueError(error_msg)

    verdict = HOMEWORK_VERDICTS[status]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def process_homeworks(bot, current_timestamp, last_homework_status):
    """Обрабатывает домашние работы из ответа API."""
    response = get_api_answer(current_timestamp)
    homeworks = check_response(response)

    if homeworks:
        homework = homeworks[0]
        current_status = homework.get('status')

        if current_status != last_homework_status:
            message = parse_status(homework)
            if send_message(bot, message):
                return current_status
        else:
            logger.debug('Статус домашней работы не изменился')
    else:
        logger.debug('Отсутствие в ответе новых статусов')

    return last_homework_status


def handle_recovery(bot, previous_error):
    """Обрабатывает восстановление после ошибки."""
    if previous_error:
        recovery_msg = f'Ошибка исправлена: {previous_error}'
        if send_message(bot, recovery_msg):
            logger.info(recovery_msg)
        return None
    return previous_error


def handle_error(bot, error, previous_error):
    """Обрабатывает ошибки в работе программы."""
    error_msg = f'Сбой в работе программы: {error}'
    logger.error(error_msg)

    if str(error) != previous_error:
        if send_message(bot, error_msg):
            return str(error)

    return previous_error


def main():
    """Основная логика работы бота."""
    if not check_tokens():
        raise TokenError()

    bot = TeleBot(TELEGRAM_TOKEN)
    current_timestamp = int(time.time())
    previous_error = None
    last_homework_status = None

    logger.info('Бот запущен и начал работу')

    while True:
        try:
            process_homeworks(bot, current_timestamp, last_homework_status)
            previous_error = handle_recovery(bot, previous_error)
        except Exception as error:
            previous_error = handle_error(bot, error, previous_error)

        time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
