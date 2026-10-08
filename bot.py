"""JalTaap Telegram bot.

- /start            -> pick your language
- send location     -> instant flood / rain / heat check + you're subscribed
- send a photo      -> report flooding (then send location to pin it)
- /report           -> report flooding without a photo
- every 6 hours     -> checks all subscribers, messages only when risk goes UP

Run:  python bot.py   (needs TELEGRAM_BOT_TOKEN in .env)
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from jaltaap import reports
from jaltaap.agent import make_alert
from jaltaap.alerts import LANGS, overall_level
from jaltaap.flood import flood_risk
from jaltaap.heat import heat_risk
from jaltaap.rain import rain_risk
from jaltaap.routing import safe_route

load_dotenv()
logging.basicConfig(level=logging.INFO)
USE_LLM = os.environ.get("JALTAAP_USE_LLM", "1") == "1"
PHOTO_DIR = os.path.join(os.path.dirname(__file__), "data", "photos")
os.makedirs(PHOTO_DIR, exist_ok=True)

LOC_KB = ReplyKeyboardMarkup([[KeyboardButton("📍 Send my location", request_location=True)]],
                             resize_keyboard=True)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows, row = [], []
    for code, name in LANGS.items():
        row.append(InlineKeyboardButton(name, callback_data=f"lang:{code}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await update.message.reply_text(
        "🌊🔥 JalTaap warns you before floods, heavy rain and dangerous heat hit your area.\n\n"
        "Choose your language / अपनी भाषा चुनें:", reply_markup=InlineKeyboardMarkup(rows))


async def pick_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    code = q.data.split(":")[1]
    reports.set_lang(q.message.chat_id, code)
    await q.answer()
    await q.message.reply_text(f"✅ {LANGS[code]}. Now send your location 👇", reply_markup=LOC_KB)


def _check(lat, lon, lang):
    alert = make_alert(lat, lon, lang, use_llm=USE_LLM)
    levels = []
    for fn, key in ((flood_risk, "level_peak"), (rain_risk, "level"), (heat_risk, "level")):
        try:
            levels.append(fn(lat, lon)[key])
        except Exception:
            pass
    return alert["text"], overall_level(*levels)


async def got_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    chat = update.message.chat_id
    sub = reports.get_subscriber(chat) or {}
    lang = sub.get("lang") or "en"

    # this location pins a pending flood report
    if ctx.user_data.get("pending_report"):
        p = ctx.user_data.pop("pending_report")
        reports.add_report(loc.latitude, loc.longitude, "flood", p.get("depth", "knee"),
                           photo=p.get("photo"), source="telegram")
        inc = reports.nearby_incidents(loc.latitude, loc.longitude, km=0.5)
        n = inc[0]["reports"] if inc else 1
        await update.message.reply_text(f"🙏 Thanks! Report saved. {n} report(s) at this spot now. "
                                         "Others nearby will be routed around it.")
        return

    reports.save_subscriber(chat, loc.latitude, loc.longitude, lang)
    await update.message.reply_text("⏳ Checking river, rain and heat for your spot...")
    text, level = await asyncio.to_thread(_check, loc.latitude, loc.longitude, lang)
    reports.set_last_level(chat, level)
    await update.message.reply_text(text)
    if level != "green":
        try:
            mode = "heat" if "°C" in text and "flood" not in text.lower() else "flood"
            inc = reports.nearby_incidents(loc.latitude, loc.longitude, km=5)
            r = await asyncio.to_thread(safe_route, loc.latitude, loc.longitude, mode, inc)
            if r.get("ok"):
                d = r["destination"]
                await update.message.reply_text(f"🧭 Nearest safe spot: {d['name']} — {r['distance_m']} m, "
                                                 f"~{r['walk_min']} min walk.")
                await update.message.reply_location(d["lat"], d["lon"])
        except Exception as e:
            logging.warning("route failed: %s", e)
    await update.message.reply_text("🔔 You're subscribed. I'll message you if risk goes up here.\n"
                                     "See flooding? Send a photo to warn others.")


async def got_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    f = await update.message.photo[-1].get_file()
    path = os.path.join(PHOTO_DIR, f"{update.message.chat_id}_{update.message.message_id}.jpg")
    await f.download_to_drive(path)
    ctx.user_data["pending_report"] = {"photo": path}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(x.title(), callback_data=f"depth:{x}")
                                for x in ("ankle", "knee", "waist")]])
    await update.message.reply_text("How deep is the water?", reply_markup=kb)


async def report_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["pending_report"] = {}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(x.title(), callback_data=f"depth:{x}")
                                for x in ("ankle", "knee", "waist")]])
    await update.message.reply_text("How deep is the water?", reply_markup=kb)


async def pick_depth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ctx.user_data.setdefault("pending_report", {})["depth"] = q.data.split(":")[1]
    await q.answer()
    await q.message.reply_text("Now send the location of the flooding 👇", reply_markup=LOC_KB)


async def periodic_check(ctx: ContextTypes.DEFAULT_TYPE):
    """Only message people when their risk goes UP (no spam)."""
    order = {"green": 0, "orange": 1, "red": 2}
    for s in reports.all_subscribers():
        try:
            text, level = await asyncio.to_thread(_check, s["lat"], s["lon"], s["lang"] or "en")
            if order[level] > order.get(s["last_level"] or "green", 0):
                await ctx.bot.send_message(s["chat_id"], text)
            reports.set_last_level(s["chat_id"], level)
        except Exception as e:
            logging.warning("check failed for %s: %s", s["chat_id"], e)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather on Telegram)")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CallbackQueryHandler(pick_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(pick_depth, pattern=r"^depth:"))
    app.add_handler(MessageHandler(filters.LOCATION, got_location))
    app.add_handler(MessageHandler(filters.PHOTO, got_photo))
    hours = float(os.environ.get("CHECK_EVERY_HOURS", "6"))
    app.job_queue.run_repeating(periodic_check, interval=hours * 3600, first=60)
    print("JalTaap bot running. Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
