# 1. Get the readings

The model needs three things for every room: temperature, relative humidity, and the time they were measured. That's it.

## Any source works

| Source | Good for | Watch out for |
|---|---|---|
| **LoRaWAN sensor** | Homes and estates. Battery lasts years, no Wi-Fi needed | You need a gateway and a network server (chapter 2) |
| **BMS trend log** | Schools, offices, buildings that already have one | Sensors are often in the return air or corridor, not the room |
| **A cheap board** (ESP32, Raspberry Pi + SHT/BME sensor) | Testing at home, learning | Keep it powered, and check its humidity accuracy |
| **Anything else** with temperature and RH | | Same three rules below |

## Three rules

**1. Often enough.** Every hour at the very least. Every 10 to 15 minutes is better. The model steps hour by hour, and a cold bedroom can be damp for a few hours each night and dry by lunchtime. Readings once a day will miss it completely.

**2. In the right place.** Mould starts where surfaces are coldest. Put the sensor where the risk is, not where it's comfortable.

```
  GOOD                                    AVOID
  ────                                    ─────
  bedroom, on an external wall            next to a radiator or heater
  away from direct sun                    by the window, in the sun
  the room people complain about          in the kitchen steam or over the kettle
  about head height                       in a cupboard, behind a curtain
```

**3. With a name.** Every reading needs a room ID that never changes. Not "bedroom", because every flat has one. Something like `rowan-house/flat-12/bedroom-1`, or the sensor's own ID if it never moves. If the sensor moves, the name moves with the room, not the sensor.

That last rule matters more than it looks. When you have 500 rooms and an alert says "bedroom", nobody knows which door to knock on.

## The minimum record

By the end of chapter 2, every reading should look like this:

| ts | room_id | temp_c | rh_pct |
|---|---|---|---|
| 2026-01-06T02:00:00Z | rowan-house/flat-12/bedroom-1 | 14.6 | 87.9 |

- `ts`: the time, in UTC, ISO 8601
- `room_id`: the name that never changes
- `temp_c`: degrees Celsius
- `rh_pct`: relative humidity, 0 to 100

Anything else the sensor sends (CO2, noise, battery) is useful, but the model doesn't need it.

## A note on accuracy

The model's threshold is around 80% RH. A sensor that reads 5% low will tell you a damp room is fine. Check new sensors side by side for a day before you trust them, and prefer sensors with a stated humidity accuracy of ±3% or better.

**Next:** [2. Get it in one place](02-one-place.md)
