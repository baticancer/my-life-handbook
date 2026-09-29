#!/usr/bin/env python3
"""按「阈值速度（配速）」分区统计跑步强度分布：各档里程 / 时间 / 占比。

用法：
    export GARMIN_TOKEN_DIR=/path/to/garmin-token
    TMPDIR=/tmp <python3> analyze_pace_zones.py [天数] [输出.json] [--scheme C] [--seg]

原理：
  1) 拉取最近 N 天的跑步活动；
  2) 整体分区：每个活动拉逐点 details（sumDuration / sumDistance），逐段差分得到每段 dt/dd，
     段配速 = dt/dd*1000（秒/km），按分区边界归入 慢跑 / 中间 / 快跑 三档；
  3) 分段明细：每个活动拉 splits（lapDTOs），逐段算配速 / 心率 / 步频 / 触地 / 步幅，
     并结合 details 逐点气温(directAirTemperature)与海拔(directElevation)算出逐段气温与爬升；
  4) 阶段识别：用 cadence + 配速自动判定 热身 / 主课 / 恢复 / 冷身，
     —— 主课段（cadence>=150 且 配速<=4:45）才代表真实训练刺激，
        技术指标（步频/触地/步幅）与分区占比均给出「整课」与「仅主课段」两版，
        避免被恢复走段稀释。
  5) 心率判读需结合气温与爬升（平路/高温下 HR 虚高，不能单看数字）。

分区方案（秒/km；<上界 归该档，最慢一档为开下界）：
  A（阈值二分）：慢跑 >4:33(273) / 中间 4:15-4:33(255-273) / 快跑 <4:15(255)
  B（突出 M）：  慢跑 >5:00(300) / 中间 4:33-5:00(273-300) / 快跑 <4:33(273)
  C（80:20 目标，曹宁默认）：慢跑 >4:45(285) / 中间 4:20-4:45(260-285) / 快跑 <4:20(260)
    注：方案 C 边界按曹宁当前 VDOT≈46（T=4:33 / I=4:15）贴合绍兴冲 3:15 的有氧主导目标重设。
"""
import os
import garth
import warnings
import json
import sys
import datetime
import argparse

warnings.filterwarnings('ignore')
garth.configure(domain='garmin.cn')
garth.resume(os.environ.get('GARMIN_TOKEN_DIR', '/home/jnak/.garmin-health-report'))

# 每套方案：(上界秒/km, 中间区下界秒/km)。慢跑=pace>上界；中间=下界<=pace<=上界；快跑=pace<下界。
SCHEMES = {
    'A': {'upper': 273, 'mid': 255, 'label': '阈值二分(T/I)'},
    'B': {'upper': 300, 'mid': 273, 'label': '突出M配速'},
    'C': {'upper': 285, 'mid': 260, 'label': '80:20目标(默认)'},
}

RUN_TYPES = ('running', 'treadmill_running', 'trail_running', 'track_running', 'indoor_running')

# 阶段判定阈值
MAIN_CADENCE = 150      # 步频 >= 此值视为"在跑"
MAIN_PACE = 285         # 配速 <=4:45 视为进入质量区间（中间区或快跑区）


def zone_of(pace, scheme):
    upper, mid = scheme['upper'], scheme['mid']
    if pace > upper:
        return 0      # 慢跑
    if pace >= mid:
        return 1      # 中间
    return 2          # 快跑


def pace_str(s):
    if s is None:
        return '—'
    m = int(s // 60); sec = int(round(s % 60))
    if sec == 60:
        m += 1; sec = 0
    return f'{m}:{sec:02d}'


def hms(s):
    s = int(round(s)); h = s // 3600; m = (s % 3600) // 60; sec = s % 60
    return f'{h}:{m:02d}:{sec:02d}'


def get_details(aid):
    """拉逐点 details，提取 (sumDistance_m, airTemp, elevation) 样本，按距离排序。"""
    try:
        d = garth.client.connectapi(
            f'/activity-service/activity/{aid}/details?maxChartSize=4000&maxPolylineSize=0')
    except Exception:
        return []
    md = [m.get('key') for m in d.get('metricDescriptors', [])]
    pts = d.get('activityDetailMetrics', [])
    fields = {}
    for k in ['sumDistance', 'directAirTemperature', 'directElevation']:
        if k in md:
            fields[k] = md.index(k)
    if 'sumDistance' not in fields:
        return []
    samples = []
    for p in pts:
        mtr = p.get('metrics') or []
        rec = {}
        ok = True
        for k, idx in fields.items():
            if idx >= len(mtr) or mtr[idx] is None:
                ok = False
                break
            rec[k] = mtr[idx]
        if ok:
            samples.append(rec)
    samples.sort(key=lambda r: r['sumDistance'])
    return samples


def temp_climb_in(samples, d0, d1):
    """返回 (段均温, 净爬升) 在距离 [d0,d1] 米范围内。"""
    ts, es = [], []
    for s in samples:
        if d0 <= s['sumDistance'] <= d1:
            if s.get('directAirTemperature') is not None:
                ts.append(s['directAirTemperature'])
            if s.get('directElevation') is not None:
                es.append(s['directElevation'])
    tavg = sum(ts) / len(ts) if ts else None
    climb = (max(es) - min(es)) if len(es) >= 2 else 0.0
    return tavg, climb


def build_splits(aid, scheme):
    """返回分段明细列表：每段 {phase, dist_km, pace, hr, cad, gct, stride, temp, climb, zone}。"""
    try:
        spl = garth.client.connectapi(f'/activity-service/activity/{aid}/splits?start=0&limit=60')
    except Exception:
        return None, []
    laps = spl.get('lapDTOs', [])
    if not laps:
        return None, []
    samples = get_details(aid)

    segs = []
    cum = 0.0
    for lp in laps:
        dist_m = lp.get('distance') or 0.0
        dur_s = lp.get('duration') or 0.0
        d0, d1 = cum, cum + dist_m
        cum = d1
        if dist_m <= 0 or dur_s <= 0:
            continue
        pace = dur_s / dist_m * 1000.0
        cad = lp.get('averageRunCadence')
        gct = lp.get('groundContactTime')
        stride = lp.get('strideLength')
        hr = lp.get('averageHR')
        tavg, climb = temp_climb_in(samples, d0, d1)
        segs.append({
            'd0': d0, 'd1': d1, 'dist_km': dist_m / 1000.0, 'dur_s': dur_s,
            'pace': pace, 'cad': cad, 'gct': gct, 'stride': stride, 'hr': hr,
            'temp': tavg, 'climb': climb, 'zone': zone_of(pace, scheme),
        })

    # 阶段识别
    n = len(segs)
    is_main = [(s['cad'] or 0) >= MAIN_CADENCE and s['pace'] <= MAIN_PACE for s in segs]
    first_main = next((i for i, m in enumerate(is_main) if m), None)
    last_main = next((i for i in range(n - 1, -1, -1) if is_main[i]), None)
    for i, s in enumerate(segs):
        if is_main[i]:
            s['phase'] = '主课'
        elif first_main is not None and i < first_main:
            s['phase'] = '热身'
        elif last_main is not None and i > last_main:
            s['phase'] = '冷身'
        else:
            s['phase'] = '恢复'
    return samples, segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('days', type=int, nargs='?', default=30)
    ap.add_argument('out_file', nargs='?', default=None)
    ap.add_argument('--scheme', default='C', choices=['A', 'B', 'C'])
    ap.add_argument('--seg', action='store_true', help='输出分段明细')
    args = ap.parse_args()

    scheme = SCHEMES[args.scheme]
    upper, mid = scheme['upper'], scheme['mid']
    slow_label = f'>{pace_str(upper)}/km'
    mid_label = f'{pace_str(mid)}-{pace_str(upper)}/km'
    fast_label = f'<{pace_str(mid)}/km'

    days = args.days
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)

    acts = garth.client.connectapi('/activitylist-service/activities/search/activities?start=0&limit=400')
    acts = acts if isinstance(acts, list) else []
    runs = [a for a in acts
            if (a.get('activityType') or {}).get('typeKey') in RUN_TYPES
            and (a.get('startTimeLocal') or '')[:10] >= str(start)
            and (a.get('distance') or 0) > 500]

    zones = [{'dist': 0.0, 'time': 0.0} for _ in range(3)]
    per_run = []
    seg_runs = []   # 仅主课段汇总，用于对比
    for a in runs:
        aid = a['activityId']
        # —— 整体分区（逐点）——
        try:
            d = garth.client.connectapi(
                f'/activity-service/activity/{aid}/details?maxChartSize=4000&maxPolylineSize=0')
        except Exception:
            d = None
        md = [m.get('key') for m in (d or {}).get('metricDescriptors', [])]
        pts = (d or {}).get('activityDetailMetrics', [])
        z = [{'dist': 0.0, 'time': 0.0} for _ in range(3)]
        if 'sumDuration' in md and 'sumDistance' in md:
            i_dur = md.index('sumDuration'); i_dst = md.index('sumDistance')
            prev_dur = prev_dst = None
            for p in pts:
                mtr = p.get('metrics') or []
                if len(mtr) <= max(i_dur, i_dst):
                    continue
                dur = mtr[i_dur]; dst = mtr[i_dst]
                if dur is None or dst is None:
                    continue
                if prev_dur is not None:
                    dt = dur - prev_dur; dd = dst - prev_dst
                    if dt > 0 and dd > 0:
                        zi = zone_of(dt / dd * 1000.0, scheme)
                        z[zi]['dist'] += dd / 1000.0
                        z[zi]['time'] += dt
                prev_dur, prev_dst = dur, dst
        for i in range(3):
            zones[i]['dist'] += z[i]['dist']; zones[i]['time'] += z[i]['time']
        per_run.append({'date': a['startTimeLocal'], 'id': aid, 'zones': z})

        # —— 分段（主课段视角）——
        samples, segs = build_splits(aid, scheme)
        if segs:
            mz = [{'dist': 0.0, 'time': 0.0} for _ in range(3)]
            cad_l, gct_l, str_l = [], [], []
            for s in segs:
                if s['phase'] == '主课':
                    mz[s['zone']]['dist'] += s['dist_km']
                    mz[s['zone']]['time'] += s['dur_s']
                    if s['cad']:
                        cad_l.append(s['cad'])
                    if s['gct']:
                        gct_l.append(s['gct'])
                    if s['stride']:
                        str_l.append(s['stride'])
            seg_runs.append({
                'date': a['startTimeLocal'], 'id': aid, 'zones': mz,
                'cad': round(sum(cad_l) / len(cad_l), 1) if cad_l else None,
                'gct': round(sum(gct_l) / len(gct_l), 1) if gct_l else None,
                'stride': round(sum(str_l) / len(str_l), 1) if str_l else None,
                'segs': segs,
            })

    tot_d = sum(z['dist'] for z in zones) or 1
    tot_t = sum(z['time'] for z in zones) or 1
    labels = [('慢跑区', slow_label), ('中间区', mid_label), ('快跑区', fast_label)]

    print(f'方案: {args.scheme}（{scheme["label"]}）  统计区间: {start} ~ {today}   跑步次数: {len(per_run)}')
    print(f'\n{"配速分区":<7}{"口径":<15}{"里程":>11}{"时间":>12}{"里程占比":>10}{"时间占比":>10}')
    for i, z in enumerate(zones):
        print(f'{labels[i][0]:<7}{labels[i][1]:<15}{z["dist"]:>9.2f}km{hms(z["time"]):>12}'
              f'{z["dist"]/tot_d*100:>9.1f}%{z["time"]/tot_t*100:>9.1f}%')
    print(f'{"合计":<7}{"—":<15}{tot_d:>9.2f}km{hms(tot_t):>12}{100.0:>9.1f}%{100.0:>9.1f}%')
    print('（上表为整课含恢复/走路段；主课段视角见下方）')

    print(f'\n{"="*64}\n仅主课段（cadence>=150 且 配速<=4:45）分区与技术均值\n{"="*64}')
    if seg_runs:
        mz_all = [{'dist': 0.0, 'time': 0.0} for _ in range(3)]
        for r in seg_runs:
            for i in range(3):
                mz_all[i]['dist'] += r['zones'][i]['dist']
                mz_all[i]['time'] += r['zones'][i]['time']
        mtot_d = sum(z['dist'] for z in mz_all) or 1
        mtot_t = sum(z['time'] for z in mz_all) or 1
        print(f'{"配速分区":<7}{"里程":>11}{"时间":>12}{"里程占比":>10}{"时间占比":>10}')
        for i, z in enumerate(mz_all):
            print(f'{labels[i][0]:<7}{z["dist"]:>9.2f}km{hms(z["time"]):>12}'
                  f'{z["dist"]/mtot_d*100:>9.1f}%{z["time"]/mtot_t*100:>9.1f}%')
        print(f'\n主课段技术均值（多课平均）：')
        cads = [r['cad'] for r in seg_runs if r['cad']]
        gcts = [r['gct'] for r in seg_runs if r['gct']]
        strs = [r['stride'] for r in seg_runs if r['stride']]
        if cads:
            print(f'  步频 {sum(cads)/len(cads):.0f}spm ｜ 触地 {sum(gcts)/len(gcts):.0f}ms ｜ 步幅 {sum(strs)/len(strs):.0f}cm')

    print('\n按次明细（整课 每档 里程km/时间）：')
    for p in sorted(per_run, key=lambda x: x['date']):
        parts = '  '.join(f'{labels[i][0]}:{p["zones"][i]["dist"]:.2f}km/{hms(p["zones"][i]["time"])}'
                          for i in range(3))
        print(f'  {p["date"][:16]}  {parts}')

    if args.seg:
        print('\n' + '=' * 64)
        print('分段明细（热身/主课/恢复/冷身 · 含气温与爬升）')
        print('=' * 64)
        for r in sorted(seg_runs, key=lambda x: x['date']):
            print(f'\n[{r["date"][:16]}] 主课段技术: 步频{r["cad"]} 触地{r["gct"]}ms 步幅{r["stride"]}cm')
            print(f'  {"段":>3} {"阶段":<4} {"距离":>7} {"配速":>7} {"区":<4} {"HR":>4} {"步频":>5} {"触地":>5} {"步幅":>5} {"℃":>5} {"爬升":>5}')
            for i, s in enumerate(r['segs']):
                zname = ['慢', '中', '快'][s['zone']]
                t = f'{s["temp"]:.1f}' if s['temp'] is not None else '—'
                hr_s = str(int(s['hr'])) if s['hr'] else '-'
                cad_s = str(int(s['cad'])) if s['cad'] else '-'
                gct_s = str(int(s['gct'])) if s['gct'] else '-'
                stride_s = str(int(s['stride'])) if s['stride'] else '-'
                print(f'  {i:>3} {s["phase"]:<4} {s["dist_km"]:>6.2f}km {pace_str(s["pace"]):>7} '
                      f'{zname:<4} {hr_s:>4} {cad_s:>5} {gct_s:>5} {stride_s:>5} {t:>5} {s["climb"]:>4.0f}m')

    if args.out_file:
        json.dump({
            'scheme': args.scheme, 'range': [str(start), str(today)], 'runs': len(per_run),
            'labels': labels,
            'zones_whole': [{'name': labels[i][0], 'range': labels[i][1],
                             'dist_km': round(zones[i]['dist'], 2), 'time_sec': round(zones[i]['time']),
                             'dist_pct': round(zones[i]['dist'] / tot_d * 100, 1),
                             'time_pct': round(zones[i]['time'] / tot_t * 100, 1)} for i in range(3)],
            'per_run': [{'date': p['date'], 'id': p['id'],
                         'zones': [{'dist_km': round(zz['dist'], 2), 'time_sec': round(zz['time'])} for zz in p['zones']]}
                        for p in per_run],
        }, open(args.out_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n已写入', args.out_file)


if __name__ == '__main__':
    main()
