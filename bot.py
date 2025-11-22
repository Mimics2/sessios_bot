import os
import logging
import asyncio
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InputFile, BufferedInputFile
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError,
    PhoneCodeExpiredError
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфиг
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

# Состояния FSM
class SessionStates(StatesGroup):
    METHOD = State()
    PHONE = State()
    CODE = State()
    PASSWORD = State()

# Инициализация Aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.qr_sessions = {}
        self.session_timeouts = {}
    
    async def cleanup_old_sessions(self, user_id: int = None):
        """Очистка устаревших сессий"""
        now = datetime.now()
        expired_sessions = []
        
        for uid, timeout in self.session_timeouts.items():
            if now > timeout:
                expired_sessions.append(uid)
        
        for uid in expired_sessions:
            if uid in self.active_sessions:
                try:
                    await self.active_sessions[uid]['client'].disconnect()
                    del self.active_sessions[uid]
                except:
                    pass
            if uid in self.qr_sessions:
                try:
                    await self.qr_sessions[uid]['client'].disconnect()
                    del self.qr_sessions[uid]
                except:
                    pass
            if uid in self.session_timeouts:
                del self.session_timeouts[uid]
            logger.info(f"🧹 Очищена устаревшая сессия для {uid}")
    
    async def create_fresh_session(self, phone: str, user_id: int, method: str):
        """Создание новой сессии"""
        try:
            # Очищаем старую сессию
            await self.cleanup_old_sessions(user_id)
            
            # Создаем нового клиента
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            # Отправляем запрос кода
            sent_code = await client.send_code_request(phone)
            
            # Сохраняем сессию
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': method
            }
            
            # Таймаут 5 минут
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=5)
            
            return True, "✅ Код отправлен! У вас есть 5 минут чтобы ввести код."
            
        except FloodWaitError as e:
            return False, f"❌ Слишком много запросов. Подождите {e.seconds} секунд"
        except PhoneNumberInvalidError:
            return False, "❌ Неверный номер телефона"
        except Exception as e:
            logger.error(f"❌ Ошибка создания сессии: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    async def qr_method(self, user_id: int):
        """Метод с QR-кодом"""
        try:
            await self.cleanup_old_sessions(user_id)
            
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            qr_login = await client.qr_login()
            
            self.qr_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login
            }
            
            # Таймаут 3 минуты для QR
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=3)
            
            # Создаем QR-код
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
    
    async def verify_code(self, user_id: int, code: str):
        """Проверка кода"""
        await self.cleanup_old_sessions(user_id)
        
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена. Начните с /start"
        
        data = self.active_sessions[user_id]
        
        # Проверяем таймаут
        if datetime.now() > self.session_timeouts[user_id]:
            return False, "❌ Время сессии истекло. Начните с /start"
        
        try:
            clean_code = code.replace(' ', '').replace('-', '').strip()
            
            await data['client'].sign_in(
                phone=data['phone'],
                code=clean_code,
                phone_code_hash=data['phone_code_hash']
            )
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            # Очищаем сессии
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
            
        except PhoneCodeExpiredError:
            return False, "❌ Код устарел. Запросите новый код с /start"
        except SessionPasswordNeededError:
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            return False, "❌ Неверный код подтверждения"
        except FloodWaitError as e:
            return False, f"❌ Слишком много попыток. Подождите {e.seconds} секунд"
        except Exception as e:
            logger.error(f"❌ Ошибка входа: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
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
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, f"❌ Неверный пароль 2FA"
    
    async def wait_qr_login(self, user_id: int):
        """Ожидание QR-авторизации"""
        if user_id not in self.qr_sessions:
            return False, "❌ QR сессия не найдена"
        
        data = self.qr_sessions[user_id]
        
        try:
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
            
        except asyncio.TimeoutError:
            await data['client'].disconnect()
            if user_id in self.qr_sessions:
                del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, "❌ Время ожидания истекло"
        except Exception as e:
            await data['client'].disconnect()
            if user_id in self.qr_sessions:
                del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, f"❌ Ошибка: {str(e)}"

manager = SessionManager()

# 🔥 КОМАНДА START
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await manager.cleanup_old_sessions(message.from_user.id)
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 Автоматическая отправка", callback_data="method_auto")
    builder.button(text="📱 Ручной ввод кода", callback_data="method_manual") 
    builder.button(text="📷 QR-код (рекомендуется)", callback_data="method_qr")
    builder.adjust(1)
    
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "💡 **Рекомендация:** Используйте QR-код - он самый надежный!\n\n"
        "Выберите способ авторизации:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SessionStates.METHOD)

# 🔥 ВЫБОР МЕТОДА
@router.callback_query(F.data.startswith("method_"), SessionStates.METHOD)
async def handle_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.replace("method_", "")
    user_id = callback.from_user.id
    
    await callback.answer()
    
    if method == "qr":
        await callback.message.edit_text("🔄 Генерируем QR-код...")
        
        success, qr_image, qr_login = await manager.qr_method(user_id)
        
        if success:
            # Конвертируем BytesIO в BufferedInputFile
            qr_bytes = qr_image.getvalue()
            input_file = BufferedInputFile(qr_bytes, filename="qr_code.png")
            
            await callback.message.answer_photo(
                photo=input_file,
                caption="📷 **Вход по QR-коду:**\n\n"
                       "1. Откройте Telegram → Настройки\n"
                       "2. Устройства → Подключить устройство\n" 
                       "3. Отсканируйте QR-код\n"
                       "4. Подождите подтверждения...\n\n"
                       "⏳ Действует 3 минуты"
            )
            
            # Фоновая обработка QR
            asyncio.create_task(process_qr_login(user_id, callback.message))
            await state.clear()
        else:
            await callback.message.edit_text(qr_image)
            await state.clear()
    
    else:
        method_name = "автоматической отправки" if method == "auto" else "ручного ввода"
        await callback.message.edit_text(
            f"📱 **Метод {method_name}**\n\n"
            f"Отправьте номер телефона:\n"
            f"Формат: +79123456789\n\n"
            f"⏰ Код действителен 5 минут"
        )
        await state.update_data(method=method)
        await state.set_state(SessionStates.PHONE)

# 🔥 ОБРАБОТКА НОМЕРА ТЕЛЕФОНА
@router.message(SessionStates.PHONE)
async def handle_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    data = await state.get_data()
    method = data.get('method', 'auto')
    
    if not phone.startswith('+'):
        await message.answer("❌ Используйте формат +79123456789")
        return
    
    await message.answer("🔄 Создаем сессию...")
    
    success, result = await manager.create_fresh_session(phone, user_id, method)
    
    if success:
        await message.answer(
            f"✅ {result}\n\n"
            f"🔢 **Введите код:**\n"
            f"• Только цифры (5 цифр)\n" 
            f"• Без пробелов и дефисов\n"
            f"• Пример: 12345"
        )
        await state.update_data(phone=phone)
        await state.set_state(SessionStates.CODE)
    else:
        await message.answer(
            f"{result}\n\n"
            f"💡 Попробуйте QR-код: /start"
        )
        await state.clear()

# 🔥 ОБРАБОТКА КОДА
@router.message(SessionStates.CODE)
async def handle_code(message: Message, state: FSMContext):
    code = message.text
    user_id = message.from_user.id
    
    await message.answer("🔄 Проверяем код...")
    
    success, result = await manager.verify_code(user_id, code)
    
    if success:
        data = await state.get_data()
        phone = data.get('phone', 'unknown')
        
        # Создаем файл сессии
        session_bytes = result.encode('utf-8')
        session_file = BufferedInputFile(session_bytes, filename=f"session_{phone.replace('+', '')}.txt")
        
        await message.answer_document(
            document=session_file,
            caption="✅ **Сессия создана!**"
        )
        await message.answer(f"`{result}`")
        
    elif result == "2FA_NEEDED":
        await message.answer("🔐 Введите пароль двухфакторной аутентификации:")
        await state.set_state(SessionStates.PASSWORD)
    else:
        await message.answer(
            f"{result}\n\n"
            f"💡 **Советы:**\n"
            f"• Используйте QR-код (/start)\n"
            f"• Вводите код быстро\n"
            f"• Проверьте номер"
        )
        await state.clear()
    
    if success:
        await state.clear()

# 🔥 ОБРАБОТКА ПАРОЛЯ 2FA
@router.message(SessionStates.PASSWORD)
async def handle_password(message: Message, state: FSMContext):
    password = message.text
    user_id = message.from_user.id
    
    await message.answer("🔄 Проверяем пароль...")
    
    success, session_string = await manager.verify_password(user_id, password)
    
    if success:
        data = await state.get_data()
        phone = data.get('phone', 'unknown')
        
        session_bytes = session_string.encode('utf-8')
        session_file = BufferedInputFile(session_bytes, filename=f"session_{phone.replace('+', '')}.txt")
        
        await message.answer_document(
            document=session_file,
            caption="✅ **Сессия создана!**"
        )
        await message.answer(f"`{session_string}`")
    else:
        await message.answer(f"{session_string}\n\nПопробуйте /start")
    
    await state.clear()

# 🔥 ОБРАБОТКА QR-ЛОГИНА
async def process_qr_login(user_id: int, message: Message):
    """Фоновая обработка QR-авторизации"""
    try:
        success, result = await manager.wait_qr_login(user_id)
        
        if success:
            session_bytes = result.encode('utf-8')
            session_file = BufferedInputFile(session_bytes, filename="telegram_session.txt")
            
            await message.answer_document(
                document=session_file,
                caption="✅ **Сессия создана через QR-код!**"
            )
            await message.answer(f"`{result}`")
        else:
            await message.answer(f"{result}\n\nПопробуйте снова: /start")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# 🔥 КОМАНДА HELP
@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "Доступные методы:\n\n"
        "• 🔐 **Авто** - бот отправляет код\n"
        "• 📱 **Ручной** - вы получаете код вручную\n"
        "• 📷 **QR-код** - сканируете код (рекомендуется)\n\n"
        "💡 **Рекомендация:** Используйте QR-код\n\n"
        "Команды:\n"
        "/start - Начать создание сессии\n"
        "/help - Показать справку"
    )

# 🔥 КОМАНДА CANCEL
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await manager.cleanup_old_sessions(user_id)
    await state.clear()
    await message.answer("❌ Операция отменена")

# 🔥 ЗАПУСК БОТА
async def main():
    logger.info("🤖 Бот Aiogram + Telethon запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
