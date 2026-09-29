#!/usr/bin/env python3
"""拉取佳明国行（connect.garmin.cn）数据，输出汇总 JSON。

自包含版：复用本机已保存的 OAuth token，无需每次登录。

用法：
    export GARMIN_TOKEN_DIR=/path/to/garmin-token   # 存放 OAuth token 的目录
    export GARMIN_DISPLAY_NAME=你的佳明昵称
    TMPDIR=/tmp <python3> fetch_garmin.py [天数] [输出文件.json]

- 天数：往前取多少天（默认 14）
- 输出文件：给了就写文件并打印摘要；不给就把完整 JSON 打到 stdout

依赖：pip install garth
"""
import os
import garth
import json
import datetime
import sys
import warnings

warnings.filterwarnings('ignore')

TOKEN_DIR = os.environ.get('GARMIN_TOKEN_DIR', '/home/jnak/.garmin-health-report')
DISPLAY_NAME = os.environ.get('GARMIN_DISPLAY_NAME', '战神')

garth.configure(domain='garmin.cn')
garth.resume(TOKEN_DIR)


def api(ep):
    """调 connectapi，失败不中断。"""
    try:
        return garth.client.connectapi(ep)
    except Exception as e:
        return {'_err': str(e)[:120]}


days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
out_file = sys.argv[2] if len(sys.argv) > 2 else None

today = datetime.date.today()
start = today - datetime.timedelta(days=days - 1)
out = {'today': str(today), 'range': [str(start), str(today)]}

# ---------- 资料 ----------
sp = api('/userprofile-service/socialProfile')
out['profile'] = {k: sp.get(k) for k in
                  ['id', 'displayName', 'fullName', 'userProfileFullName'] if k in sp}

# ---------- 睡眠 / 晨脉 / HRV ----------
sleep = {}
for i in range(days - 1, -1, -1):
    d = (today - datetime.timedelta(days=i)).isoformat()
    s = api(f'/wellness-service/wellness/dailySleepData/{DISPLAY_NAME}?date={d}&nonPersistent=true')
    if not isinstance(s, dict):
        sleep[d] = {'sleep_h': None}
        continue
    dto = s.get('dailySleepDTO') or {}
    sleep[d] = {
        'sleep_h': round((dto.get('sleepTimeSeconds') or 0) / 3600, 1),
        'deep_h': round((dto.get('deepSleepSeconds') or 0) / 3600, 1),
        'rem_h': round((dto.get('remSleepSeconds') or 0) / 3600, 1),
        'score': (dto.get('sleepScores') or {}).get('overall', {}).get('value'),
        'stress': s.get('avgStressLevel'),
        'rhr': s.get('restingHeartRate'),           # 静息心率在响应顶层
        'hrv': s.get('avgOvernightHrv'),            # HRV 同样在顶层
        'spo2': dto.get('averageSpO2Value'),
    }
out['sleep'] = sleep

# ---------- 活动 ----------
acts = api(f'/activitylist-service/activities/search/activities?start=0&limit={max(days * 3, 20)}')
acts = acts if isinstance(acts, list) else []
out['activities'] = []
for a in acts:
    st = a.get('startTimeLocal') or ''
    if st[:10] < str(start):
        continue
    out['activities'].append({
        'date': st,
        'name': a.get('activityName'),
        'type': (a.get('activityType') or {}).get('typeKey'),
        'km': round((a.get('distance') or 0) / 1000, 2),
        'min': round((a.get('duration') or 0) / 60, 1),
        'avgHR': a.get('averageHR'),
        'maxHR': a.get('maxHR'),
        'cal': a.get('calories'),
        'aTE': a.get('aerobicTrainingEffect'),
        'anTE': a.get('anaerobicTrainingEffect'),
        'id': a.get('activityId'),
    })

# ---------- 活动详情（配速 / 爬升 / 心率区间） ----------
details = {}
for a in out['activities']:
    d = api(f'/activity-service/activity/{a["id"]}')
    if not isinstance(d, dict) or '_err' in d:
        continue
    summ = d.get('summaryDTO') or {}
    details[str(a['id'])] = {
        'pace_min_km': round(1000 / (summ.get('averageSpeed') or 1) / 60, 2),
        'avgHR': summ.get('averageHR'),
        'maxHR': summ.get('maxHR'),
        'elevationGain': summ.get('elevationGain'),
        'elevationLoss': summ.get('elevationLoss'),
        'avgCadence': (summ.get('averageRunCadence') or summ.get('averageCadence')),
        'maxCadence': summ.get('maxRunCadence'),
        'groundContactTime': summ.get('groundContactTime'),       # 触地时间 ms
        'strideLength': summ.get('strideLength'),                 # 步幅 cm
        'verticalOscillation': summ.get('verticalOscillation'),   # 垂直振幅 cm
        'verticalRatio': summ.get('verticalRatio'),               # 垂直比例 %
        'vO2max': (d.get('fitnessOwnerDto') or {}).get('vO2MaxValue') or d.get('vO2MaxValue'),
        'hrzones': [z.get('secsInZone') for z in
                    (d.get('hrTimeInZoneDTO') or {}).get('zoneTimeSecsList', [])]
        if d.get('hrTimeInZoneDTO') else None,
    }
out['activity_details'] = details

# ---------- 体重 ----------
w = api(f'/weight-service/weight/dateRange?startDate={start}&endDate={today}')
pts = [x for x in w if isinstance(x, dict)] if isinstance(w, list) else []
out['weight'] = pts[-1].get('weight') if pts else None

# ---------- 输出 ----------
if out_file:
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'已写入 {out_file}（{len(out["activities"])} 条活动）')
    for a in out['activities']:
        det = details.get(str(a['id']), {})
        print(f"  {a['date']} {a['name']} {a['km']}km {a['min']}min "
              f"均HR{a['avgHR']} 配速{det.get('pace_min_km')} 爬升{det.get('elevationGain')}m")
else:
    print(json.dumps(out, ensure_ascii=False, indent=1))
