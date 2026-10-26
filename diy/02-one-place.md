# 2. Get it in one place

Sensors speak many languages. The model only speaks one: the common CSV from chapter 1.

```
  ts,room_id,temp_c,rh_pct
```

This chapter is about getting every source into that shape.

```
  LoRaWAN network server ──webhook──▶ JSON lines ──from_uplinks.py──┐
                                                                    ├──▶ readings.csv
  BMS trend log ──────────export────▶ CSV ───────rename columns─────┘
```

## LoRaWAN: from webhook to CSV

Most network servers can send every uplink to a webhook, already decoded. Save each message as one line of JSON (a "JSON lines" file), then convert it:

```bash
python3 diy/from_uplinks.py uplinks.jsonl --out readings.csv \
  --ts-field   <where the time is> \
  --room-field <where the device name is> \
  --temp-field <where the temperature is> \
  --rh-field   <where the humidity is>
```

Each field is a dot path into the message. Some common shapes:

| Source | --ts-field | --room-field | --temp-field / --rh-field |
|---|---|---|---|
| The Things Stack (TTN) | `received_at` | `end_device_ids.device_id` | `uplink_message.decoded_payload.temperature` / `...humidity` |
| ChirpStack v4 | `time` | `deviceInfo.deviceName` | `object.temperature` / `object.humidity` |
| This repo's test data | `ts` (default) | `device.name` (default) | `decoded.temperature` / `decoded.humidity` (defaults) |

The exact names inside the decoded payload depend on your sensor's decoder. Open one message and look before you guess.

The script:
- accepts epoch milliseconds, epoch seconds or ISO 8601 times, and writes UTC
- skips any message missing one of the four fields, and tells you how many
- never fills a gap with a made-up value

## BMS: from trend log to CSV

Most BMS front ends can export a trend log as CSV. They usually come out wide, one column per point:

| Time | AHU1-Bed1-Temp | AHU1-Bed1-RH |
|---|---|---|

Turn that into the long format, one row per reading per room. A spreadsheet is fine for a one-off. For a nightly job, a few lines of script will do it. Two things to check:

- **Time zone.** BMS exports are often local time. Convert to UTC, or the night hours land on the wrong day.
- **Point names.** Map each pair of points to one `room_id`, and write that mapping down. It's the same naming problem as chapter 1, and it's where most BMS projects go wrong.

## Gaps

LoRaWAN drops packets. BMS exports skip when a controller restarts. That's normal. The scorer in chapter 3 works at any cadence and caps any single gap at 3 hours, so a week of missing data can't make a room look suddenly mouldy. Gaps still mean you saw less, so check `hours_used` in the output (chapter 3).

## Where to keep it

A folder of CSV files is enough to start. One file per day keeps the nightly job simple. When you outgrow it, any database works: the simulator in `tools/simulator/` shows a Postgres version.

**Next:** [3. Score it](03-score.md)
