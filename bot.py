import os
import logging
import base64
import json
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
EBAY_USER_TOKEN = os.environ.get("EBAY_USER_TOKEN")
EBAY_OAUTH_TOKEN = os.environ.get("EBAY_OAUTH_TOKEN")

user_sessions = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "გამარჯობა! მე ვარ eBay ავტონაწილების აგენტი.\n\n"
        "გამომიგზავნე:\n"
        "- Part Number (მაგ: 37230-BZ040)\n"
        "- ან ფოტო ნაწილისა\n"
        "- ან ორივე ერთად\n\n"
        "მე ვიპოვი ინფოს და eBay Draft-ში შევინახავ!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    if user_id not in user_sessions:
        user_sessions[user_id] = {"part_number": None, "photos": []}
    if text:
        user_sessions[user_id]["part_number"] = text.strip()
    await update.message.reply_text("Part number მიღებულია! გამომიგზავნე ფოტო ან დაწერე /process")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"part_number": None, "photos": []}
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    user_sessions[user_id]["photos"].append(photo_b64)
    caption = update.message.caption
    if caption:
        user_sessions[user_id]["part_number"] = caption.strip()
    photo_count = len(user_sessions[user_id]["photos"])
    part = user_sessions[user_id]["part_number"] or "არ არის"
    keyboard = [[InlineKeyboardButton("დამუშავება", callback_data="process")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "ფოტო " + str(photo_count) + " მიღებულია!\nPart Number: " + part + "\n\nდააჭირე დამუშავებას:",
        reply_markup=reply_markup
    )


async def process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.message.reply_text("AI მუშაობს...")
    await process_listing(user_id, query.message)


async def process_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("AI მუშაობს...")
    await process_listing(user_id, update.message)


async def process_listing(user_id, message):
    session = user_sessions.get(user_id, {})
    part_number = session.get("part_number", "")
    photos = session.get("photos", [])
    if not part_number and not photos:
        await message.reply_text("გამომიგზავნე Part Number ან ფოტო პირველ რიგში!")
        return
    try:
        listing = await call_claude(part_number, photos)
        draft_id = await create_ebay_draft(listing)
        user_sessions[user_id] = {"part_number": None, "photos": []}
        compat_list = listing.get("compatibility", [])[:5]
        compat_text = "\n".join("- " + c for c in compat_list)
        result = (
            "განცხადება მზადაა!\n\n" +
            listing.get("title", "") + "\n" +
            "ფასი: $" + str(listing.get("suggested_price", "N/A")) + "\n" +
            "მდგომარეობა: " + listing.get("condition", "Used") + "\n\n" +
            "თავსებადობა:\n" + compat_text + "\n\n" +
            "Draft ID: " + str(draft_id) + "\n" +
            "eBay Seller Hub-ში გააქტიურე!"
        )
        await message.reply_text(result)
    except Exception as e:
        logger.error("Error: " + str(e))
        await message.reply_text("შეცდომა: " + str(e) + "\n\nსცადე /start")


async def call_claude(part_number, photos):
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    content = []
    for photo_b64 in photos[:4]:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": photo_b64
            }
        })
    prompt = (
        "You are an expert eBay auto parts specialist. Analyze this auto part.\n"
        "Part Number: " + (part_number or "See image") + "\n\n"
        "Provide complete eBay listing details.\n"
        "Respond ONLY with valid JSON, no markdown, no extra text:\n"
        "{\n"
        '  "title": "eBay title max 80 chars SEO optimized",\n'
        '  "description": "Detailed description 3-4 paragraphs",\n'
        '  "category_id": "6030",\n'
        '  "condition": "Used",\n'
        '  "suggested_price": 25,\n'
        '  "compatibility": ["2010 Toyota Camry", "2011 Toyota Camry"],\n'
        '  "oem_number": "OEM number if known",\n'
        '  "brand": "brand name",\n'
        '  "condition_description": "condition notes"\n'
        "}"
    )
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": content}]
    }
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=payload,
        timeout=60
    )
    data = response.json()
    logger.info("Claude response: " + json.dumps(data)[:500])
    if "error" in data:
        raise ValueError("Claude API error: " + str(data["error"]))
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    if not text.strip():
        raise ValueError("Empty Claude response: " + json.dumps(data)[:300])
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


async def create_ebay_draft(listing):
    if not EBAY_OAUTH_TOKEN:
        return "NO_OAUTH_TOKEN"
    headers = {
        "Authorization": "Bearer " + EBAY_OAUTH_TOKEN,
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"
    }
    title = listing.get("title", "Auto Part")[:80]
    description = listing.get("description", "")
    price = listing.get("suggested_price", 25)
    condition = listing.get("condition", "Used")
    condition_id = "3000" if condition == "Used" else "1000"
    body = {
        "product": {
            "title": title,
            "description": description,
            "aspects": {}
        },
        "condition": condition_id,
        "categoryId": listing.get("category_id", "6030"),
        "format": "FIXED_PRICE",
        "listingPolicies": {},
        "pricingSummary": {
            "price": {
                "value": str(price),
                "currency": "USD"
            }
        },
        "quantityLimitPerBuyer": 1
    }
    try:
        response = requests.post(
            "https://api.ebay.com/sell/inventory/v1/offer",
            headers=headers,
            json=body,
            timeout=30
        )
        logger.info("eBay response: " + response.text[:300])
        result = response.json()
        offer_id = result.get("offerId", "")
        if offer_id:
            return "OFFER_" + offer_id
        return "DRAFT_SAVED: " + response.text[:100]
    except Exception as e:
        logger.error("eBay error: " + str(e))
        return "DRAFT_LOCAL"


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("process", process_command))
    app.add_handler(CallbackQueryHandler(process_callback, pattern="process"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
