from bot_config import TELEGRAM_TOKEN
from aiogram import Bot, Dispatcher, types
from googletrans import Translator
from bs4 import BeautifulSoup

import telebot
import sqlite3
import logging
import time
import requests
import threading
import time
import html2text
import re


bot = telebot.TeleBot(TELEGRAM_TOKEN)
translator = Translator()

url_water_base = 'https://www.gwp.ge'
url_water_list = '/ka/gadaudebeli'
url_electricity = 'https://app.telasi.ge/api/view/telasi/getPoweroutages'
url_electricity_one = 'https://www.telasi.ge/company-news/power-outage?content='
db_address = '../data/users_and_streets.db'

icons = {
    "water": "💧", "electricity": "⚡"
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Ready Steady Go!")


def init_db():
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('''
		CREATE TABLE IF NOT EXISTS users
		(chat_id INTEGER PRIMARY KEY, street TEXT)
		''')
	cursor.execute('''
		CREATE TABLE IF NOT EXISTS last_water_news_url
        (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT)
        ''')
	cursor.execute('''
		CREATE TABLE IF NOT EXISTS last_electricity_news_id
        (id INTEGER PRIMARY KEY AUTOINCREMENT, content_id TEXT)
        ''')
	conn.commit()
	conn.close()

init_db()


@bot.message_handler(commands=['start'])
def handle_start(message):
	chat_id = message.chat.id

	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('INSERT OR IGNORE INTO users (chat_id) VALUES (?)', (chat_id,))
	conn.commit()
	conn.close()

	bot.send_message(chat_id, "Привет! Я бот, который будет уведомлять вас об отключениях воды и электричества на вашей улице.")
	bot.send_message(chat_id, f"Пожалуйста, укажите улицу (просто название, на английском)."
		+ "\n\n_К примеру:_ если вы напишете «Tamar», то вам будут выпадать результаты и по *Tamar*ashvili, и по Queen *Tamar* Avenue, "
		+ "а если напишете «Tamarashvili», то только по *«Tamarashvili»*.", parse_mode='Markdown')


@bot.message_handler(func=lambda message: True)
def handle_text(message):
	chat_id = message.chat.id
	street = message.text
	if is_valid_street_name(street):
		street = format_proper_street_name(street)
		conn = sqlite3.connect(db_address)
		cursor = conn.cursor()
		cursor.execute('UPDATE users SET street = ? WHERE chat_id = ?', (street, chat_id))
		conn.commit()
		conn.close()

		bot.send_message(chat_id, f"Спасибо! Теперь, когда я узнаю об отключениях на улице *{street}*, я вам тут же напишу", parse_mode='Markdown')
		check_for_water_news()
		check_for_electricity_news()
	else:
		bot.send_message(chat_id, f"Есть несколько правил:\n • Не надо оставлять пустую строку\n" 
			+ " • Не надо писать слишком много текста\n • Используйте только латиницу или цифры\n" 
			+ "В противном случае правильная работа бота будет осложнена. Попробуйте еще раз", parse_mode='Markdown')
		bot.send_message(chat_id, "Напишите вашу улицу (просто название, на английском):")


def get_username(chat_id):
	try:
		chat = bot.get_chat(chat_id)
		username = chat.username
		if username:
			return username
		else:
			return "Username не установлен"
	except Exception as e:
		logging.error(f"Не удалось получить username для chat_id {chat_id}: {e}")
		return None


def is_valid_street_name(street):
	if len(street) == 0 or len(street) > 50:
		return False
	return True


def format_proper_street_name(street):
	street = street.strip()
	street = re.sub(r'\s+', ' ', street)
	return street


def check_for_water_news():
	response = requests.get(url_water_base + url_water_list)
	soup = BeautifulSoup(response.content, 'html.parser')

	table = soup.find('table', class_='table samushaoebi')
	if table:
		first_row = table.find('tr')
		link = first_row.find('a')['href']
		full_url = url_water_base + link
		save_water_news_if_new(full_url)
	else:
		logging.error("Ошибка запроса к сайту GWP!")



def save_water_news_if_new(url):
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('SELECT url FROM last_water_news_url ORDER BY id DESC LIMIT 1')
	result = cursor.fetchone()
	logging.info(f"Вода была {result}, а теперь стала {url}")
	if result is None or result[0] != url:
		fetch_water_news_details(url)
	conn.close()


def fetch_water_news_details(url):
	response = requests.get(url)
	soup = BeautifulSoup(response.content, 'html.parser')

	news_container = soup.find('div', class_='container shua')
	news_details = news_container.find('div', class_='col-md-9 col-md-push-3 news-details')

	title_tag = news_details.find('p', class_='media-heading')
	title = translate_text(title_tag.text.strip()) if title_tag else ''

	content_div = news_details.find('div', class_='initial')
	content = translate_text(content_div.text.strip()) if content_div else '[ДАННЫЕ УДАЛЕНЫ]'

	notify_users_if_relevant(title, content, "water")
	save_water_news_details(url)


def split_text_into_chunks(text, chunk_size=3500):
	return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def save_water_news_details(url):
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('INSERT INTO last_water_news_url (url) VALUES (?)', (url,))
	conn.commit()
	conn.close()


def check_for_electricity_news():
	response = requests.get(url_electricity)
	if response.status_code == 200:
		data = response.json()
		if data['content']['list']:
			last_item = data['content']['list'][0]
			content_id = last_item['id']
			if is_electricity_news_fresh(content_id):
				fetch_electricity_news_details(last_item, content_id)
	else:
		logging.error("Ошибка запроса к API Telasi!")


def is_electricity_news_fresh(content_id):
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('SELECT content_id FROM last_electricity_news_id ORDER BY id DESC LIMIT 1')
	result = cursor.fetchone()
	conn.close()

	logging.info(f"Электричество было {result}, а теперь стало {content_id}")
	
	return result is None or result[0] != str(content_id)
	

def fetch_electricity_news_details(last_item, content_id):
	title =  translate_text(last_item['title'])
	content = translate_text(format_content(last_item['editor']))
	notify_users_if_relevant(title, content, "electricity")
	save_electricity_news_details(content_id)


def format_content(html_content):
	markdown_content = html2text.html2text(html_content)
	paragraphs = markdown_content.split('\n\n')
	cleaned_paragraphs = [' '.join(p.split()) for p in paragraphs]
	final_content = '\n\n'.join(cleaned_paragraphs)
	# result = final_content.replace('\n\n\n', '\n\n').replace('\n\n', '\n')
	return final_content


def translate_text(text):
	text = clean_text_from_extra_spaces(text)
	text = fix_comma_spacing(text)
	try:
		chunks = split_text_into_chunks(text)
		translated_chunks = []
		for chunk in chunks:
			translated_chunk = recursive_translate(chunk)
			translated_chunks.append(translated_chunk)
			time.sleep(3)
		translated_text = ' '.join(translated_chunks)
		return translated_text
	except requests.RequestException as e:
		logging.error(f"Ошибка запроса к API Google Translate: {e}")
		return text


def clean_text_from_extra_spaces(text):
	text = text.lstrip()
	# text = re.sub(r'\s+', ' ', text)
	return text


def fix_comma_spacing(text):
	text = re.sub(r'\s+,', ',', text)
	text = re.sub(r',(\S)', r', \1', text)
	return text


def recursive_translate(chunk, max_attempts=3):
	attempts = 0
	while attempts < max_attempts:
		try:
			translation = translator.translate(chunk, src='ka', dest='en').text
			if re.search(r'[აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ]', translation):
				match = re.search(r'[აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ]', translation)
				if match:
					translated_part = translation[:match.start()]
					untranslated_part = chunk[match.start():]
					retranslated_part = recursive_translate(untranslated_part)
					return translated_part + retranslated_part
			return translation
		except Exception as e:
			logging.warning(f"Ошибка при попытке {attempts+1} рекурсивного перевода: {e}")
			attempts += 1
			time.sleep(3)
	logging.error(f"Не удалось перевести текст после {max_attempts} попыток: {chunk}")
	return chunk


def save_electricity_news_details(content_id):
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('INSERT INTO last_electricity_news_id (content_id) VALUES (?)', (content_id,))
	conn.commit()
	conn.close()


def notify_users_if_relevant(title, content, i_type):
	conn = sqlite3.connect(db_address)
	cursor = conn.cursor()
	cursor.execute('SELECT chat_id, street FROM users')
	users = cursor.fetchall()
	conn.close()

	icon =  icons[i_type]
	notified_users = []

	for user in users:
		chat_id, street = user
		if chat_id and street and content:
			if street.lower() in content.lower():
				content_with_bold = highlight_inclusions(content, street)
				content_chunks = split_text_into_chunks(content_with_bold)
				for chunk in content_chunks:
					bot.send_message(chat_id, f"{icon} *{title}*\n\n{chunk}", parse_mode='Markdown')
				notified_users.append(chat_id)

	if notified_users:
		logging.info(f"Сообщение было отправлено пользователям: {', '.join(map(str, notified_users))}")


def highlight_inclusions(text, word):
	pattern = re.compile(re.escape(word), re.IGNORECASE)

	def replace_with_bold(match):
		return f"*{match.group(0)}*"

	highlighted_text = pattern.sub(replace_with_bold, text)
	return highlighted_text


def start_news_checking():
	while True:
		try:
			check_for_water_news()
			time.sleep(300)
			check_for_electricity_news()
			time.sleep(300)
		except Exception as e:
			logging.error(f"Error checking for news: {e}")
			time.sleep(7)


def run_polling():
	while True:
		try:
			bot.polling(none_stop=True, interval=0)
		except requests.exceptions.ReadTimeout as e:
			logging.error(f"ReadTimeoutError: {e}")
			time.sleep(15)
		except Exception as e:
			logging.error(f"Error in polling: {e}")
			time.sleep(15)


news_thread = threading.Thread(target=start_news_checking)
news_thread.daemon = True
news_thread.start()

run_polling()