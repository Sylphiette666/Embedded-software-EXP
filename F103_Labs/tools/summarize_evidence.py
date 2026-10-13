"""Summarize existing bench evidence; never opens serial/SWD or changes source logs.

Whole experiments are capped at partial even when individual checks pass.
Optional --physical-evidence supplies explicitly recorded human observations as
{"labs": [{"id": 4, "checks": [{"item": "...", "status": "passed",
"method": "physical_button|visual", "observed": "...", "evidence": ["..."]}]}]}.
The operator owns those observation records; this tool never invents them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import struct

from intelhex import IntelHex

ROOT = Path(__file__).resolve().parents[1]
BASE, SIZE, BLOCK = 0x08000000, 65536, 256


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def ref(path):
    return str(path.resolve())


def check(item, status, method, observed, evidence=()):
    return dict(item=item, status=status, method=method, observed=observed,
                evidence=[str(p) for p in evidence])


def flash_from_rx_tail(path, reads=1):
    """AN3155 Read Memory emits 3 ACKs then 256 bytes per block.

    Final complete Flash reads occur immediately before the reset cleanup in
    bench_isp.py; cleanup itself reads no UART bytes. Decode actual raw RX rather
    than trusting the JSON PASS field alone.
    """
    raw = path.read_bytes()
    span = (BLOCK + 3) * (SIZE // BLOCK)
    if len(raw) < span * reads:
        raise ValueError('raw RX file is too short for requested complete Flash reads')
    tail = raw[-span * reads:]
    output = []
    for index in range(reads):
        sequence = tail[index * span:(index + 1) * span]
        blocks = []
        for offset in range(0, span, BLOCK + 3):
            chunk = sequence[offset:offset + BLOCK + 3]
            if chunk[:3] != b'\x79\x79\x79':
                raise ValueError('raw RX tail does not contain complete ACK/data Flash blocks')
            blocks.append(chunk[3:])
        output.append(b''.join(blocks))
    return output


def backup_valid(path, uid):
    data = path.read_bytes()
    meta = read_json(path.with_suffix('.json'))
    if (len(data) != SIZE or meta.get('uid') != uid or meta.get('sha256') != sha(data)
            or meta.get('address') != BASE or meta.get('length') != SIZE):
        raise ValueError('backup size/UID/address/hash mismatch')
    sp, reset = struct.unpack('<II', data[:8])
    if (data in (bytes(SIZE), b'\xFF' * SIZE) or not 0x20000000 < sp <= 0x20005000
            or not reset & 1 or not BASE <= reset & ~1 < BASE + SIZE):
        raise ValueError('blank/invalid original vector table')
    return data, meta


def validate_isp(path, record, builds):
    target = record.get('target', '')
    result = {'method': 'ISP', 'status': 'not_tested', 'target': target,
              'evidence': [ref(path)], 'notes': '', 'started_at': record.get('started_at'),
              'hex_sha256': record.get('hex_sha256'), 'uid': record.get('device', {}).get('uid')}
    if record.get('status') != 'PASS':
        result['status'] = 'failed'
        result['notes'] = 'ISP操作未完成：' + str(record.get('error', '未获得PASS记录'))
        return result
    try:
        device = record['device']
        if device['pid'] != '0x0410' or device['flash_kib'] != 64:
            raise ValueError('unexpected target identity')
        if record.get('verification') != 'complete_64k_readback_matches_expected_RMW_image':
            raise ValueError('missing full readback verification declaration')
        hexpath = Path(record['hex_file'])
        actual_hex_hash = sha(hexpath.read_bytes())
        build = builds[target]
        if (not build['passed'] or record['hex_sha256'] != actual_hex_hash
                or build['hex_sha256'] != actual_hex_hash):
            raise ValueError('historical downloaded HEX differs from current validated build')
        before_path = Path(record['preflash_backup'])
        before, _ = backup_valid(before_path, device['uid'])
        expected = bytearray(before)
        image = IntelHex(str(hexpath))
        for address in image.addresses():
            if not BASE <= address < BASE + SIZE:
                raise ValueError('HEX outside documented Flash')
            expected[address - BASE] = image[address]
        rxpath = path.with_suffix('.rx.bin')
        actual = flash_from_rx_tail(rxpath)[0]
        if actual != expected or sha(actual) != record['readback_sha256']:
            raise ValueError('decoded raw Flash readback differs from expected RMW bytes/hash')
        result.update(status='passed', readback_sha256=sha(actual),
                      hex_sha256=actual_hex_hash, preflash_backup=ref(before_path),
                      notes='当前HEX与Keil构建、ISP下载记录一致；从原始RX重建的完整64KB回读与RMW期望值逐字节一致。')
        result['evidence'].extend([ref(rxpath), ref(before_path), ref(hexpath)])
    except (OSError, KeyError, ValueError, TypeError) as error:
        # An evidence gap or changed artifact is not proof of a firmware defect.
        result['notes'] = '证据关联未通过，未判定实验程序失败：' + str(error)
    return result


def serial_record(path, record):
    lab = record.get('requested_lab', record.get('lab'))
    if lab not in range(1, 9) or not record.get('actual_hardware'):
        return None
    if 'serial_text' in record:
        rawpath = Path(record['raw_serial_file'])
        raw = rawpath.read_bytes()
        valid = sha(raw) == record.get('serial_rx_sha256')
        text = raw.decode('utf-8', errors='replace')
        declared_checks = record.get('checks', [])
    else:
        rawpath = path.with_suffix('.raw')
        raw = rawpath.read_bytes()
        event_bytes = b''.join(bytes.fromhex(e['hex']) for e in record.get('events', [])
                               if e.get('direction') == 'RX' and 'hex' in e)
        valid = raw == event_bytes and (not record.get('sha256') or sha(raw) == record['sha256'])
        text = raw.decode('utf-8', errors='replace')
        declared_checks = record.get('checks', [])
    evidence = [ref(path), ref(rawpath)]
    checks = [check('真实UART数据及原始文件一致性', 'passed' if valid and raw else 'not_tested',
                    'serial', f'收到{len(raw)}字节；SHA256={sha(raw)}；校验一致={valid}。', evidence)]
    if not valid:
        return lab, checks
    if lab == 1:
        states = re.findall(r'STATE ms=(\d+) LED0=(\d+) LED1=(\d+)', text)
        patterns = sorted(set((int(a), int(b)) for _, a, b in states))
        checks.append(check('LED输出寄存器状态的串口报告',
                            'passed' if {(0, 0), (1, 0)}.issubset(set(patterns)) else 'not_tested',
                            'serial', f'{len(states)}条STATE，LED0/LED1报告状态={patterns}；未测引脚电压或LED发光。', evidence))
    elif lab == 6 and declared_checks and 'command_hex' in declared_checks[0]:
        for item in declared_checks:
            checks.append(check('串口命令0x' + item['command_hex'],
                                'passed' if item.get('passed') is True else 'failed', 'serial',
                                f"响应={item.get('reply', '').strip()!r}；状态={item.get('state', '').strip()}；仅证明固件报告。", evidence))
    else:
        for item in declared_checks:
            if item.get('name') == 'UART_response_received':
                continue
            observed = json.dumps(item, ensure_ascii=False)
            if item.get('name') == 'coarse_serial_report_alternation':
                intervals = item.get('host_intervals_seconds', [])
                observed = (f"报告翻转{len(item.get('reported_changes', []))}次；"
                            f"目标间隔{item.get('expected_seconds')}秒；PC到达时间均值"
                            f"{item.get('host_mean_seconds', 0):.6f}秒；")
                if intervals:
                    observed += f"区间{min(intervals):.6f}～{max(intervals):.6f}秒。"
                observed += '仅是固件意图/ODR串口报告和PC粗计时，未测LED光线、引脚电压或校准时基。'
            checks.append(check(item.get('name', '串口报告检查'),
                                'passed' if item.get('passed') is True else 'not_tested',
                                'serial', observed, evidence))
    return lab, checks


def generate(evidence_root, physical_path=None):
    buildpath = ROOT / 'Build' / 'keil_results.json'
    builds = {r['target']: r for r in read_json(buildpath)}
    files = []
    for path in sorted(evidence_root.glob('*.json')):
        if path.name in ('validation_summary.json', 'physical_observations.json'):
            continue
        try:
            record = read_json(path)
            if isinstance(record, dict) and record.get('status') not in ('STARTED', 'RUNNING', 'IN_PROGRESS'):
                files.append((path, record))
        except (OSError, ValueError):
            # The controller may still be writing an active evidence file.
            continue
    downloads = [validate_isp(p, d, builds) for p, d in files
                 if p.name.startswith('isp_') and d.get('mode') == 'flash']
    swd_failures = [(p, d) for p, d in files if d.get('phase') == 'attach_identify'
                    and any('No device connected' in e.get('message', '') for e in d.get('errors', []))]
    if swd_failures:
        downloads.append({'method': 'SWD', 'status': 'failed', 'target': '',
                          'evidence': [ref(p) for p, _ in swd_failures],
                          'notes': 'ST-Link探针已枚举，但目标连接失败；未进行SWD下载。此项为连接问题，不判定实验程序失败。'})
    else:
        downloads.append({'method': 'SWD', 'status': 'not_tested', 'target': '', 'evidence': [],
                          'notes': '没有可核实的SWD下载成功证据。'})
    probe = {'type': 'ST-Link', 'serial': '01040166524300093638414B', 'target_id': ''}
    diagnostic = evidence_root / 'probe_diagnostic.json'
    if diagnostic.exists():
        probe.update(read_json(diagnostic)); probe['evidence'] = [ref(diagnostic)]
    backup = {'status': 'not_tested', 'note': '只有最早成功double_read_backup可称为本次实验前原固件；preflash是各次下载前快照。'}
    identities = sorted([(p, d) for p, d in files if p.name.startswith('isp_')
                         and d.get('mode') == 'identify-backup' and d.get('status') == 'PASS'],
                        key=lambda x: x[1].get('started_at', ''))
    if identities:
        p, d = identities[0]
        try:
            binary = Path(d['backup'])
            original, meta = backup_valid(binary, d['device']['uid'])
            one, two = flash_from_rx_tail(p.with_suffix('.rx.bin'), reads=2)
            if (meta.get('purpose') != 'double_read_backup' or original != one or one != two
                    or sha(original) != d['backup_sha256']):
                raise ValueError('original double-read backup does not match raw UART data')
            backup.update(status='passed', path=ref(binary), sha256=sha(original),
                          size=len(original), device=d['device'], evidence=[ref(p), ref(binary), ref(p.with_suffix('.rx.bin'))])
            backup['original'] = {'binary': ref(binary), 'metadata': ref(binary.with_suffix('.json')),
                                  'sha256': sha(original), 'size': len(original)}
            backup['notes'] = ['这是本次实验操作前读出的用户原固件；不作“原厂固件”认定。',
                               '各次preflash文件只代表对应下载前的快照，不可替代最早double_read_backup。']
            probe['target_id'] = 'ISP识别PID ' + d['device']['pid'] + '；SWD未连接'
        except (OSError, KeyError, ValueError) as error:
            backup['error'] = str(error)
    by_lab = {n: [] for n in range(1, 9)}
    for p, d in files:
        if p.name.startswith('isp_'):
            continue
        try:
            parsed = serial_record(p, d)
            if parsed:
                n, checks = parsed; by_lab[n].extend(checks)
        except (OSError, KeyError, ValueError, TypeError) as error:
            n = d.get('requested_lab', d.get('lab'))
            if n in by_lab:
                by_lab[n].append(check('串口证据解析', 'not_tested', 'serial', str(error), [ref(p)]))
    manual = {}
    if physical_path:
        for item in read_json(physical_path).get('labs', []):
            approved = []
            for c in item.get('checks', []):
                if (c.get('method') not in ('physical_button', 'visual')
                        or c.get('status') not in ('passed', 'failed', 'not_tested')
                        or not c.get('observed') or not c.get('evidence')):
                    raise ValueError('Physical observation needs explicit method/status/description/evidence')
                approved.append(c)
            manual[item['id']] = approved
    labs = []
    for n in range(1, 9):
        target = next(name for name in builds if name.startswith(f'{n:02d}_'))
        source = ROOT / 'Labs' / target / 'main.c'
        hexpath = ROOT / 'Build' / ('Keil_' + target) / (target + '.hex')
        axfpath = hexpath.with_suffix('.axf')
        build = builds[target]
        build_ok = (build.get('passed') and sha(hexpath.read_bytes()) == build.get('hex_sha256')
                    and sha(axfpath.read_bytes()) == build.get('axf_sha256'))
        checks = [check('Keil重新编译与当前产物哈希', 'passed' if build_ok else 'not_tested',
                        'offline', '0错误、0警告；AXF/HEX与编译记录SHA256一致。' if build_ok else '当前产物与构建记录不一致。', [ref(buildpath), ref(hexpath), ref(axfpath)])]
        matching = [d for d in downloads if d['target'] == target and d['method'] == 'ISP' and d['status'] == 'passed']
        if matching:
            latest = matching[-1]
            checks.append(check('ISP下载及完整64KB回读校验', 'passed', 'isp', latest['notes'], latest['evidence']))
        else:
            checks.append(check('ISP下载及完整64KB回读校验', 'not_tested', 'isp', '没有与当前固件关联的完整有效下载记录。'))
        checks.extend(by_lab[n])
        checks.extend(manual.get(n, []))
        if not any(c['method'] == 'visual' for c in checks):
            checks.append(check('LED实体发光观察', 'not_tested', 'visual', '尚未取得用户明确观察或照片/视频证据；串口报告不能替代。'))
        if n in (4, 5, 8) and not any(c['method'] == 'physical_button' for c in checks):
            checks.append(check('K1/K4实体按键完整操作', 'not_tested', 'physical_button', '无明确实体按键操作证据；未用软件注入代替。'))
        limitations = ['整项实验尚未标记完全通过；需结合本表未完成项现场验收。',
                       'UART中的LED状态来自固件输出寄存器或意图日志，不能证明LED实体发光。']
        if n in (1, 7):
            limitations.append('PC串口时间只能支持粗略时序观察，不证明校准仪器级1 ms精度。')
        if n == 1:
            limitations.append('ST-Link/SWD目标连接未成功，指导书要求的第二种下载方式尚未验收。')
        if n == 8:
            limitations.append('当前实验八使用OLED；串口中文或I2C应答不能替代实体屏幕观察。旧PC显示版本记录不能验证新OLED版本。')
            if not any(c.get('method') == 'visual_oled' for c in checks):
                checks.append(check('OLED启动画面与完整中文显示', 'not_tested', 'visual_oled', '需要本次固件的实体屏幕观察记录。'))
        hardware = any(c['status'] == 'passed' and c['method'] != 'offline' for c in checks)
        labs.append({'id': n, 'target': target, 'source_sha256': sha(source.read_bytes()),
                     'source_file': ref(source), 'hex_sha256': sha(hexpath.read_bytes()),
                     'status': 'partial' if hardware else 'not_tested',
                     'conclusion': '已取得部分真实板上证据，未完成项仍需现场验收。' if hardware else '已编译；尚无足够真实板上证据。',
                     'checks': checks, 'limitations': limitations})
    envpath = Path(r'D:\F103_LabTools\environment_status.json')
    environment = read_json(envpath) if envpath.exists() else {}
    software = []
    for name, path, version in [
        ('Keil uVision', r'D:\Keil5\UV4\UV4.exe', environment.get('keil', '')),
        ('Arm Compiler', r'D:\Keil5\ARM\ARMCLANG\bin\armclang.exe', environment.get('compiler', '')),
        ('STM32F1 DFP', r'D:\Keil5\Keil\STM32F1xx_DFP\2.4.1', environment.get('dfp', '')),
        ('Tera Term', r'D:\TeraTerm\ttermpro.exe', environment.get('teraterm', '')),
        ('Python实验工具环境', r'D:\F411_PythonEnv\Scripts\python.exe', '版本见安装记录'),
        ('CH340驱动', 'Windows已枚举COM4', environment.get('ch340_driver', ''))]:
        exists = Path(path).exists() if name != 'CH340驱动' else bool(identities)
        software.append(dict(name=name, path=path, version=version,
                             status='passed' if exists else 'not_tested', evidence=[ref(envpath)],
                             notes='软件版本源于既有安装记录；本汇总不安装软件、不执行硬件探测。'))
    date_label=(datetime.strptime(evidence_root.name,'%Y%m%d').date().isoformat()
                if re.fullmatch(r'\d{8}',evidence_root.name) else evidence_root.name)
    return {'schema_version': 1, 'date': date_label,
            'generated_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'board': '普中精灵 STM32F103C8T6 完整开发板', 'serial_port': 'COM4', 'probe': probe,
            'overall': {'status': 'partial',
                        'conclusion': '已完成8个工程重新编译；板上ISP及串口证据按实验记录，所有实验整体最多为部分验证。',
                        'limitations': ['物理按键、LED光学观察和校准时序没有证据时均标记未验证。',
                                        'SWD目标连接失败是连接问题，不代表实验程序失败。',
                                        '离线模拟结果不计作板上验证。']},
            'software': software, 'downloads': downloads, 'labs': labs, 'backup': backup,
            'evidence_root': ref(evidence_root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', type=Path,
                        default=ROOT / 'Evidence' / datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--physical-evidence', type=Path)
    args = parser.parse_args()
    output = args.output or args.evidence_root / 'validation_summary.json'
    summary = generate(args.evidence_root, args.physical_evidence)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)
    print('Original backup:', summary['backup']['status'])
    print('ISP downloads:', [(d['target'], d['status']) for d in summary['downloads'] if d['method'] == 'ISP'])
    print('Experiments:', [(d['id'], d['status']) for d in summary['labs']])


if __name__ == '__main__':
    main()
