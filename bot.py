import asyncio
import logging
import re
from dataclasses import dataclass
from enum import IntEnum, auto

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import ADMIN_IDS, ADMIN_LOG_CHAT_ID, BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

router = Router()

# Аккаунт-получатель переданной NFT (только для уведомления, передача вручную)
TARGET_ACCOUNT = 8090009094

# Имя, под которым нужно "передать" (слаг). Редактируется через /target или /set_target
TARGET_LABEL = "8090009094"


@dataclass
class OfferDraft:
    recipient_id: int
    recipient_name: str
    link: str = ""
    slug: str = ""
    amount: str = ""

    @property
    def display(self) -> str:
        pieces = [p for p in (self.link, self.slug) if p]
        return "\n".join(pieces) if pieces else "(без ссылки)"


class OfferFlow(StatesGroup):
    waiting_recipient = State()
    waiting_gift = State()
    waiting_amount = State()


class Decision(IntEnum):
    ACCEPTED = auto()
    REJECTED = auto()


SLAG_RE = r"t\.me/nft/([A-Za-z0-9_\-]+)"


def cb(decision: Decision, offer_id: int) -> str:
    return f"{int(decision)}:{offer_id}"


def parse_cb(data: str) -> tuple[Decision, int]:
    d, oid = data.split(":", 1)
    return Decision(int(d)), int(oid)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def offer_kb(offer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardBuilder().button(
        text="✅ Принять", callback_data=cb(Decision.ACCEPTED, offer_id)
    ).button(
        text="❌ Отклонить", callback_data=cb(Decision.REJECTED, offer_id)
    ).as_markup()


def recipients_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for uid, name in db.get_recipients():
        b.button(text=name, callback_data=f"recip:{uid}")
    b.adjust(1)
    return b.as_markup()


async def notify_admins(bot: Bot, text: str):
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(uid, text)
        except Exception as exc:
            logger.warning("не смог уведомить админа %s: %s", uid, exc)
    if ADMIN_LOG_CHAT_ID and ADMIN_LOG_CHAT_ID not in ADMIN_IDS:
        try:
            await bot.send_message(ADMIN_LOG_CHAT_ID, text)
        except Exception as exc:
            logger.warning("логочат недоступен: %s", exc)


@router.message(Command("start"))
async def cmd_start(message: Message):
    if is_admin(message.from_user.id):
        return await message.answer(
            "Привет, босс! Команды:\n"
            "/offer — создать оффер\n"
            "/recipients — список получателей\n"
            "/add_recipient <имя> — привязать текущий чат как получателя\n"
            "/target — показать аккаунт передачи\n"
            "/set_target <ID> — сменить аккаунт передачи\n"
            "/admin_chat — это меню админа"
        )
    # Получатель регистрируется боту
    db.ensure_user(message.from_user.id, message.from_user.full_name or message.from_user.username or str(message.from_user.id))
    await message.answer(
        "Ты в базе как получатель. Жди оффера — он придёт сюда, отвечай кнопками."
    )


@router.message(Command("recipients"))
async def cmd_recipients(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = db.get_recipients()
    if not rows:
        return await message.answer("Получателей пока нет. Начни с /start у получателя либо /add_recipient.")
    text = "\n".join(f"{name}: <code>{uid}</code>" for uid, name in rows)
    await message.answer("Получатели:\n" + text)


@router.message(Command("add_recipient"))
async def cmd_add_recipient(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    name = (command.args or "").strip()
    if not name:
        return await message.answer("Формат: /add_recipient <имя>")
    db.ensure_user(message.from_user.id, name)
    await message.answer(f"Текущий чат зарегистрирован как получатель «{name}».")


@router.message(Command("target"))
async def cmd_target(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(f"Аккаунт передачи: <code>{TARGET_ACCOUNT}</code> ({TARGET_LABEL}).")
    await message.answer("Сам трансфер бот не выполняет — NFT принимает владелец аккаунта вручную, /set_target меняет только метку.")


@router.message(Command("set_target"))
async def cmd_set_target(message: Message, command: CommandObject):
    global TARGET_ACCOUNT, TARGET_LABEL
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip()
    parts = args.split()
    if not parts:
        return await message.answer("Формат: /set_target <ID или @username> [метку]")
    TARGET_ACCOUNT = parts[0]
    TARGET_LABEL = " ".join(parts[1:]) or parts[0]
    await message.answer(f"Аккаунт передачи: {TARGET_ACCOUNT} ({TARGET_LABEL}).")


@router.message(Command("offer"))
async def cmd_offer(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = db.get_recipients()
    if not rows:
        return await message.answer("Сначала нужен получатель: пусть он нажмёт /start боту.")
    await state.set_state(OfferFlow.waiting_recipient)
    await message.answer("Выбери получателя:", reply_markup=recipients_kb())


@router.callback_query(F.data.startswith("recip:"))
async def on_recipient(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    uid = int(callback.data.split(":", 1)[1])
    name = dict(db.get_recipients()).get(uid, str(uid))
    await state.update_data(draft=OfferDraft(recipient_id=uid, recipient_name=name))
    await state.set_state(OfferFlow.waiting_gift)
    await callback.message.answer(
        f"Получатель: {name}. Теперь пришли подарок — ссылку t.me/nft/..., слаг или название текстом:"
    )
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(OfferFlow.waiting_gift)
async def on_gift(message: Message, state: FSMContext):
    text = (message.text or message.caption or "").strip()
    if not text:
        return await message.answer("Пришли ссылку, слаг или название.")
    data = await state.get_data()
    draft: OfferDraft = data["draft"]
    m = re.search(SLAG_RE, text)
    draft.slug = m.group(1) if m else text
    draft.link = text
    await state.update_data(draft=draft)
    await state.set_state(OfferFlow.waiting_amount)
    await message.answer("Шаг 3/4 — сумма (число):")


@router.message(OfferFlow.waiting_amount)
async def on_amount(message: Message, state: FSMContext, bot: Bot):
    amount = (message.text or "").strip()
    if not amount:
        return await message.answer("Введи сумму числом.")
    data = await state.get_data()
    draft: OfferDraft = data["draft"]
    draft.amount = amount

    offer_id = db.add_offer(
        recipient_id=draft.recipient_id,
        link=draft.link,
        slug=draft.slug,
        amount=draft.amount,
    )

    # Отправляем оффер выбранному получателю
    sent = False
    try:
        await bot.send_message(
            draft.recipient_id,
            (
                f"📦 Оффер NFT\n\n{draft.display}\n"
                f"💰 Сумма: {draft.amount}\n\n"
                f"Принять или отклонить?"
            ),
            reply_markup=offer_kb(offer_id),
        )
        sent = True
    except Exception as exc:
        logger.error("оффер %s не доставлен: %s", draft.recipient_id, exc)

    await state.clear()
    status = "доставлен ✅" if sent else "НЕ доставлен ❌ (получатель не начал диалог с ботом?)"
    await message.answer(
        f"Оффер #{offer_id} {status} → {draft.recipient_name}.\n"
        f"Сумма: {draft.amount}. Ждём решение."
    )

    await notify_admins(
        bot,
        f"🧾 Новый оффер #{offer_id} для {draft.recipient_name}:\n"
        f"{draft.display}\nСумма: {draft.amount}",
    )


@router.callback_query(F.data.regexp(r"^[12]:\d+$"))
async def on_decision(callback: CallbackQuery, bot: Bot, state: FSMContext):
    decision, offer_id = parse_cb(callback.data)
    user = callback.from_user
    db.record_resolution(offer_id, user.id, user.username, decision.name)

    await callback.message.edit_text(
        "✅ Вы приняли оффер" if decision == Decision.ACCEPTED else "❌ Вы отклонили оффер",
        reply_markup=None,
    )
    await callback.answer(
        "Спасибо за ответ!"
        if decision == Decision.ACCEPTED
        else "Оффер отклонён.",
        show_alert=False,
    )

    verdict = "ПРИНЯЛ ✅" if decision == Decision.ACCEPTED else "ОТКЛОНИЛ ❌"
    suffix = (
        f"\n➡️ Передай NFT аккаунту {TARGET_ACCOUNT} ({TARGET_LABEL}) вручную."
        if decision == Decision.ACCEPTED
        else ""
    )
    await notify_admins(
        bot,
        f"🗳 {user.username or user.id} {verdict} оффер #{offer_id}.\n"
        f"Решение: {decision.name}"
        + suffix,
    )


async def main():
    db.init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())