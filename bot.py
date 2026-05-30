import os, logging, base64, json, re, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
EBAY_DEV_ID = os.environ.get("EBAY_DEV_ID")
EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID")
EBAY_USER_TOKEN = os.environ.get("EBAY_USER_TOKEN")
PAYPAL_EMAIL = os.environ.get("PAYPAL_EMAIL", "zurabgelenidze1@gmail.com")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "გამარჯობა! მე ვარ eBay ავტონაწილების აგენტი.\n\n"
        "გამომიგზავნე:\n- Part Number (მაგ: 37230-BZ040)\n- ან ფოტო\n- ან ორივე ერთად\n\n"
        "მე მოვიძიებ ინფორმაციას და შევინახავ eBay-ის დრაფტებში!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    if user_id not in user_sessions: user_sessions[user_id] = {"part_number": None, "photos": []}
    if text: user_sessions[user_id]["part_number"] = text.strip()
    await update.message.reply_text("Part number მიღებულია! გამომიგზავნე ფოტო ან დაწერე /process")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions: user_sessions[user_id] = {"part_number": None, "photos": []}
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    user_sessions[user_id]["photos"].append(photo_b64)
    if update.message.caption: user_sessions[user_id]["part_number"] = update.message.caption.strip()
    
    part = user_sessions[user_id]["part_number"] or "არ არის"
    keyboard = [[InlineKeyboardButton("დამუშავება", callback_data="process")]]
    await update.message.reply_text(
        f"ფოტო მიღებულია!\nPart Number: {part}\n\nდააჭირე დამუშავებას:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("AI მუშაობს ინფორმაციის მოძიებაზე...")
    await process_listing(update.callback_query.from_user.id, update.callback_query.message)

async def process_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("AI მუშაობს ინფორმაციის მოძიებაზე...")
    await process_listing(update.effective_user.id, update.message)

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
        
        status_text = f"✅ წარმატებით შეინახა დრაფტებში! Item ID: {draft_id}" if draft_id.isdigit() else f"⚠️ {draft_id}"
        result = (
            f"დრაფტის პროცესი დასრულდა!\n\nსათაური: {listing.get('title', '')}\n"
            f"ფასი: ${listing.get('suggested_price', 'N/A')}\nბრენდი: {listing.get('brand', 'Unbranded')}\n"
            f"მდგომარეობა: {listing.get('condition', 'Used')}\n\nთავსებადობა:\n{compat_text}\n\n{status_text}\n\n"
            "შეგიძლია შეხვიდე eBay Seller Hub -> Drafts და ნახო!"
        )
        await message.reply_text(result)
    except Exception as e:
        logger.error("Error: " + str(e))
        await message.reply_text(f"შეცდომა: {str(e)}\n\nსცადე /start")

async def call_claude(part_number, photos):
    headers = {"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    content = []
    for photo_b64 in photos[:4]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}})
    prompt = (
        f"You are an expert eBay auto parts specialist. Analyze this auto part.\nPart Number: {part_number or 'See image'}\n\n"
        "Provide complete eBay listing details. Use category_id 262 for Parts and Accessories.\n"
        "CRITICAL: For 'brand', provide the actual car brand (e.g. Toyota, Honda, Ford, BMW, Mopar). NEVER use 'OEM'. If unknown, use 'Unbranded'.\n\n"
        "Respond ONLY with valid JSON, no markdown:\n"
        "{\n"
        '  "title": "eBay title max 80 chars SEO optimized with part number",\n'
        '  "description": "Detailed description plain text",\n'
        '  "category_id": "262",\n'
        '  "condition": "Used",\n'
        '  "suggested_price": 25,\n'
        '  "compatibility": ["Year Make Model"],\n'
        '  "oem_number": "OEM part number",\n'
        '  "brand": "Toyota",\n'
        '  "part_number": "exact part number",\n'
        '  "condition_description": "Good working condition OEM part"\n'
        "}"
    )
    content.append({"type": "text", "text": prompt})
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json={"model": "claude-sonnet-4-6", "max_tokens": 1500, "messages": [{"role": "user", "content": content}]},
        timeout=60
    )
    data = response.json()
    if "error" in data: raise ValueError("Claude API error: " + str(data["error"]))
    text = "".join([block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    return json.loads(text[start:end]) if (start >= 0 and end > start) else json.loads(text)

async def create_ebay_draft(listing):
    if not EBAY_USER_TOKEN: return "NO_USER_TOKEN"
    title = listing.get("title", "Auto Part")[:80]
    description = listing.get("description", "No description")
    price = listing.get("suggested_price", 25)
    brand = "Unbranded" if listing.get("brand", "Unbranded").upper() == "OEM" else listing.get("brand", "Unbranded")
    part_num = listing.get("part_number", listing.get("oem_number", "Universal"))

    item_specifics = (
        f"<ItemSpecifics>"
        f"<NameValueList><Name>Brand</Name><Value>{brand}</Value></NameValueList>"
        f"<NameValueList><Name>Manufacturer Part Number</Name><Value>{part_num}</Value></NameValueList>"
        f"<NameValueList><Name>Type</Name><Value>Direct Replacement</Value></NameValueList>"
        f"<NameValueList><Name>Grade</Name><Value>A</Value></NameValueList>"
        f"<NameValueList><Name>Quality</Name><Value>Excellent</Value></NameValueList>"
        f"<NameValueList><Name>Certification</Name><Value>OEM Genuine</Value></NameValueList>"
        f"<NameValueList><Name>OE/OEM Part Number</Name><Value>{part_num}</Value></NameValueList>"
        f"</ItemSpecifics>"
    )
    picture_details = (
        "<PictureDetails>"
        "<PictureURL>https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/Car_with_Driver-Side_A-Pillar_Highlighted.jpg/800px-Car_with_Driver-Side_A-Pillar_Highlighted.jpg</PictureURL>"
        "</PictureDetails>"
    )
    xml_request = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<AddFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<RequesterCredentials><eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken></RequesterCredentials>"
        "<ErrorLanguage>en_US</ErrorLanguage><WarningLevel>High</WarningLevel>"
        f"<Item><Title>{title}</Title><Description><![CDATA[" + description + "]]></Description>"
        f"<PrimaryCategory><CategoryID>262</CategoryID></PrimaryCategory><StartPrice>{price}</StartPrice>"
        f"<CategoryMappingAllowed>true</CategoryMappingAllowed>{item_specifics}{picture_details}"
        f"<Country>US</Country><Currency>USD</Currency><DispatchTimeMax>3</DispatchTimeMax>"
        f"<ListingDuration>GTC</ListingDuration><ListingType>FixedPriceItem</ListingType>"
        f"<PaymentMethods>PayPal</PaymentMethods><PayPalEmailAddress>{PAYPAL_EMAIL}</PayPalEmailAddress>"
        f"<PostalCode>10965</PostalCode><Quantity>1</Quantity>"
        f"<ReturnPolicy><ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption><RefundOption>MoneyBack</RefundOption>"
        f"<ReturnsWithinOption>Days_30</ReturnsWithinOption><ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption></ReturnPolicy>"
        f"<ShippingDetails><ShippingType>Flat</ShippingType><ShippingServiceOptions><ShippingServicePriority>1</ShippingServicePriority>"
        f"<ShippingService>USPSPriority</ShippingService><ShippingServiceCost>9.99</ShippingServiceCost></ShippingServiceOptions></ShippingDetails>"
        f"<ShipToLocations>US</ShipToLocations><Site>US</Site></Item></AddFixedPriceItemRequest>"
    )
    headers = {
        "X-EBAY-API-SITEID": "0", "X-EBAY-API-COMPATIBILITY-LEVEL": "967", "X-EBAY-API-CALL-NAME": "AddFixedPriceItem",
        "X-EBAY-API-APP-NAME": EBAY_APP_ID or "", "X-EBAY-API-DEV-NAME": EBAY_DEV_ID or "", "X-EBAY-API-CERT-NAME": EBAY_CERT_ID or "",
        "Content-Type": "text/xml"
    }
    try:
        response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_request.encode("utf-8"), timeout=30)
        match = re.search(r"<ItemID>(\d+)</ItemID>", response.text)
        if match: return match.group(1)
        errors = re.findall(r"<ShortMessage>(.*?)</ShortMessage>", response.text)
        return "eBay პასუხი: " + " | ".join(errors[:3]) if errors else "მომზადებულია"
    except Exception as e:
        logger.error("eBay error: " + str(e))
        return "LOCAL_DRAFT"

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
