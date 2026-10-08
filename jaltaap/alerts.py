"""Rule-based alert text. Used when no LLM is available, and as a safety net
if the agent fails. English + Hindi templates; the Strands agent handles
every other language."""
from __future__ import annotations

import re

LANGS = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "as": "Assamese", "ta": "Tamil",
    "te": "Telugu", "ml": "Malayalam", "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada",
    "or": "Odia", "pa": "Punjabi", "ur": "Urdu",
}

EMOJI = {"green": "🟢", "orange": "🟠", "red": "🔴"}

_MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
           "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]}


def nice_date(iso: str, lang: str = "en") -> str:
    try:
        y, m, d = (int(x) for x in iso[:10].split("-"))
        return f"{d} {_MONTHS.get(lang, _MONTHS['en'])[m - 1]}"
    except Exception:
        return iso

T = {
    "en": {
        "flood_red": "🔴 FLOOD DANGER: the river near you may cross its highest danger level around {date} ({prob}% of forecast runs agree).",
        "flood_orange": "🟠 FLOOD WATCH: the river near you is likely to rise above normal flood level around {date} ({prob}% of forecast runs agree).",
        "flood_now": "{e} The river near you is above its {what} level right now ({now:,} m³/s) and {trend}.",
        "flood_do": "Move documents, medicines, grain and phone chargers up high. Keep drinking water ready. Know your nearest relief camp.",
        "heat_red": "🔴 DANGEROUS HEAT on {date}: feels like {feels}°C{wb}.",
        "heat_orange": "🟠 HEAT ALERT on {date}: feels like {feels}°C{wb}.",
        "heat_do": "Avoid outdoor work 12–4 pm. Drink water every 20 minutes even if not thirsty. Check on elderly people and children. Schools: no outdoor assembly or sports.",
        "rain": "🌧 {cat} rain expected on {date} ({mm} mm). Streets may waterlog — avoid underpasses and open drains.",
        "ok": "🟢 No flood, heavy rain or heat danger expected near you in the coming days.",
        "wb": ", humid air (wet-bulb {wb}°C)",
        "night": "🌙 {label} on {date}: it will stay {tmin}°C at night, {dep}°C above normal. Homes won't cool down, so check on elderly people and keep windows open or fans on.",
        "work": "☀️ Working outside {date}: risky {risky}. Safer {safe}. In the worst hour, heavy work means {plan} each hour, and drink {water}.",
        "chance": " {pct}% of 51 forecast runs agree.",
    },
    "hi": {
        "flood_red": "🔴 बाढ़ का ख़तरा: आपके पास की नदी {date} के आसपास सबसे ऊँचे ख़तरे के स्तर को पार कर सकती है ({prob}% पूर्वानुमान सहमत)।",
        "flood_orange": "🟠 बाढ़ चेतावनी: आपके पास की नदी {date} के आसपास सामान्य बाढ़ स्तर से ऊपर जा सकती है ({prob}% पूर्वानुमान सहमत)।",
        "flood_now": "{e} आपके पास की नदी अभी {what} स्तर से ऊपर है ({now:,} m³/s) और {trend}।",
        "flood_do": "ज़रूरी कागज़, दवाइयाँ, अनाज और फ़ोन चार्जर ऊँची जगह रखें। पीने का पानी तैयार रखें। नज़दीकी राहत शिविर पता रखें।",
        "heat_red": "🔴 ख़तरनाक गर्मी {date} को: महसूस होगा {feels}°C{wb}।",
        "heat_orange": "🟠 गर्मी की चेतावनी {date} को: महसूस होगा {feels}°C{wb}।",
        "heat_do": "दोपहर 12–4 बजे बाहर काम से बचें। प्यास न लगे तब भी हर 20 मिनट पानी पिएँ। बुज़ुर्गों और बच्चों का ध्यान रखें। स्कूल: बाहर प्रार्थना सभा या खेल न करें।",
        "rain": "🌧 {date} को भारी बारिश ({mm} मिमी) की संभावना। सड़कों पर पानी भर सकता है — अंडरपास और खुले नालों से दूर रहें।",
        "ok": "🟢 आने वाले दिनों में आपके पास बाढ़, भारी बारिश या गर्मी का ख़तरा नहीं है।",
        "wb": ", उमस (वेट-बल्ब {wb}°C)",
        "night": "🌙 {date} की रात गर्म रहेगी: {tmin}°C, सामान्य से {dep}°C ज़्यादा। घर ठंडे नहीं होंगे, बुज़ुर्गों का ध्यान रखें, पंखा चलाएँ और खिड़कियाँ खुली रखें।",
        "work": "☀️ {date} बाहर काम: {risky} ख़तरनाक। {safe} बेहतर। सबसे गर्म घंटे में भारी काम हो तो हर घंटे {plan}, और {water} पानी पिएँ।",
        "chance": " {pct}% पूर्वानुमान सहमत।",
    },
}


PERSONAS = {
    "family": {"label": "Family at home",
               "en": {"flood": "Move documents, medicines, grain and phone chargers up high. Keep drinking water ready. Know your nearest relief camp.",
                      "heat": "Stay indoors 12–4 pm. Drink water every 20 minutes even if not thirsty. Check on elderly people and small children."},
               "hi": {"flood": "ज़रूरी कागज़, दवाइयाँ, अनाज और फ़ोन चार्जर ऊँची जगह रखें। पीने का पानी तैयार रखें। नज़दीकी राहत शिविर पता रखें।",
                      "heat": "दोपहर 12–4 बजे घर के अंदर रहें। प्यास न लगे तब भी हर 20 मिनट पानी पिएँ। बुज़ुर्गों और छोटे बच्चों का ध्यान रखें।"}},
    "school": {"label": "School / college",
               "en": {"flood": "Plan to close or shift classes before the peak day. Tell parents today. Don't send buses across low bridges or causeways.",
                      "heat": "Cancel outdoor assembly and sports. Give water breaks every class. Shift exams and events to the morning."},
               "hi": {"flood": "पीक दिन से पहले छुट्टी या क्लास बदलने की योजना बनाएँ। आज ही अभिभावकों को बताएँ। बसें नीचे पुल या रपटे से न भेजें।",
                      "heat": "बाहर प्रार्थना सभा और खेल बंद करें। हर क्लास में पानी का ब्रेक दें। परीक्षा और कार्यक्रम सुबह रखें।"}},
    "farmer": {"label": "Farmer",
               "en": {"flood": "Harvest what's ready, move stored grain, seed and fertiliser up high, and shift cattle to higher ground.",
                      "heat": "Irrigate early morning or evening. Keep cattle in shade with water. Avoid spraying pesticide in the afternoon."},
               "hi": {"flood": "जो फ़सल तैयार है काट लें। अनाज, बीज और खाद ऊँची जगह रखें। पशुओं को ऊँची जगह ले जाएँ।",
                      "heat": "सिंचाई सुबह या शाम करें। पशुओं को छाँव और पानी दें। दोपहर में कीटनाशक का छिड़काव न करें।"}},
    "worker": {"label": "Outdoor worker",
               "en": {"flood": "Stay away from riverbanks, drains and underpasses at work. Keep your phone charged and share location with family.",
                      "heat": "Rest in shade 12–4 pm. Drink a glass of water every 20 minutes. Stop work if dizzy or confused and tell someone."},
               "hi": {"flood": "काम पर नदी किनारे, नालों और अंडरपास से दूर रहें। फ़ोन चार्ज रखें, परिवार को लोकेशन भेजें।",
                      "heat": "दोपहर 12–4 बजे छाँव में आराम करें। हर 20 मिनट एक गिलास पानी पिएँ। चक्कर या उलझन हो तो काम रोकें और किसी को बताएँ।"}},
    "care": {"label": "Hospital / elderly care",
             "en": {"flood": "Stock 5 days of medicines, oxygen and fuel for backup power. Move ground-floor patients up. List who needs help to evacuate.",
                    "heat": "Keep wards cool and ventilated. Prepare ORS and cooling for heatstroke cases. Check bedridden and elderly residents every 2 hours."},
             "hi": {"flood": "5 दिन की दवाइयाँ, ऑक्सीजन और जनरेटर ईंधन रखें। नीचे की मंज़िल के मरीज़ ऊपर ले जाएँ। किसे निकालने में मदद चाहिए, सूची बनाएँ।",
                    "heat": "वार्ड ठंडे और हवादार रखें। लू के मरीज़ों के लिए ORS और ठंडक तैयार रखें। बिस्तर पर पड़े और बुज़ुर्गों को हर 2 घंटे देखें।"}},
}


_HI_WORK = {"no limit": "बिना रुकावट काम", "a glass every 30 min": "हर 30 मिनट एक गिलास",
            "a glass every 20 min": "हर 20 मिनट एक गिलास", "a glass every 15 min": "हर 15 मिनट एक गिलास"}


def _hi_plan(p: str) -> str:
    if p in _HI_WORK:
        return _HI_WORK[p]
    m = re.match(r"(\d+) work / (\d+) rest", p)
    return f"{m.group(1)} मिनट काम, {m.group(2)} मिनट आराम" if m else p


def template_alert(flood: dict | None, heat: dict | None, rain: dict | None, lang: str = "en",
                   persona: str = "family", work: dict | None = None) -> str:
    lang = lang if lang in T else "en"
    t = T[lang]
    do = PERSONAS.get(persona, PERSONAS["family"])[lang]
    lines = []
    if flood and flood.get("level_peak") in ("orange", "red"):
        import datetime as _dt
        high_now = flood.get("level_now") in ("orange", "red") and flood["peak_date"] <= _dt.date.today().isoformat()
        if high_now:
            words = {"en": {"orange": "warning", "red": "danger", "falling": "slowly falling", "rising": "still rising", "steady": "holding steady"},
                     "hi": {"orange": "चेतावनी", "red": "ख़तरे के", "falling": "धीरे-धीरे उतर रही है", "rising": "अभी भी बढ़ रही है", "steady": "स्थिर है"}}[lang]
            lines += [t["flood_now"].format(e=EMOJI[flood["level_now"]], what=words[flood["level_now"]],
                                            now=flood["now_m3s"], trend=words[flood["trend"]]), do["flood"]]
        else:
            key = "flood_" + flood["level_peak"]
            prob = round(100 * (flood["prob_red"] if flood["level_peak"] == "red" else flood["prob_orange"]))
            lines += [t[key].format(date=nice_date(flood["peak_date"], lang), prob=prob), do["flood"]]
    if rain and rain.get("level") in ("orange", "red"):
        w = rain["worst_day"]
        lines.append(t["rain"].format(cat=w["imd_category"].title(), date=nice_date(w["date"], lang), mm=w["rain_mm"]))
    if heat and heat.get("level") in ("orange", "red"):
        w = heat["worst_day"]
        wb = t["wb"].format(wb=w["wetbulb_max"]) if w.get("wetbulb_max") else ""
        msg = t["heat_" + heat["level"]].format(date=nice_date(w["date"], lang), feels=w["feels_like_max"], wb=wb)
        ch = w.get("chance_red") if heat["level"] == "red" else w.get("chance_orange")
        if ch:
            msg += t["chance"].format(pct=round(100 * ch))
        lines += [msg, do["heat"]]
    if heat:
        for d in heat.get("days", []):
            if d.get("night"):
                label = d["night"] if lang == "en" else d["night"]
                lines.append(t["night"].format(label=label, date=nice_date(d["date"], lang), tmin=d["tmin"],
                                               dep=round(d["tmin"] - d["normal_tmin"], 1)))
                break
    if work and persona in ("worker", "farmer", "school") and work.get("days"):
        wd = next((d for d in work["days"] if d["level"] != "green"), None)
        if wd:
            plan, water = wd["plan"]["heavy"], wd["water"]
            if lang == "hi":
                plan, water = _hi_plan(plan), _hi_plan(water)
            else:
                plan = re.sub(r"(\d+) work / (\d+) rest", r"\1 min work, \2 min rest", plan)
            lines.append(t["work"].format(date=nice_date(wd["date"], lang), risky=", ".join(wd["risky_hours"]) or "-",
                                          safe=", ".join(wd["safe_hours"]) or "-", plan=plan, water=water))
    return "\n\n".join(lines) if lines else t["ok"]


def overall_level(*levels: str | None) -> str:
    order = {"green": 0, "orange": 1, "red": 2}
    lv = [l for l in levels if l]
    return max(lv, key=lambda x: order[x]) if lv else "green"
