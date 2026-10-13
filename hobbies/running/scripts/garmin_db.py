#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""garmin_db.py - 佳明数据桥 P0: 落库SQLite + 每日快照 health_context
用法: python garmin_db.py [--days 30] [--backfill] [--no-detail] [--rebuild]
纪律: 缺失=NULL不填0; 哨兵255丢弃; 单位米/秒/摄氏度; 数字判读由代码做
"""
import os, sys, json, time, sqlite3, datetime, argparse, statistics
import garth

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "garmin.db")
CTX_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_context.md")
CTX_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_context.json")
TOKEN_DIR = "/home/jnak/.garmin-health-report"
GARMIN_USER = "战神"
RHR_BASELINE = 50
Z2_HI = 130
SENTINEL = 255
_warned = False


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def api(path, tries=3):
    global _warned
    for i in range(tries):
        try:
            r = garth.connectapi(path)
            if r not in (None, [], {}):
                return r
        except Exception as e:
            if not _warned:
                log(f"connectapi err: {e}")
                _warned = True
        if i < tries - 1:
            time.sleep(2)
    return None


def init_db(conn):
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS activities (activity_id INTEGER PRIMARY KEY, start_local TEXT, date TEXT, name TEXT, sport TEXT, distance_m REAL, duration_s REAL, avg_hr INTEGER, max_hr INTEGER, avg_cadence INTEGER, elevation_gain REAL, calories REAL, vo2max REAL, avg_pace_spm REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS wellness (date TEXT PRIMARY KEY, rhr INTEGER, hrv REAL, sleep_score INTEGER, sleep_total_s REAL, body_battery_low REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS metrics (activity_id INTEGER, seq INTEGER, ts REAL, hr INTEGER, speed REAL, cadence INTEGER, gct INTEGER, stride REAL, vert_osc REAL, vert_ratio REAL, temp REAL, elevation REAL, PRIMARY KEY (activity_id, seq))")
    conn.commit()


# === PART1 END ===

def upsert_activity(conn, a):
    c = conn.cursor()
    dist = a.get("distance")
    dur = a.get("duration")
    avg_hr = a.get("averageHR")
    max_hr = a.get("maxHR")
    cad = a.get("averageRunCadence") or a.get("avgBikeCadence")
    elev = a.get("elevationGain")
    cal = a.get("calories")
    vo2 = a.get("vO2MaxValue")
    name = a.get("activityName") or ""
    start = a.get("startTimeLocal") or ""
    date = start[:10] if start else ""
    pace = round(dist / dur, 4) if (dist and dur) else None
    if avg_hr == SENTINEL: avg_hr = None
    if max_hr == SENTINEL: max_hr = None
    at = a.get("activityType")
    if isinstance(at, dict):
        sport = at.get("typeKey") or str(at.get("typeId", ""))
    else:
        sport = at or a.get("sport")
    c.execute("INSERT OR REPLACE INTO activities (activity_id, start_local, date, name, sport, distance_m, duration_s, avg_hr, max_hr, avg_cadence, elevation_gain, calories, vo2max, avg_pace_spm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.get("activityId"), start, date, name, sport, dist, dur, avg_hr, max_hr, cad, elev, cal, vo2, pace))
    conn.commit()


def upsert_wellness(conn, date, s):
    c = conn.cursor()
    rhr = s.get("restingHeartRate")
    hrv = s.get("avgOvernightHrv")
    dto = s.get("dailySleepDTO") or {}
    score = dto.get("sleepScore") if isinstance(dto, dict) else None
    total = dto.get("sleepTimeSeconds") if isinstance(dto, dict) else None
    bb = s.get("bodyBatteryLowestValue")
    if rhr == SENTINEL: rhr = None
    c.execute("INSERT OR REPLACE INTO wellness (date, rhr, hrv, sleep_score, sleep_total_s, body_battery_low) VALUES (?,?,?,?,?,?)", (date, rhr, hrv, score, total, bb))
    conn.commit()


# === PART2 END ===

def fetch_details_metrics(conn, aid, limit_points=0):
    if limit_points <= 0:
        return
    d = api(f"/activity-service/activity/{aid}/details?maxChartSize=4000&maxPolylineSize=0")
    if not d:
        return
    idxkey = {m["metricsIndex"]: m["key"] for m in d.get("metricDescriptors", [])}
    hr_i = next((i for i, k in idxkey.items() if "HeartRate" in k), None)
    ts_i = next((i for i, k in idxkey.items() if k == "directTimestamp"), None)
    sp_i = next((i for i, k in idxkey.items() if k == "directSpeed"), None)
    cad_i = next((i for i, k in idxkey.items() if "Cadence" in k), None)
    gct_i = next((i for i, k in idxkey.items() if "GroundContact" in k), None)
    st_i = next((i for i, k in idxkey.items() if "Stride" in k), None)
    vo_i = next((i for i, k in idxkey.items() if "VerticalOscillation" in k), None)
    vr_i = next((i for i, k in idxkey.items() if "VerticalRatio" in k), None)
    tp_i = next((i for i, k in idxkey.items() if k == "directAirTemperature"), None)
    el_i = next((i for i, k in idxkey.items() if k == "directElevation"), None)
    c = conn.cursor()
    rows = []
    for seq, blk in enumerate(d.get("activityDetailMetrics", [])):
        v = blk.get("metrics", [])
        def get(i):
            try:
                return v[i] if (i is not None and i < len(v)) else None
            except Exception:
                return None
        hr = get(hr_i)
        if hr == SENTINEL: hr = None
        rows.append((aid, seq, get(ts_i), hr, get(sp_i), get(cad_i), get(gct_i), get(st_i), get(vo_i), get(vr_i), get(tp_i), get(el_i)))
        if len(rows) >= limit_points:
            break
    c.executemany("INSERT OR REPLACE INTO metrics (activity_id, seq, ts, hr, speed, cadence, gct, stride, vert_osc, vert_ratio, temp, elevation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


# === PART3 END ===

def collect(conn, days_wellness=30, backfill=False, with_detail=False):
    garth.configure(domain="garmin.cn")
    garth.resume(TOKEN_DIR)
    act_limit = 200 if backfill else 60
    fetched = 0
    start = 0
    page = 20
    seen_ids = set()
    while fetched < act_limit:
        acts = api(f"/activitylist-service/activities/search/activities?start={start}&limit={page}")
        if not acts:
            break
        for a in acts:
            aid = a.get("activityId")
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            upsert_activity(conn, a)
            if with_detail:
                fetch_details_metrics(conn, aid, limit_points=4000)
        fetched += len(acts)
        start += page
        if len(acts) < page:
            break
        time.sleep(0.3)
    log(f"活动入库: {len(seen_ids)} 条")
    w_days = 365 if backfill else days_wellness
    today = datetime.date.today()
    ok = 0
    miss = 0
    for i in range(w_days):
        d = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        cur = conn.execute("SELECT rhr FROM wellness WHERE date=?", (d,)).fetchone()
        if cur is not None and not backfill:
            continue
        s = api(f"/wellness-service/wellness/dailySleepData/{GARMIN_USER}?date={d}&nonPersistent=true")
        if s and (s.get("restingHeartRate") is not None or s.get("avgOvernightHrv") is not None):
            upsert_wellness(conn, d, s)
            ok += 1
        else:
            miss += 1
        time.sleep(0.15)
    log(f"wellness: 新增 {ok} 天, 缺 {miss} 天")


# === PART4 END ===

def pace_str(speed_ms):
    if not speed_ms or speed_ms < 0.1:
        return "-"
    sec = 1000.0 / speed_ms
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}:{s:02d}/km"


def build_context(conn):
    cur = conn.cursor()
    today = datetime.date.today()
    out = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"), "rhr_baseline": RHR_BASELINE}
    a_range = cur.execute("SELECT MIN(date), MAX(date) FROM activities").fetchone()
    w_range = cur.execute("SELECT MIN(date), MAX(date) FROM wellness WHERE rhr IS NOT NULL").fetchone()
    out["coverage"] = {"activities": list(a_range), "wellness_rhr": list(w_range)}
    recent_w = []
    for i in range(13, -1, -1):
        d = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        row = cur.execute("SELECT rhr, hrv, sleep_score FROM wellness WHERE date=?", (d,)).fetchone()
        recent_w.append({"date": d, "rhr": row[0] if row else None, "hrv": row[1] if row else None, "sleep_score": row[2] if row else None})
    out["recent_14d_wellness"] = recent_w
    missing_days = [w["date"] for w in recent_w if w["rhr"] is None]
    out["wellness_missing_last14d"] = missing_days
    avail = [w for w in recent_w if w["rhr"] is not None]
    if len(avail) >= 2:
        recovered = all(w["rhr"] <= RHR_BASELINE + 2 for w in avail[-2:])
    elif len(avail) == 1:
        recovered = avail[0]["rhr"] <= RHR_BASELINE + 2
    else:
        recovered = None
    out["morning_hr_recovered"] = recovered
    out["latest_rhr"] = avail[-1]["rhr"] if avail else None
    out["latest_hrv"] = avail[-1]["hrv"] if avail else None


# === PART5 END ===

    d7 = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    acts7 = cur.execute("SELECT date, name, distance_m, duration_s, avg_hr FROM activities WHERE date>=? ORDER BY date", (d7,)).fetchall()
    weekly_km = sum((a[2] or 0) for a in acts7) / 1000.0
    out["last_7d"] = {"activities": [{"date": a[0], "name": a[1], "km": round((a[2] or 0)/1000, 2), "min": round((a[3] or 0)/60), "avg_hr": a[4]} for a in acts7], "total_km": round(weekly_km, 1)}
    d30 = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    acts30 = cur.execute("SELECT name, avg_hr, distance_m FROM activities WHERE date>=?", (d30,)).fetchall()
    quality = [a for a in acts30 if (a[1] and a[1] > Z2_HI) or any(t in (a[0] or "") for t in ("@I", "@T", "@MP", "间歇", "阈值"))]
    out["last_30d_quality_sessions"] = len(quality)
    longest = max((a[2] or 0) for a in acts30) if acts30 else 0
    out["last_30d_longest_km"] = round(longest / 1000, 2)
    out["data_ready_for_review"] = (len(missing_days) == 0) and (recovered in (True, None))
    with open(CTX_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _write_md(out)
    return out


def _write_md(out):
    L = []
    L.append("# 每日身体状态快照 health_context")
    L.append(f"> 自动生成于 {out['generated_at']}  |  RHR基线={out['rhr_baseline']}")
    cov = out["coverage"]
    L.append("")
    L.append(f"**数据覆盖**: 活动 {cov['activities'][0]}~{cov['activities'][1]} | 晨脉 {cov['wellness_rhr'][0]}~{cov['wellness_rhr'][1]}")
    L.append("")
    rec = out["recent_14d_wellness"]
    L.append("## 近14天 晨脉/HRV")
    L.append("| 日期 | RHR | HRV | 睡眠分 |")
    L.append("|---|---|---|---|")
    for w in rec:
        L.append(f"| {w['date']} | {w['rhr'] if w['rhr'] is not None else '缺'} | {w['hrv'] if w['hrv'] is not None else '缺'} | {w['sleep_score'] if w['sleep_score'] is not None else '缺'} |")
    miss = out["wellness_missing_last14d"]
    if miss:
        L.append(f"\n注意: 缺晨脉日期 {', '.join(miss)} (多为手表未同步, 手动同步即可)")
    L.append("")
    rh = out["morning_hr_recovered"]
    L.append(f"## 晨脉恢复判定(代码): 最新RHR={out['latest_rhr']} HRV={out['latest_hrv']} -> {'已回基线' if rh is True else ('未回基线' if rh is False else '数据不足')}")
    w7 = out["last_7d"]
    L.append("")
    L.append(f"## 近7天活动 (总 {w7['total_km']} km)")
    L.append("| 日期 | 名称 | 距离 | 时长 | 平均HR |")
    L.append("|---|---|---|---|---|")
    for a in w7["activities"]:
        L.append(f"| {a['date']} | {a['name']} | {a['km']}km | {a['min']}min | {a['avg_hr'] or '缺'} |")
    L.append("")
    L.append(f"## 近30天: 质量课 {out['last_30d_quality_sessions']} 次 | 最长 {out['last_30d_longest_km']} km")
    L.append("")
    L.append(f"## 复评就绪: {'是' if out['data_ready_for_review'] else '否(存在缺数据/未恢复)'}")
    with open(CTX_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# === PART6 END ===

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--no-detail", dest="detail", action="store_false")
    ap.add_argument("--rebuild", action="store_true", help="仅重建快照不联网")
    args = ap.parse_args()
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    if not args.rebuild:
        log(f"数据库: {DB_PATH}")
        collect(conn, days_wellness=args.days, backfill=args.backfill, with_detail=args.detail)
    else:
        log("仅重建快照(跳过联网)")
    ctx = build_context(conn)
    conn.close()
    log(f"快照已生成: {CTX_MD}")
    log(f"复评就绪: {ctx['data_ready_for_review']}")


if __name__ == "__main__":
    main()

