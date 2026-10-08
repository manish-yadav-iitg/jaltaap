# JalTaap 🌊🔥

**Heat, floods, drought, groundwater, water tankers and pipe leaks for any place in India, on one desk. Built on AWS.**

*Jal* = water, *taap* = heat. Built for Bharat Build Tour, Event 2 (with Amazon Web Services).

## The problem

Every monsoon, rivers in Assam, Bihar, Kerala and Telangana flood, and people get maybe a few hours of warning, often none. Then a few weeks later the same places get humid heat that feels like 45°C. The official heatwave rule mostly doesn't fire for that, because it looks at temperature, not humidity.

## The desks

The app is one screen: a rail of desks on the left, a "bulletin sheet" in the middle, the map on the right. Pick a place once (search, tap the map, or a quick pick) and every desk works on it. An **Ask JalTaap** box at the bottom answers questions about that place with Amazon Bedrock, using what every desk knows.

| Desk | What it answers | How |
|---|---|---|
| **Pulse** | Where in India is water or heat turning dangerous this week? Then everything about one place, an alert you can send (SMS via Amazon SNS, read aloud by the browser, 13 languages via Amazon Bedrock) and a district bulletin saved to S3. | GloFAS river forecasts vs each river's own 40-year history, Chronos second opinion, IMD heat + rain rules |
| **Heat** | How hard is the heat on the body, who here is most exposed, which streets are hottest, when is it safe to work outside? | Body Heat Load = Heat Index + WBGT + UTCI (ECMWF thermofeel) with a "weakest link" rule; Heat Vulnerability Index from who lives here; 4-tier action plan; 49-point hot-pocket grid; WBGT work/rest planner; heat drills; 30-year trend |
| **Flood** | Will this street waterlog, why, who should do what, and which drain is choking? | Explainable logistic score from Copernicus DEM terrain, OSM drains/underpasses and rain; flash alert; impact on nearby hospitals/schools; per-role directives; drain sensors with orifice-equation blockage + Isolation Forest; storm simulator with Muskingum routing |
| **Drought** | Is a drought setting in, what can be sown, where should rain be stored, will the village run short? | SPI-1/3/6 and SPEI-3 (non-parametric, WMO), D0–D4 classes, IMD season category, Water Health Score, crop fit, recharge structures, 55 lpcd water budget with what-if sliders, Sentinel-2 greenness from AWS Open Data |
| **Groundwater** | Where is my well's water table going? | Water-table fluctuation model with CGWB infiltration factors per aquifer, stage of development (Safe → Over-exploited), 90-day scenarios, ensemble band from 25 past years, days to warning/critical/dry, advice per role, well readings with SNS alerts on category change |
| **Tankers** | Who gets water first, and did it really arrive? | Water Stress Index, priority score with its reasons (hospitals, schools, days without water, heat), fair trip packing, routes via Amazon Location, 6-digit delivery codes by SNS, diversion check on GPS tracks, Gini fairness score |
| **Leaks** | Which pipe is losing water? | Ridge expected-pressure model, robust z-score and CUSUM drift alarms, fingerprint matching to locate the pipe, minimum night flow, Isolation Forest severity, Random Forest what-if, CSV batch scoring |
| **Field reports** | Seen a flooded street? | Citizen reports merged with DBSCAN, photos stored in S3, safe walking routes around them |
| **Replay** | Would it have warned them? | Real floods replayed day by day, plus a 10-monsoon scorecard |
| **AWS** | What is running on AWS right now? | Live status of every AWS service, plus the SMS/OTP outbox |

## Where AWS is used

Seven AWS services, each called for real when AWS keys are present (`aws configure` or `.env`). Without keys each one falls back to a free local path, so `python server.py` always works.

| AWS service | Used for | Without keys |
|---|---|---|
| Amazon Bedrock (Nova Lite, Converse API) | Ask JalTaap copilot, district bulletins, Strands agent alerts, alerts in 12 Indian languages | rule-based answers, English/Hindi templates, Ollama |
| Strands Agents SDK | agent with flood, rain, heat, waterlogging, drought and groundwater tools | Ollama model |
| Amazon S3 | citizen photos, bulletins (presigned links); Sentinel-2 scenes from the AWS Open Data bucket | `data/files/` |
| Amazon DynamoDB | tanker requests, fleet, well/drain readings, audit log (single table) | SQLite `data/jaltaap.db` |
| Amazon SNS | SMS alerts, tanker delivery codes, groundwater category changes | outbox on the AWS desk |
| Amazon Location Service | place search, tanker road routes, satellite base map | Open-Meteo geocoder, straight-line estimate, OpenStreetMap tiles |
| AWS Lambda + API Gateway | the whole website (container image), deployed API + 6-hourly watch of river cities | local uvicorn server |
| Amazon Chronos-Bolt (open model) | second-opinion river forecast + backtests | – |

## Does it actually work? We tested it on real floods

The thresholds only use data from before the flood year, and the model only sees data up to each day. So it's a fair test, like it was really running back then.

| Flood | Peak vs red line | First warning | Warning before peak |
|---|---|---|---|
| Kerala, Periyar (Aug 2018) | 2.3× | 14 Aug | 2 days |
| Assam, Brahmaputra (Jun 2022) | 1.1× | 15 Jun | 2 days |
| Telangana, Godavari (Jul 2022) | 1.8× | 11 Jul | 5 days |
| Delhi, Yamuna (Jul 2023) | missed | – | – |

We also ran it on **every monsoon day from 2015 to 2024**, not just the famous floods. When JalTaap says "warning", it's right about **9 times out of 10** (86–91% on all four rivers). It catches about half of the high-water days in advance, and the softer "watch" level catches around 80% but with more false alarms. Full numbers: `python -m jaltaap.evaluate`.

The Delhi 2023 flood was mostly caused by water released from a barrage upstream. River models can't see that. That's exactly why the citizen reports feature exists.

## Google Flood Hub as a second opinion

Google runs its own AI flood forecasts for India ([Flood Hub](https://sites.research.google/floods/)). JalTaap shows Google's reading next to its own for any place: "JalTaap and Google agree", or which one is more worried. It can also draw Google's predicted flood area on the map, and there's a toggle for every Google gauge in India.

To switch it on (free, the data is CC BY 4.0):
1. Join the waitlist: https://developers.google.com/flood-forecasting
2. When Google emails you, enable the Flood Forecasting API in your Google Cloud project and make an API key
3. Put it in `.env` as `GOOGLE_FLOODHUB_API_KEY=...` and restart

Without a key, every place still gets an "Open this spot on Google Flood Hub" link.

## Built with (all open source / free)

| What | Used for |
|---|---|
| [Strands Agents SDK](https://strandsagents.com) (AWS) | the alert-writing agent and its tools |
| [Amazon Chronos-Bolt](https://huggingface.co/amazon/chronos-bolt-small) | second-opinion river forecast + backtests |
| [GloFAS](https://global-flood.emergency.copernicus.eu/) via [Open-Meteo](https://open-meteo.com/en/docs/flood-api) | river forecasts and 40 years of river history |
| Open-Meteo weather + ERA5 | rain, heat, wet-bulb, heat history |
| OpenStreetMap + OSMnx | roads, hospitals, schools, relief spots |
| [thermofeel](https://github.com/ecmwf/thermofeel) (ECMWF) | WBGT and wet-bulb for the outdoor-work safety strip |
| ECMWF ensemble (51 runs) via Open-Meteo | % chance of dangerous heat each day |
| scikit-learn DBSCAN | merging duplicate citizen reports |
| CLIP (Hugging Face) | quick "is this really a flood photo?" check |
| [Google Flood Hub API](https://developers.google.com/flood-forecasting) (optional) | second-opinion flood status + flood-area maps |
| AWS Lambda, API Gateway, DynamoDB, Bedrock | deployed version |

## Run it on your laptop (Windows)

```powershell
cd C:\Users\my060\Project_hack
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
# optional: Strands agent, Chronos, OSM routes, Telegram bot
python -m pip install -r requirements-extras.txt

# the web app -> open http://localhost:8000
python server.py

# rebuild the flood replays + scorecard (takes a few minutes the first time)
python -m jaltaap.replay
python -m jaltaap.evaluate
```

**Switch AWS on:** run `aws configure` (region `ap-south-1`), enable the Nova Lite model in the Bedrock console, copy `.env.example` to `.env`, and restart. The AWS desk shows which services are live. `JALTAAP_MODEL=auto` uses Bedrock when keys exist and [Ollama](https://ollama.com) otherwise.

**Telegram bot:** get a token from @BotFather, copy `.env.example` to `.env`, put the token in, then run `python bot.py`.

**Missing a few heat cities?** Open-Meteo limits history downloads per hour. Run `python scripts/build_stations.py --cities` later and it fills in only the missing ones.

**Quick check from the terminal:** `python -m jaltaap.agent 25.59 85.14 hi` gives a Hindi alert for Patna.

## Deploy on AWS

Step-by-step guide: [DEPLOY_AWS.md](DEPLOY_AWS.md).

```powershell
# 1. the AWS services + the risk/alert API
python deploy/build.py
cd deploy
sam deploy --guided
cd ..

# 2. the whole website on AWS Lambda behind API Gateway (needs Docker Desktop running)
python deploy/web/deploy.py
```

You get:
- a DynamoDB records table, an S3 bucket, an SNS topic, an Amazon Location place index, route calculator and map key. The stack output `EnvForLaptop` lists the values to paste into `.env` so your laptop server uses them too
- `GET /risk?lat=..&lon=..` returns flood, rain and heat as JSON
- `GET /alert?lat=..&lon=..&lang=hi` returns an alert written by the agent on Bedrock
- a Lambda that checks 12 river cities every 6 hours, saves them to DynamoDB, and posts to a Telegram channel when a place turns orange or red
- from step 2: the whole website as a Lambda container image (the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) runs the same `server.py`), behind its own API Gateway URL

## Project layout

```
jaltaap/
  flood.py     river snapping, 40-yr thresholds, ensemble forecast
  heat.py      feels-like, IMD heatwave + warm-night rules, 51-run ensemble chance, heat trends
  heatstress.py hour-by-hour WBGT, safe hours, work/rest plan
  rain.py      IMD heavy-rain categories, cloudburst-ish hourly check
  forecast.py  Amazon Chronos wrapper
  replay.py    replay past floods, days-of-warning charts
  evaluate.py  10-monsoon scorecard (hits, false alarms)
  reports.py   citizen reports, DBSCAN merging, photo check, subscribers
  routing.py   safe walking route around flooded streets
  agent.py     Strands agent + tools
  alerts.py    fallback alert templates
  floodhub.py  Google Flood Hub second opinion (optional)
  national.py  the all-India board (38 rivers + cities in 2 API calls)
  aws.py       every AWS call in one place, each with a local fallback
  store.py     DynamoDB single-table store (SQLite when offline)
  copilot.py   Ask JalTaap + district bulletins (Bedrock)
  thermal.py   Body Heat Load: Heat Index + WBGT + UTCI
  vulnerability.py  Heat Vulnerability Index, action tiers, drills
  microclimate.py   hot pockets on a 49-point grid
  waterlog.py  street waterlogging score, flash alert, impact, directives
  drains.py    drain sensors, blockage detection, storm cascade
  drought.py   SPI/SPEI, water health, crops, recharge, water budget
  satellite.py Sentinel-2 greenness from AWS Open Data
  groundwater.py  water-table model, CGWB stage, scenarios, readings
  tankers.py   priority, trip planning, delivery codes, diversion, fairness
  leaks.py     pressure model, CUSUM, leak location, severity, what-if
server.py      web server + JSON API (FastAPI)
web/           the web app (plain HTML/CSS/JS, Leaflet, Chart.js): app.js = shell + Pulse/Field/Replay/AWS, desks.js = the six hazard desks
bot.py         Telegram bot
scripts/       builds the national watch list (danger lines for every river point)
deploy/        AWS SAM (Lambda, API Gateway, DynamoDB, S3, SNS, Location); deploy/web/ = the website as a Lambda container
data/          past flood events
outputs/       replay charts + scorecard
```

## Honest limits

- GloFAS is a model, not a gauge reading, and it only covers bigger rivers. Small streams and city drains are covered by the rain check and by people's reports.
- The warning is 2–5 days, not weeks. That's still enough to move grain, documents, medicines and old people.
- It can't see dam or barrage releases. Citizen reports fill part of that gap.
