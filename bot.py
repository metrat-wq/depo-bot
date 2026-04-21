import os
import json
import logging
from datetime import datetime
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === КОНФІГУРАЦІЯ ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))

# === SYSTEM PROMPT ===
SYSTEM_PROMPT = """Ти — реалістичний радник-друг на ім'я Depo.
Твоя роль: накопичувати всю інформацію про користувача (сни, події, ідеї, залежності, патерни поведінки), підтримувати в боротьбі з залежностями (алкоголь, інші) і тверезо оцінювати його ідеї/ситуації.

Правила поведінки:
- Будь реалістом: оцінюй ідеї об'єктивно, без оптимізму/песимізму. Опирайся ТІЛЬКИ на базу даних (історія чатів, збережені нотатки, патерни). Якщо даних мало — скажи "Потрібно більше інфо про тебе для точної оцінки".
- Для залежностей: нагадуй минулі рецидиви/тригери з бази. Підтримуй фактами: "Ти вже перемагав це — ось як (з історії)". Не солодко: "Ризик високий, бо патерн повторюється".
- Оцінка ідей: аналізуй реалістично — плюси/мінуси/ризики на основі його минулих ідей/проєктів/помилок. Запитуй уточнення, якщо треба додати в базу.
- Пам'ять: завжди інтегруй контекст з бази (історія, сни, події).
- Стиль: говори просто, як друг — українською, з емпатією, але без жалю і мотиваційних кліше. Коротко, по суті. Якщо ідея погана — скажи прямо з конкретною причиною.
- Обмеження: ніколи не давай медичні поради, не суди. Якщо криза — "Звернися до фахівця".

ПОТОЧНА БАЗА ЗНАНЬ ПРО ARTEM:
{memory_context}

Поточна дата: {current_date}"""

# === GOOGLE SHEETS ===
def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_dict = json.loads(creds_json)
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID)
    return sheet

def get_memory():
    try:
        sheet = get_sheet()
        try:
            ws = sheet.worksheet("Memory")
        except:
            ws = sheet.add_worksheet("Memory", 1000, 3)
            ws.append_row(["Дата", "Категорія", "Факт"])
        records = ws.get_all_values()
        if len(records) <= 1:
            return "База поки порожня — це перший діалог."
        memory_lines = []
        for row in records[1:]:
            if len(row) >= 3 and row[2]:
                memory_lines.append(f"[{row[0]}] {row[1]}: {row[2]}")
        return "\n".join(memory_lines[-50:]) if memory_lines else "База поки порожня."
    except Exception as e:
        logger.error(f"Memory read error: {e}")
        return "Помилка читання бази."

def save_memory(category: str, fact: str):
    try:
        sheet = get_sheet()
        try:
            ws = sheet.worksheet("Memory")
        except:
            ws = sheet.add_worksheet("Memory", 1000, 3)
            ws.append_row(["Дата", "Категорія", "Факт"])
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        ws.append_row([date_str, category, fact])
        return True
    except Exception as e:
        logger.error(f"Memory save error: {e}")
        return False

def save_conversation(user_msg: str, bot_msg: str):
    try:
        sheet = get_sheet()
        try:
            ws = sheet.worksheet("History")
        except:
            ws = sheet.add_worksheet("History", 5000, 3)
            ws.append_row(["Дата", "Artem", "Depo"])
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        ws.append_row([date_str, user_msg[:500], bot_msg[:500]])
    except Exception as e:
        logger.error(f"History save error: {e}")

def get_pending_save(user_id: int):
    try:
        sheet = get_sheet()
        try:
            ws = sheet.worksheet("Pending")
        except:
            ws = sheet.add_worksheet("Pending", 100, 2)
        records = ws.get_all_values()
        for i, row in enumerate(records):
            if len(row) >= 2 and row[0] == str(user_id):
                return i + 1, row[1]
        return None, None
    except:
        return None, None

def set_pending_save(user_id: int, text: str):
    try:
        sheet = get_sheet()
        try:
            ws = sheet.worksheet("Pending")
        except:
            ws = sheet.add_worksheet("Pending", 100, 2)
        row_idx, _ = get_pending_save(user_id)
        if row_idx:
            ws.update_cell(row_idx, 2, text)
        else:
            ws.append_row([str(user_id), text])
    except Exception as e:
        logger.error(f"Pending save error: {e}")

def clear_pending_save(user_id: int):
    try:
        sheet = get_sheet()
        ws = sheet.worksheet("Pending")
        records = ws.get_all_values()
        for i, row in enumerate(records):
            if len(row) >= 1 and row[0] == str(user_id):
                ws.delete_rows(i + 1)
                return
    except:
        pass

# === GEMINI ===
def ask_gemini(user_message: str, memory: str) -> str:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")
        system = SYSTEM_PROMPT.format(
            memory_context=memory,
            current_date=datetime.now().strftime("%Y-%m-%d")
        )
        full_prompt = f"{system}\n\nArtem: {user_message}"
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "Виникла помилка. Спробуй ще раз."

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    await update.message.reply_text(
        "Привіт Artem 👋\n\nЯ — Depo, твій особистий радник.\n\n"
        "Я запам'ятовую все що ти мені розповідаєш і враховую це в кожній відповіді.\n\n"
        "Просто пиши як другу. Команди:\n"
        "/save — зберегти щось в пам'ять вручну\n"
        "/memory — показати що я про тебе знаю\n"
        "/clear_last — видалити останній запис"
    )

async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    await update.message.reply_text("⏳ Читаю базу...")
    memory = get_memory()
    if len(memory) > 4000:
        memory = memory[-4000:]
    await update.message.reply_text(f"🧠 Що я знаю про тебе:\n\n{memory}")

async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    args = context.args
    if args:
        fact = " ".join(args)
        success = save_memory("Вручну", fact)
        if success:
            await update.message.reply_text(f"✅ Збережено: {fact}")
        else:
            await update.message.reply_text("❌ Помилка збереження.")
    else:
        row_idx, pending = get_pending_save(update.effective_user.id)
        if pending:
            success = save_memory("Авто", pending)
            clear_pending_save(update.effective_user.id)
            if success:
                await update.message.reply_text(f"✅ Збережено в пам'ять!")
            else:
                await update.message.reply_text("❌ Помилка збереження.")
        else:
            await update.message.reply_text(
                "Що зберегти? Напиши:\n/save [текст]\n\nНаприклад:\n/save Боюся змін в роботі"
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return

    user_text = update.message.text
    await update.message.reply_text("⏳")

    memory = get_memory()
    response = ask_gemini(user_text, memory)

    # Зберегти діалог в історію
    save_conversation(user_text, response)

    # Авто-витяг важливої інфо для пропозиції зберегти
    keywords = ["залежність", "рецидив", "випив", "зірвався", "ідея", "план", "сон", "проблема", "тригер", "патерн"]
    if any(k in user_text.lower() for k in keywords):
        set_pending_save(update.effective_user.id, user_text)

    await update.message.reply_text(response)

# === MAIN ===
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", show_memory))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
