import os
import logging
import asyncio
import qrcode
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфиг
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

# Состояния
METHOD, PHONE, CODE, PASSWORD, QR_WAIT = range(5)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.qr_sessions = {}
    
    # 🔥 МЕТОД 1: Автоматическая отправка кода
    async def auto_method(self, phone: str, user_id: int):
        """Автоматическая отправка кода"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            sent_code = await client.send_code_request(phone)
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': 'auto'
            }
            
            return True, "✅ Запрос кода отправлен! Проверьте Telegram."
            
        except FloodWaitError as e:
            return False, f"❌ Слишком много запросов. Подождите {e.seconds} секунд"
        except PhoneNumberInvalidError:
            return False, "❌ Неверный номер телефона"
        except Exception as e:
            logger.error(f"Auto method error: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 МЕТОД 2: Ручной ввод кода (ИСПРАВЛЕННЫЙ)
    async def manual_method(self, phone: str, user_id: int):
        """Ручной метод - пользователь сам получает код"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            # Для ручного метода тоже отправляем код, но пользователь получает его сам
            sent_code = await client.send_code_request(phone)
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': 'manual'
            }
            
            return True, (
                "📱 **Ручной метод:**\n\n"
                "1. Откройте официальный Telegram\n"
                "2. Введите номер телефона: " + phone + "\n"
                "3. Получите код подтверждения\n"
                "4. Введите код сюда\n\n"
                "🔢 **Введите код из Telegram:**"
            )
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 МЕТОД 3: QR-код
    async def qr_method(self, user_id: int):
        """Метод с QR-кодом"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            # Генерируем QR-код
            qr_login = await client.qr_login()
            
            self.qr_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'method': 'qr'
            }
            
            # Создаем QR-код изображение
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)
            
            return True, bio, qr_login
            
        except Exception as e:
            return False, f"❌ Ошибка QR-метода: {str(e)}", None
    
    # 🔥 ОБРАБОТКА КОДА ДЛЯ АВТО И РУЧНОГО МЕТОДОВ (ИСПРАВЛЕННАЯ)
    async def verify_code(self, user_id: int, code: str):
        """Проверка кода для auto и manual методов"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            # Используем правильный метод sign_in для всех случаев
            await data['client'].sign_in(
                phone=data['phone'],
                code=code,
                phone_code_hash=data['phone_code_hash']
            )
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
            
        except SessionPasswordNeededError:
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            return False, "❌ Неверный код"
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 ОБРАБОТКА ПАРОЛЯ 2FA
    async def verify_password(self, user_id: int, password: str):
        """Проверка пароля 2FA"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            await data['client'].sign_in(password=password)
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Неверный пароль: {str(e)}"
    
    # 🔥 ОЖИДАНИЕ QR-АВТОРИЗАЦИИ
    async def wait_qr_login(self, user_id: int):
        """Ожидание авторизации по QR-коду"""
        if user_id not in self.qr_sessions:
            return False, "❌ QR сессия не найдена"
        
        data = self.qr_sessions[user_id]
        
        try:
            # Ждем авторизацию (таймаут 120 секунд)
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            
            return True, session_string
            
        except asyncio.TimeoutError:
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            return False, "❌ Время ожидания истекло. QR-код больше не действителен."
        except Exception as e:
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            return False, f"❌ Ошибка QR-авторизации: {str(e)}"

manager = SessionManager()

# 🔥 ГЛАВНОЕ МЕНЮ ВЫБОРА МЕТОДА
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало - выбор метода"""
    keyboard = [
        [InlineKeyboardButton("🔐 Автоматическая отправка кода", callback_data="auto")],
        [InlineKeyboardButton("📱 Ручной ввод кода", callback_data="manual")],
        [InlineKeyboardButton("📷 QR-код", callback_data="qr")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 **Генератор сессий Telegram**\n\n"
        "Выберите способ авторизации:\n\n"
        "• 🔐 **Авто** - бот отправит код (может не работать)\n"
        "• 📱 **Ручной** - вы получаете код вручную (надежно)\n"
        "• 📷 **QR-код** - сканируете код (самый простой)\n\n"
        "💡 **Рекомендуем:** Ручной или QR-код",
        reply_markup=reply_markup
    )
    return METHOD

async def handle_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора метода"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    method = query.data
    
    context.user_data['method'] = method
    
    if method == 'qr':
        # Сразу запускаем QR-метод
        await query.edit_message_text("🔄 Генерируем QR-код...")
        
        success, qr_image, qr_login = await manager.qr_method(user_id)
        
        if success:
            await query.message.reply_photo(
                photo=qr_image,
                caption="📷 **Вход по QR-коду:**\n\n"
                       "1. Откройте Telegram → Настройки\n"
                       "2. Устройства → Подключить устройство\n"
                       "3. Отсканируйте QR-код\n"
                       "4. Подождите авторизацию...\n\n"
                       "⏳ Ожидаем ~2 минуты..."
            )
            
            # Ждем авторизацию в фоне
            asyncio.create_task(process_qr_login(user_id, query.message))
            
            return ConversationHandler.END
        else:
            await query.message.reply_text(qr_image)
            return ConversationHandler.END
    
    else:
        # Для auto и manual методов запрашиваем номер
        method_name = "автоматической отправки" if method == 'auto' else "ручного ввода"
        await query.edit_message_text(
            f"📱 **Метод {method_name}**\n\n"
            f"Отправьте номер телефона:\n"
            f"Формат: +79123456789"
        )
        return PHONE

async def process_qr_login(user_id: int, message):
    """Фоновая обработка QR-логина"""
    try:
        success, result = await manager.wait_qr_login(user_id)
        
        if success:
            await message.reply_document(
                document=result.encode('utf-8'),
                filename='telegram_session.txt',
                caption="✅ **Сессия создана через QR-код!**"
            )
            await message.reply_text(f"`{result}`", parse_mode='Markdown')
        else:
            await message.reply_text(result)
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка номера телефона"""
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    method = context.user_data.get('method', 'auto')
    
    if not phone.startswith('+'):
        await update.message.reply_text("❌ Используйте формат +79123456789")
        return PHONE
    
    processing_msg = await update.message.reply_text("🔄 Обрабатываем...")
    
    if method == 'auto':
        success, message = await manager.auto_method(phone, user_id)
    else:
        success, message = await manager.manual_method(phone, user_id)
    
    if success:
        await processing_msg.edit_text(f"✅ {message}\n\n🔢 Введите код:")
        return CODE
    else:
        await processing_msg.edit_text(f"{message}\n\nПопробуйте /start")
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кода подтверждения"""
    code = update.message.text.replace(' ', '')
    user_id = update.effective_user.id
    
    if not code.isdigit() or len(code) < 4:
        await update.message.reply_text("❌ Код должен содержать 4-5 цифр")
        return CODE
    
    processing_msg = await update.message.reply_text("🔄 Проверяем код...")
    
    success, result = await manager.verify_code(user_id, code)
    
    if success:
        await processing_msg.edit_text("✅ Код верный! Создаем сессию...")
        
        # Получаем номер для имени файла
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=result.encode('utf-8'),
            filename=f'session_{phone.replace("+", "")}.txt',
            caption="✅ **Сессия создана!**"
        )
        await update.message.reply_text(f"`{result}`", parse_mode='Markdown')
        
    elif result == "2FA_NEEDED":
        await processing_msg.edit_text("🔐 Введите пароль 2FA:")
        return PASSWORD
    else:
        await processing_msg.edit_text(f"{result}\n\nПопробуйте /start")
    
    return ConversationHandler.END

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка пароля 2FA"""
    password = update.message.text
    user_id = update.effective_user.id
    
    processing_msg = await update.message.reply_text("🔄 Проверяем пароль...")
    
    success, session_string = await manager.verify_password(user_id, password)
    
    if success:
        await processing_msg.edit_text("✅ Пароль верный! Создаем сессию...")
        
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=session_string.encode('utf-8'),
            filename=f'session_{phone.replace("+", "")}.txt',
            caption="✅ **Сессия создана!**"
        )
        await update.message.reply_text(f"`{session_string}`", parse_mode='Markdown')
    else:
        await processing_msg.edit_text(f"{session_string}\n\nПопробуйте /start")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена операции"""
    user_id = update.effective_user.id
    
    # Очищаем все сессии
    if user_id in manager.active_sessions:
        try:
            await manager.active_sessions[user_id]['client'].disconnect()
            del manager.active_sessions[user_id]
        except:
            pass
    
    if user_id in manager.qr_sessions:
        try:
            await manager.qr_sessions[user_id]['client'].disconnect()
            del manager.qr_sessions[user_id]
        except:
            pass
    
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка"""
    await update.message.reply_text(
        "🔐 **Генератор сессий Telegram**\n\n"
        "Доступные методы:\n\n"
        "• 🔐 **Авто** - бот отправляет код (может не работать)\n"
        "• 📱 **Ручной** - вы получаете код вручную в Telegram\n"
        "• 📷 **QR-код** - сканируете код из настроек Telegram\n\n"
        "💡 **Рекомендация:** Используйте ручной метод или QR-код\n\n"
        "Команды:\n"
        "/start - Начать создание сессии\n"
        "/help - Показать справку"
    )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler для всех методов
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            METHOD: [CallbackQueryHandler(handle_method)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    logger.info("🤖 Бот запущен с 3 методами авторизации!")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
