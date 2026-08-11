import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import logging
import threading
import re
import sys
import atexit
import requests
from flask import Flask
from threading import Thread

# --- Flask Keep Alive ---
app = Flask('')

@app.route('/')
def home():
    return "HOSTING BOT IS NOW RUNNING......................."

def run_flask():
    port = int(os.environ.get("PORT", 2828))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = '8545190651:AAHJdBFPN5sz7ovfqZVvNoKucGV_s0JekqQ'  # Replace with your token
OWNER_ID = '7624692476'
ADMIN_ID = '7624692476'
YOUR_USERNAME = 'Erosagt'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 53
ADMIN_LIMIT = 9999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Command Button Layouts ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["✨ Upload File", "💎 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["🗿 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["✨ Upload File", "💎 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💤 Subscriptions", "😎 Lock Bot"],
    ["💤 Running All Code", "👀 Admin Panel"],
    ["🗿 Contact Owner"]
]

# --- Database Setup ---
def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"Invalid expiry for user {user_id}")
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            user_files.setdefault(user_id, []).append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

init_db()
load_data()

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    script_info['log_file'].close()
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                script_info['log_file'].close()
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process status: {e}")
            return False
    return False

def kill_process_tree(process_info):
    script_key = process_info.get('script_key', 'N/A')
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            process_info['log_file'].close()
        process = process_info.get('process')
        if process and hasattr(process, 'pid') and process.pid:
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        child.kill()
                gone, alive = psutil.wait_procs(children, timeout=1)
                for p in alive:
                    p.kill()
                parent.terminate()
                try:
                    parent.wait(timeout=1)
                except psutil.TimeoutExpired:
                    parent.kill()
            except psutil.NoSuchProcess:
                pass
    except Exception as e:
        logger.error(f"Error killing process tree: {e}")

# --- Automatic Package Installation ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
}
core_modules = {'asyncio','json','datetime','os','sys','re','time','math','random','logging','threading','subprocess','zipfile','tempfile','shutil','sqlite3','atexit'}
for mod in core_modules:
    TELEGRAM_MODULES[mod] = None

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        logger.info(f"Module '{module_name}' is core, skipping install")
        return False
    try:
        bot.reply_to(message, f"🐍 Installing `{package_name}`...", parse_mode='Markdown')
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', package_name],
                                capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Installed `{package_name}`.", parse_mode='Markdown')
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install `{package_name}`.", parse_mode='Markdown')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing: {e}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Installing Node package `{module_name}`...", parse_mode='Markdown')
        result = subprocess.run(['npm', 'install', module_name], cwd=user_folder,
                                capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Installed `{module_name}`.", parse_mode='Markdown')
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install `{module_name}`.", parse_mode='Markdown')
            return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ npm not found. Install Node.js.")
        return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing: {e}")
        return False

# --- Script Running Functions ---
def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Run Python {script_key} attempt {attempt}")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script '{file_name}' not found.")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding='utf-8', errors='ignore')
            try:
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if attempt_install_pip(module_name, message_obj):
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt+1)).start()
                            return
                        else:
                            bot.reply_to(message_obj, f"❌ Install failed for `{module_name}`.")
                            return
                    else:
                        bot.reply_to(message_obj, f"❌ Pre-check error:\n```\n{stderr[:300]}\n```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()
                logger.info("Pre-check timed out, proceeding to run.")

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                   stdout=log_file, stderr=log_file,
                                   stdin=subprocess.PIPE, startupinfo=startupinfo,
                                   encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'py',
            'script_key': script_key
        }
        bot.reply_to(message_obj, f"✅ Python script '{file_name}' started (PID: {process.pid})")
    except Exception as e:
        logger.error(f"Error starting Python script: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error: {e}")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Run JS {script_key} attempt {attempt}")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script '{file_name}' not found.")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = subprocess.Popen(['node', script_path], cwd=user_folder,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding='utf-8', errors='ignore')
            try:
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj):
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt+1)).start()
                                return
                            else:
                                bot.reply_to(message_obj, f"❌ NPM install failed for `{module_name}`.")
                                return
                    else:
                        bot.reply_to(message_obj, f"❌ Pre-check error:\n```\n{stderr[:300]}\n```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()
                logger.info("JS pre-check timed out, proceeding to run.")

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(['node', script_path], cwd=user_folder,
                                   stdout=log_file, stderr=log_file,
                                   stdin=subprocess.PIPE, startupinfo=startupinfo,
                                   encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'js',
            'script_key': script_key
        }
        bot.reply_to(message_obj, f"✅ JS script '{file_name}' started (PID: {process.pid})")
    except Exception as e:
        logger.error(f"Error starting JS script: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error: {e}")

# --- Database Operations ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"DB save file error: {e}")
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
        except Exception as e:
            logger.error(f"DB remove file error: {e}")
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"DB add active user error: {e}")
        finally:
            conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"DB save subscription error: {e}")
        finally:
            conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"DB remove subscription error: {e}")
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"DB add admin error: {e}")
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            if c.rowcount > 0:
                admin_ids.discard(admin_id)
                return True
            return False
        except Exception as e:
            logger.error(f"DB remove admin error: {e}")
            return False
        finally:
            conn.close()

# --- Menu Creation ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('🏜️ Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
        types.InlineKeyboardButton('🗿 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('👀 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0], buttons[1])
        markup.add(buttons[2], admin_buttons[0])
        markup.add(buttons[3], admin_buttons[1])
        markup.add(admin_buttons[2], admin_buttons[3])
        markup.add(buttons[4])
    else:
        markup.add(buttons[0], buttons[1])
        markup.add(buttons[2], buttons[3])
        markup.add(buttons[4])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# --- File Handling ---
def handle_zip_file(content, zip_name, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
    try:
        zip_path = os.path.join(temp_dir, zip_name)
        with open(zip_path, 'wb') as f:
            f.write(content)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)

        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        js_files = [f for f in extracted if f.endswith('.js')]

        if 'requirements.txt' in extracted:
            bot.reply_to(message, "🔄 Installing Python dependencies...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, 'requirements.txt')],
                           check=False, capture_output=True)

        if 'package.json' in extracted:
            bot.reply_to(message, "🔄 Installing Node dependencies...")
            subprocess.run(['npm', 'install'], cwd=temp_dir, check=False, capture_output=True)

        main_script = None
        file_type = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files:
                main_script = p; file_type = 'py'; break
        if not main_script:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in js_files:
                    main_script = p; file_type = 'js'; break
        if not main_script:
            if py_files:
                main_script = py_files[0]; file_type = 'py'
            elif js_files:
                main_script = js_files[0]; file_type = 'js'
        if not main_script:
            bot.reply_to(message, "❌ No .py or .js script found in archive.")
            return

        for item in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item)
            dst = os.path.join(user_folder, item)
            if os.path.exists(dst):
                if os.path.isdir(dst): shutil.rmtree(dst)
                else: os.remove(dst)
            shutil.move(src, dst)

        save_user_file(user_id, main_script, file_type)
        script_path = os.path.join(user_folder, main_script)
        bot.reply_to(message, f"✅ Extracted and starting `{main_script}`...", parse_mode='Markdown')
        if file_type == 'py':
            threading.Thread(target=run_script, args=(script_path, user_id, user_folder, main_script, message)).start()
        else:
            threading.Thread(target=run_js_script, args=(script_path, user_id, user_folder, main_script, message)).start()
    except Exception as e:
        logger.error(f"Error processing zip: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# --- Logic Functions ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    if user_id not in active_users:
        add_active_user(user_id)

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "👑 Owner"
    elif user_id in admin_ids: user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry = user_subscriptions[user_id].get('expiry')
        if expiry and expiry > datetime.now():
            user_status = "⭐ Premium"
            days_left = (expiry - datetime.now()).days
            expiry_info = f"\n⏳ Expires in {days_left} days"
        else:
            user_status = "🆓 Free User (Expired)"
            remove_subscription_db(user_id)
    else:
        user_status = "🆓 Free User"

    welcome_msg = (f"〽️ Welcome, {user_name}!\n\n"
                   f"🆔 ID: `{user_id}`\n"
                   f"✳️ Username: @{user_username or 'Not set'}\n"
                   f"🔰 Status: {user_status}{expiry_info}\n"
                   f"📁 Files: {current_files} / {limit_str}\n\n"
                   f"🤖 Host & run Python/JS scripts.\n"
                   f"Upload `.py`, `.js`, or `.zip`.\n\n"
                   f"👇 Use buttons or commands.")
    markup = create_reply_keyboard_main_menu(user_id)
    bot.send_message(chat_id, welcome_msg, reply_markup=markup, parse_mode='Markdown')

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked.")
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ Limit reached ({current_files}/{limit_str}). Delete files first.")
        return
    bot.reply_to(message, "📤 Send Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

def _logic_check_files(message):
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, "💎 No files uploaded yet.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files):
        running = is_bot_running(user_id, fname)
        status = "🟢 Running" if running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {status}",
                                               callback_data=f'file_{user_id}_{fname}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    bot.reply_to(message, "💎 Your files:", reply_markup=markup)

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start = time.time()
    wait = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: level = "👑 Owner"
        elif user_id in admin_ids: level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            level = "⭐ Premium"
        else: level = "🆓 Free User"
        msg = (f"⚡ Bot Speed & Status:\n\n"
               f"⏱️ Response: {response_time} ms\n"
               f"🚦 Status: {status}\n"
               f"👤 Level: {level}")
        bot.edit_message_text(msg, chat_id, wait.message_id)
    except Exception as e:
        logger.error(f"Speed test error: {e}")
        bot.edit_message_text("❌ Speed test failed.", chat_id, wait.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=markup)

# --- Admin Logic ---
def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "💳 Subscription Management", reply_markup=create_subscription_menu())

def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    running = 0
    for key, info in list(bot_scripts.items()):
        owner, fname = key.split('_', 1)
        if is_bot_running(int(owner), info['file_name']):
            running += 1
    user_running = sum(1 for key in bot_scripts if key.startswith(f"{user_id}_") and is_bot_running(user_id, key.split('_',1)[1]))
    msg = f"📊 Statistics:\n\n👥 Users: {total_users}\n💎 Files: {total_files}\n🟢 Active bots: {running}"
    if user_id in admin_ids:
        msg += f"\n🔒 Bot locked: {'Yes' if bot_locked else 'No'}"
    msg += f"\n🤖 Your running bots: {user_running}"
    bot.reply_to(message, msg)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    global bot_locked
    bot_locked = not bot_locked
    bot.reply_to(message, f"🔒 Bot {'locked' if bot_locked else 'unlocked'}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "👀 Admin Panel", reply_markup=create_admin_panel())

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_id = message_or_call.from_user.id
        reply_func = lambda t, **k: bot.reply_to(message_or_call, t, **k)
        msg_obj = message_or_call
    else:  # CallbackQuery
        admin_id = message_or_call.from_user.id
        bot.answer_callback_query(message_or_call.id)
        reply_func = lambda t, **k: bot.send_message(message_or_call.message.chat.id, t, **k)
        msg_obj = message_or_call.message

    if admin_id not in admin_ids:
        reply_func("⚠️ Admin only.")
        return

    reply_func("⏳ Starting all scripts...")
    started = 0
    for uid, files in list(user_files.items()):
        for fname, ftype in files:
            if not is_bot_running(uid, fname):
                fpath = os.path.join(get_user_folder(uid), fname)
                if os.path.exists(fpath):
                    try:
                        if ftype == 'py':
                            threading.Thread(target=run_script, args=(fpath, uid, get_user_folder(uid), fname, msg_obj)).start()
                        else:
                            threading.Thread(target=run_js_script, args=(fpath, uid, get_user_folder(uid), fname, msg_obj)).start()
                        started += 1
                        time.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Error starting {fname} for {uid}: {e}")
    reply_func(f"✅ Finished. Started {started} scripts.")

# --- Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message): _logic_send_welcome(message)

@bot.message_handler(commands=['uploadfile'])
def cmd_upload(message): _logic_upload_file(message)

@bot.message_handler(commands=['checkfiles'])
def cmd_check(message): _logic_check_files(message)

@bot.message_handler(commands=['botspeed'])
def cmd_speed(message): _logic_bot_speed(message)

@bot.message_handler(commands=['contactowner'])
def cmd_contact(message): _logic_contact_owner(message)

@bot.message_handler(commands=['status', 'statistics'])
def cmd_stats(message): _logic_statistics(message)

@bot.message_handler(commands=['subscriptions'])
def cmd_subscriptions(message): _logic_subscriptions_panel(message)

@bot.message_handler(commands=['lockbot'])
def cmd_lock(message): _logic_toggle_lock_bot(message)

@bot.message_handler(commands=['adminpanel'])
def cmd_admin(message): _logic_admin_panel(message)

@bot.message_handler(commands=['runningallcode'])
def cmd_runall(message): _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    start = time.time()
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)

BUTTON_TEXT_TO_LOGIC = {
    "✨ Upload File": _logic_upload_file,
    "💎 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📊 Statistics": _logic_statistics,
    "🗿 Contact Owner": _logic_contact_owner,
    "💤 Subscriptions": _logic_subscriptions_panel,
    "😎 Lock Bot": _logic_toggle_lock_bot,
    "💤 Running All Code": _logic_run_all_scripts,
    "👀 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    BUTTON_TEXT_TO_LOGIC[message.text](message)

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
def handle_doc(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked.")
        return
    file_limit = get_user_file_limit(user_id)
    if get_user_file_count(user_id) >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ Limit reached ({get_user_file_count(user_id)}/{limit_str}).")
        return

    doc = message.document
    fname = doc.file_name
    if not fname:
        bot.reply_to(message, "⚠️ No file name.")
        return
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only .py, .js, .zip allowed.")
        return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ File too large (max 20 MB).")
        return

    try:
        wait = bot.reply_to(message, f"⏳ Downloading `{fname}`...", parse_mode='Markdown')
        file_info = bot.get_file(doc.file_id)
        content = bot.download_file(file_info.file_path)
        bot.edit_message_text(f"✅ Downloaded. Processing...", message.chat.id, wait.message_id)

        user_folder = get_user_folder(user_id)
        if ext == '.zip':
            handle_zip_file(content, fname, message)
        else:
            fpath = os.path.join(user_folder, fname)
            with open(fpath, 'wb') as f:
                f.write(content)
            save_user_file(user_id, fname, ext[1:])
            if ext == '.py':
                threading.Thread(target=run_script, args=(fpath, user_id, user_folder, fname, message)).start()
            else:
                threading.Thread(target=run_js_script, args=(fpath, user_id, user_folder, fname, message)).start()
    except Exception as e:
        logger.error(f"File handling error: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {e}")

# --- Callback Query Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked.", show_alert=True)
        return

    if data == 'upload':
        upload_callback(call)
    elif data == 'check_files':
        check_files_callback(call)
    elif data.startswith('file_'):
        file_control_callback(call)
    elif data.startswith('start_'):
        start_bot_callback(call)
    elif data.startswith('stop_'):
        stop_bot_callback(call)
    elif data.startswith('restart_'):
        restart_bot_callback(call)
    elif data.startswith('delete_'):
        delete_bot_callback(call)
    elif data.startswith('logs_'):
        logs_bot_callback(call)
    elif data == 'speed':
        speed_callback(call)
    elif data == 'back_to_main':
        back_to_main_callback(call)
    elif data == 'subscription':
        admin_required(call, subscription_management_callback)
    elif data == 'stats':
        stats_callback(call)
    elif data == 'lock_bot':
        admin_required(call, lock_bot_callback)
    elif data == 'unlock_bot':
        admin_required(call, unlock_bot_callback)
    elif data == 'run_all_scripts':
        admin_required(call, run_all_scripts_callback)
    elif data == 'admin_panel':
        admin_required(call, admin_panel_callback)
    elif data == 'add_admin':
        owner_required(call, add_admin_init_callback)
    elif data == 'remove_admin':
        owner_required(call, remove_admin_init_callback)
    elif data == 'list_admins':
        admin_required(call, list_admins_callback)
    elif data == 'add_subscription':
        admin_required(call, add_subscription_init_callback)
    elif data == 'remove_subscription':
        admin_required(call, remove_subscription_init_callback)
    elif data == 'check_subscription':
        admin_required(call, check_subscription_init_callback)
    else:
        bot.answer_callback_query(call.id, "Unknown action.")

def admin_required(call, func):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    func(call)

def owner_required(call, func):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    func(call)

def upload_callback(call):
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    if get_user_file_count(user_id) >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ Limit reached ({get_user_file_count(user_id)}/{limit_str}).", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send your file.")

def check_files_callback(call):
    user_id = call.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.answer_callback_query(call.id, "No files.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files):
        running = is_bot_running(user_id, fname)
        status = "🟢 Running" if running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {status}",
                                               callback_data=f'file_{user_id}_{fname}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    bot.edit_message_text("💎 Your files:", call.message.chat.id, call.message.message_id, reply_markup=markup)

def file_control_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
            return
        files = user_files.get(owner, [])
        if not any(f[0] == fname for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        running = is_bot_running(owner, fname)
        ftype = next(f[1] for f in files if f[0] == fname)
        bot.answer_callback_query(call.id)
        txt = f"⚙️ Controls for `{fname}` ({ftype}) User `{owner}`\nStatus: {'🟢 Running' if running else '🔴 Stopped'}"
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner, fname, running), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"file_control_callback error: {e}")

def start_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not allowed.", show_alert=True)
            return
        files = user_files.get(owner, [])
        ftype = next((f[1] for f in files if f[0] == fname), None)
        if not ftype:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        if is_bot_running(owner, fname):
            bot.answer_callback_query(call.id, "Already running.", show_alert=True)
            return
        fpath = os.path.join(get_user_folder(owner), fname)
        if not os.path.exists(fpath):
            bot.answer_callback_query(call.id, "File missing.", show_alert=True)
            remove_user_file_db(owner, fname)
            return
        bot.answer_callback_query(call.id, "⏳ Starting...")
        if ftype == 'py':
            threading.Thread(target=run_script, args=(fpath, owner, get_user_folder(owner), fname, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(fpath, owner, get_user_folder(owner), fname, call.message)).start()
        time.sleep(1)
        running = is_bot_running(owner, fname)
        status = '🟢 Running' if running else '🟡 Starting (check logs)'
        txt = f"⚙️ Controls for `{fname}` ({ftype}) User `{owner}`\nStatus: {status}"
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner, fname, running), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"start_bot_callback error: {e}")

def stop_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not allowed.", show_alert=True)
            return
        if not is_bot_running(owner, fname):
            bot.answer_callback_query(call.id, "Not running.", show_alert=True)
            return
        script_key = f"{owner}_{fname}"
        info = bot_scripts.get(script_key)
        if info:
            kill_process_tree(info)
            if script_key in bot_scripts: del bot_scripts[script_key]
        bot.answer_callback_query(call.id, "⏳ Stopping...")
        time.sleep(0.5)
        ftype = next((f[1] for f in user_files.get(owner, []) if f[0] == fname), '?')
        txt = f"⚙️ Controls for `{fname}` ({ftype}) User `{owner}`\nStatus: 🔴 Stopped"
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner, fname, False), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"stop_bot_callback error: {e}")

def restart_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not allowed.", show_alert=True)
            return
        script_key = f"{owner}_{fname}"
        info = bot_scripts.get(script_key)
        if info:
            kill_process_tree(info)
            if script_key in bot_scripts: del bot_scripts[script_key]
        bot.answer_callback_query(call.id, "🔄 Restarting...")
        time.sleep(1)
        files = user_files.get(owner, [])
        ftype = next((f[1] for f in files if f[0] == fname), None)
        if not ftype:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        fpath = os.path.join(get_user_folder(owner), fname)
        if not os.path.exists(fpath):
            bot.answer_callback_query(call.id, "File missing.", show_alert=True)
            remove_user_file_db(owner, fname)
            return
        if ftype == 'py':
            threading.Thread(target=run_script, args=(fpath, owner, get_user_folder(owner), fname, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(fpath, owner, get_user_folder(owner), fname, call.message)).start()
        time.sleep(1)
        running = is_bot_running(owner, fname)
        status = '🟢 Running' if running else '🟡 Starting (check logs)'
        txt = f"⚙️ Controls for `{fname}` ({ftype}) User `{owner}`\nStatus: {status}"
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner, fname, running), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"restart_bot_callback error: {e}")

def delete_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not allowed.", show_alert=True)
            return
        if is_bot_running(owner, fname):
            script_key = f"{owner}_{fname}"
            info = bot_scripts.get(script_key)
            if info:
                kill_process_tree(info)
                if script_key in bot_scripts: del bot_scripts[script_key]
        user_folder = get_user_folder(owner)
        fpath = os.path.join(user_folder, fname)
        if os.path.exists(fpath): os.remove(fpath)
        log_path = os.path.join(user_folder, f"{os.path.splitext(fname)[0]}.log")
        if os.path.exists(log_path): os.remove(log_path)
        remove_user_file_db(owner, fname)
        bot.answer_callback_query(call.id, f"🗑️ Deleted {fname}.")
        bot.edit_message_text(f"🗑️ `{fname}` deleted.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"delete_bot_callback error: {e}")

def logs_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner = int(owner_str)
        user = call.from_user.id
        if not (user == owner or user in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Not allowed.", show_alert=True)
            return
        log_path = os.path.join(get_user_folder(owner), f"{os.path.splitext(fname)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, "No logs.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if len(content) > 4000:
            content = content[-4000:]
            content = "...\n" + content
        bot.send_message(call.message.chat.id, f"📜 Logs for `{fname}`:\n```\n{content}\n```", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"logs_bot_callback error: {e}")

def speed_callback(call):
    start = time.time()
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("🏃 Testing speed...", call.message.chat.id, call.message.message_id)
        bot.send_chat_action(call.message.chat.id, 'typing')
        response_time = round((time.time() - start) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        user_id = call.from_user.id
        if user_id == OWNER_ID: level = "👑 Owner"
        elif user_id in admin_ids: level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            level = "⭐ Premium"
        else: level = "🆓 Free User"
        msg = (f"⚡ Bot Speed & Status:\n\n"
               f"⏱️ Response: {response_time} ms\n"
               f"🚦 Status: {status}\n"
               f"👤 Level: {level}")
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id,
                              reply_markup=create_main_menu_inline(user_id))
    except Exception as e:
        logger.error(f"speed_callback error: {e}")
        bot.edit_message_text("❌ Speed test failed.", call.message.chat.id, call.message.message_id)

def back_to_main_callback(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    file_limit = get_user_file_limit(user_id)
    current = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: status = "👑 Owner"
    elif user_id in admin_ids: status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry = user_subscriptions[user_id].get('expiry')
        if expiry and expiry > datetime.now():
            status = "⭐ Premium"
            days = (expiry - datetime.now()).days
            expiry_info = f"\n⏳ Expires in {days} days"
        else:
            status = "🆓 Free User (Expired)"
    else:
        status = "🆓 Free User"
    text = (f"〽️ Welcome back!\n\n🆔 ID: `{user_id}`\n"
            f"🔰 Status: {status}{expiry_info}\n📁 Files: {current} / {limit_str}\n\n"
            f"👇 Use buttons or commands.")
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')

# --- Admin callbacks ---
def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("💳 Subscription Management", call.message.chat.id, call.message.message_id,
                          reply_markup=create_subscription_menu())

def stats_callback(call):
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=create_main_menu_inline(call.from_user.id))
    except:
        pass

def lock_bot_callback(call):
    global bot_locked
    bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Locked.")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                  reply_markup=create_main_menu_inline(call.from_user.id))

def unlock_bot_callback(call):
    global bot_locked
    bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Unlocked.")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                  reply_markup=create_main_menu_inline(call.from_user.id))

def run_all_scripts_callback(call):
    _logic_run_all_scripts(call)

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("👀 Admin Panel", call.message.chat.id, call.message.message_id,
                          reply_markup=create_admin_panel())

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to add as admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid <= 0: raise ValueError
        if uid in admin_ids:
            bot.reply_to(message, f"⚠️ User `{uid}` already admin.")
            return
        add_admin_db(uid)
        bot.reply_to(message, f"✅ User `{uid}` promoted to admin.")
        try: bot.send_message(uid, "🎉 You are now an admin.")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID. Send numeric ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID:")
        bot.register_next_step_handler(msg, process_add_admin)

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to remove admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin)

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid == OWNER_ID:
            bot.reply_to(message, "⚠️ Cannot remove owner.")
            return
        if uid not in admin_ids:
            bot.reply_to(message, f"⚠️ User `{uid}` not admin.")
            return
        if remove_admin_db(uid):
            bot.reply_to(message, f"✅ Admin `{uid}` removed.")
            try: bot.send_message(uid, "ℹ️ You are no longer admin.")
            except: pass
        else:
            bot.reply_to(message, f"❌ Failed to remove admin.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID:")
        bot.register_next_step_handler(msg, process_remove_admin)

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    admin_list = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(admin_ids))
    bot.edit_message_text(f"👑 Admins:\n\n{admin_list}", call.message.chat.id, call.message.message_id,
                          reply_markup=create_admin_panel(), parse_mode='Markdown')

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID and days (e.g., `12345678 30`).\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_subscription)

def process_add_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError
        uid = int(parts[0]); days = int(parts[1])
        if uid <= 0 or days <= 0: raise ValueError
        current_expiry = user_subscriptions.get(uid, {}).get('expiry')
        start = current_expiry if current_expiry and current_expiry > datetime.now() else datetime.now()
        new_expiry = start + timedelta(days=days)
        save_subscription(uid, new_expiry)
        bot.reply_to(message, f"✅ Sub for `{uid}` extended by {days} days.\nExpires: {new_expiry:%Y-%m-%d}")
        try: bot.send_message(uid, f"🎉 Sub extended {days} days until {new_expiry:%Y-%m-%d}")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid format. Use: `ID days`")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID and days:")
        bot.register_next_step_handler(msg, process_add_subscription)

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to remove subscription.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_subscription)

def process_remove_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid not in user_subscriptions:
            bot.reply_to(message, f"⚠️ User `{uid}` has no active subscription.")
            return
        remove_subscription_db(uid)
        bot.reply_to(message, f"✅ Subscription for `{uid}` removed.")
        try: bot.send_message(uid, "ℹ️ Your subscription was removed by admin.")
        except: pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID:")
        bot.register_next_step_handler(msg, process_remove_subscription)

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check subscription.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription)

def process_check_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            expiry = user_subscriptions[uid].get('expiry')
            if expiry:
                if expiry > datetime.now():
                    days = (expiry - datetime.now()).days
                    bot.reply_to(message, f"✅ User `{uid}` has active sub. Expires: {expiry:%Y-%m-%d} ({days} days left).")
                else:
                    bot.reply_to(message, f"⚠️ User `{uid}` has expired sub (on {expiry:%Y-%m-%d}).")
                    remove_subscription_db(uid)
            else:
                bot.reply_to(message, f"⚠️ User `{uid}` in sub list but no expiry.")
        else:
            bot.reply_to(message, f"ℹ️ User `{uid}` has no subscription.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID:")
        bot.register_next_step_handler(msg, process_check_subscription)

# --- Cleanup ---
def cleanup():
    logger.warning("Shutting down. Stopping all scripts...")
    for key in list(bot_scripts.keys()):
        info = bot_scripts.get(key)
        if info:
            kill_process_tree(info)
    logger.warning("Cleanup done.")
atexit.register(cleanup)

# --- Main ---
if __name__ == '__main__':
    keep_alive()
    logger.info("Bot started.")
    # Delete any existing webhook to allow polling
    try:
        bot.delete_webhook()
        logger.info("Webhook deleted.")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
