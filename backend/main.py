import asyncio
import logging
import uvicorn

from contextlib import asynccontextmanager
from typing import Callable, Dict, Any, Awaitable
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from backend.config import settings
from backend.database import init_db, get_user, upsert_user, update_session
from backend.services import mirea_service
from backend.services.session_cache import session_cache
from backend.routes.auth import router as auth_router
from backend.routes.friends import router as friends_router
from backend.routes.attendance import router as attendance_router

logger = logging.getLogger(__name__)

# Aiogram bot + dispatcher
bot = Bot(token=settings.bot_token) if settings.bot_token else None
dp = Dispatcher()

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
            
        db_user = await get_user(user.id)
        is_authorized = bool(db_user and db_user["encrypted_session"])
        
        if is_authorized:
            return await handler(event, data)
            
        state: FSMContext = data.get("state")
        if state:
            current_state = await state.get_state()
            if current_state in (
                AuthState.waiting_for_login.state,
                AuthState.waiting_for_password.state,
                AuthState.waiting_for_2fa.state
            ):
                return await handler(event, data)
                
        if isinstance(event, types.Message):
            if event.text == "/start":
                return await handler(event, data)
                
            await event.answer(
                "Для использования бота необходимо авторизоваться через аккаунт Пульс МИРЭА.\n\nВведите ваш логин (почту) от портала СДО МИРЭА:"
            )
        elif isinstance(event, types.CallbackQuery):
            await event.message.answer(
                "Для использования бота необходимо авторизоваться через аккаунт Пульс МИРЭА.\n\nВведите ваш логин (почту) от портала СДО МИРЭА:"
            )
            await event.answer()
            
        if state:
            await state.set_state(AuthState.waiting_for_login)
        return

dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())


class AuthState(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()
    waiting_for_2fa = State()


@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    """Приветствие. Если не авторизован - запрашивает логин."""
    user = await get_user(message.from_user.id)
    is_authorized = bool(user and user["encrypted_session"])

    if is_authorized:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Открыть InSync",
                web_app=WebAppInfo(url=settings.webapp_url),
            )]
        ])
        name = user["full_name"] or message.from_user.first_name
        await message.answer(
            f"С возвращением, {name}!\n\nInSync - Для студентов МИРЭА\n\n• Отметка по QR-коду\n• Отметка за себя и друзей\n• Без очередей и зависаний",
            reply_markup=kb,
        )
    else:
        await message.answer(
            "InSync — Для студентов МИРЭА\n\n"
            "• Отметка по QR-коду\n"
            "• Отметка за себя и друзей\n"
            "• Без очередей и зависаний"
        )
        await state.set_state(AuthState.waiting_for_login)

@dp.message(AuthState.waiting_for_login)
async def process_login(message: types.Message, state: FSMContext):
    await state.update_data(login=message.text)
    await message.answer(
        "Отлично. Теперь введите ваш пароль от портала СДО МИРЭА (бот сразу удалит сообщение с паролем в целях безопасности, если у него есть права):"
    )
    await state.set_state(AuthState.waiting_for_password)

@dp.message(AuthState.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    login = data['login']
    password = message.text

    try:
        await message.delete()
    except Exception:
        pass

    msg = await message.answer("🔄 Выполняю вход...")
    
    result = await mirea_service.login(login, password, owner_id=message.from_user.id)
    
    if not result.get("success") and not result.get("needs_2fa"):
        await msg.edit_text(f"❌ Ошибка авторизации: {result.get('error', 'Неверный логин или пароль')}\nПопробуйте снова с помощью команды /start")
        await state.clear()
        return

    if result.get("needs_2fa"):
        await state.update_data(challenge_id=result["challenge_id"], login=login)
        await msg.edit_text(f"📧 {result.get('hint') or 'Код подтверждения отправлен'}. Введите его:")
        await state.set_state(AuthState.waiting_for_2fa)
        return

    tokens = result["tokens"]
    encrypted = mirea_service.encrypt_session(tokens)

    await upsert_user(
        telegram_id=message.from_user.id,
        display_name=message.from_user.username or message.from_user.first_name,
        mirea_login=login,
        group_name=result.get("group_name"),
        full_name=result.get("full_name"),
        username=message.from_user.username,
    )
    await update_session(message.from_user.id, encrypted)
    await session_cache.set_decrypted(message.from_user.id, tokens)
    
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Открыть InSync",
            web_app=WebAppInfo(url=settings.webapp_url),
        )]
    ])
    
    await msg.edit_text(
        f"✔ Авторизация успешна!\n\nС возвращением, {result.get('full_name') or message.from_user.first_name}!\nТеперь вы можете полноценно пользоваться InSync.",
        reply_markup=kb
    )

@dp.message(AuthState.waiting_for_2fa)
async def process_2fa(message: types.Message, state: FSMContext):
    data = await state.get_data()
    challenge_id = data['challenge_id']
    code = (message.text or "").strip()

    msg = await message.answer("🔄 Проверяю код...")
    
    result = await mirea_service.complete_2fa(challenge_id, code, owner_id=message.from_user.id)
    
    if not result.get("success"):
        err = result.get('error', 'Неверный код')
        if "истекла" in err:
            await msg.edit_text(f"❌ {err}\nНачните заново: /start")
            await state.clear()
        else:
            await msg.edit_text(f"❌ {err}\nВведите код ещё раз или /start чтобы начать заново.")
        return

    tokens = result["tokens"]
    encrypted = mirea_service.encrypt_session(tokens)

    await upsert_user(
        telegram_id=message.from_user.id,
        display_name=message.from_user.username or message.from_user.first_name,
        mirea_login=data.get("login"),
        group_name=result.get("group_name"),
        full_name=result.get("full_name"),
        username=message.from_user.username,
    )
    await update_session(message.from_user.id, encrypted)
    await session_cache.set_decrypted(message.from_user.id, tokens)
    
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Открыть InSync",
            web_app=WebAppInfo(url=settings.webapp_url),
        )]
    ])
    
    await msg.edit_text(
        f"✔ Авторизация успешна!\n\nС возвращением, {result.get('full_name') or message.from_user.first_name}!\nТеперь вы можете полноценно пользоваться InSync.",
        reply_markup=kb
    )



def _setup_logging() -> None:
    """Логи только в консоль, начиная с уровня WARNING."""
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    # не плодим обработчики при reload
    if any(getattr(h, "_insync", False) for h in root.handlers):
        return
    console = logging.StreamHandler()
    console.setFormatter(fmt); console._insync = True
    root.addHandler(console)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: инициализация БД при старте."""
    _setup_logging()
    logger.info("Запуск InSync backend...")

    # Инициализация
    await init_db()
    logger.info("Database initialized")

    # Запуск бота в фоне (polling)
    bot_task = None
    if bot:
        bot_task = asyncio.create_task(dp.start_polling(bot))
        logger.info("Telegram bot polling started")

    yield

    # Shutdown
    if bot_task:
        bot_task.cancel()
    if bot:
        await bot.session.close()
    logger.info("InSync backend stopped")


app = FastAPI(
    title="InSync API",
    description="Telegram Mini App для автоотметки студентов МИРЭА",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.webapp_url] if settings.webapp_url else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(friends_router)
app.include_router(attendance_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "InSync"}


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

