"""Capture real F103 UART evidence, without reset, download, SWD, or key injection.

  python bench_serial.py --lab 1 --port COM4 --duration 8
  python bench_serial.py --lab 5 --port COM4 --duration 90

Labs 1/4/5/8 query '?' every 200 ms; labs 2/3/7 only listen. Lab 6 sends
Y/N/invalid characters and queries the firmware-reported state. Serial LED values
are firmware reports of ODR, not independent electrical or visible-light readings.
Lab 2/3 output is a hard-coded intent log. Physical key experiments never receive
an automatic experiment PASS. Host arrival times are only coarse timing evidence.
"""
from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
STATE = re.compile(r'^STATE ms=(\d+) LED0=([01]) LED1=([01]) KEY0=([01]) '
                   r'KEY1=([01]) IRQ0=(\d+) IRQ1=(\d+) RX_ERRORS=(\d+)$')
FIELDS = ('ms', 'LED0', 'LED1', 'KEY0', 'KEY1', 'IRQ0', 'IRQ1', 'RX_ERRORS')
COMMANDS = [(b'Y', 1, 'OK Y LED0=1'), (b'?', 1, None), (b'X', 1, None),
            (b'y', 1, None), (b'\r\n', 1, None), (b'N', 0, 'OK N LED0=0'),
            (b'X', 0, None), (b'y', 0, None), (b'\r\n', 0, None), (b'?', 0, None)]


def stamp():
    return datetime.now(TZ).isoformat(timespec='milliseconds')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, record):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def state_from_line(line):
    match = STATE.fullmatch(line.strip())
    return dict(zip(FIELDS, map(int, match.groups()))) if match else None


class SerialEvidence:
    def __init__(self, port, raw_path, events_path, record):
        self.port, self.record = port, record
        self.raw = raw_path.open('wb')
        self.events = events_path.open('w', encoding='utf-8')
        self.start = time.perf_counter()
        self.received = bytearray()
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.pending_text = ''

    def elapsed(self):
        return time.perf_counter() - self.start

    def log(self, direction, data, text):
        event = {'at': stamp(), 'seconds': round(self.elapsed(), 6),
                 'direction': direction, 'hex': data.hex(), 'text': text}
        self.events.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.events.flush()
        return event

    def drain(self):
        data = self.port.read(min(self.port.in_waiting, 16384))
        if not data:
            return
        self.received.extend(data)
        self.raw.write(data)
        self.raw.flush()
        text = self.decoder.decode(data)
        event = self.log('RX', data, text)
        self.record['actual_hardware_serial_data_received'] = True
        self.pending_text += text
        while '\n' in self.pending_text:
            line, self.pending_text = self.pending_text.split('\n', 1)
            line = line.rstrip('\r')
            item = {'at': event['at'], 'seconds': event['seconds'], 'text': line}
            self.record['lines'].append(item)
            parsed = state_from_line(line)
            if parsed is not None:
                self.record['reported_states'].append({**item, **parsed})
            print(f'RX +{event["seconds"]:.3f}s {line}', flush=True)

    def send(self, data):
        count = self.port.write(data)
        self.port.flush()
        if count != len(data):
            raise RuntimeError('Incomplete serial write')
        event = self.log('TX', data, data.decode('ascii'))
        self.record['transmissions'].append(event)
        print(f'TX +{event["seconds"]:.3f}s {data!r}', flush=True)

    def close(self):
        self.record['serial_rx_bytes'] = len(self.received)
        self.record['serial_rx_sha256'] = digest(bytes(self.received))
        self.record['serial_text'] = self.received.decode('utf-8', errors='replace')
        self.record['trailing_partial_line'] = self.pending_text
        self.record['elapsed_seconds'] = round(self.elapsed(), 3)
        self.raw.close()
        self.events.close()


def capture(c, args, record, path):
    next_query, next_checkpoint, command_index, pending = 0.0, 1.0, 0, None
    while c.elapsed() < args.duration:
        c.drain()
        now = c.elapsed()
        if args.lab in (1, 4, 5, 8) and now >= next_query:
            c.send(b'?')
            next_query = now + 0.2
        if args.lab == 6 and now >= 0.3:
            if pending is not None and now >= pending['until']:
                c.drain()
                reply = bytes(c.received[pending['offset']:]).decode('utf-8', errors='replace')
                states = [s for line in reply.splitlines() if (s := state_from_line(line))]
                expected = pending['expected']
                response_ok = pending['ack'] in reply if pending['ack'] else 'OK ' not in reply
                state_ok = bool(states) and all(s['LED0'] == expected for s in states)
                result = {'command_hex': pending['command'].hex(),
                          'command': repr(pending['command']), 'reply': reply,
                          'expected_firmware_reported_LED0': expected,
                          'reported_states': states, 'passed': response_ok and state_ok,
                          'scope': 'Actual UART replies and firmware ODR report only; no direct pin/LED measurement.'}
                record['command_checks'].append(result)
                print('SERIAL CHECK', 'PASS' if result['passed'] else 'FAIL', result['command'], flush=True)
                pending = None
            if pending is None and command_index < len(COMMANDS):
                command, expected, ack = COMMANDS[command_index]
                pending = {'command': command, 'expected': expected, 'ack': ack,
                           'offset': len(c.received), 'until': c.elapsed() + 0.3}
                # Appending the documented query does not modify GPIO. It gives
                # an ODR report after every tested command, including ignored bytes.
                c.send(command if command == b'?' else command + b'?')
                command_index += 1
        if now >= next_checkpoint:
            record['elapsed_seconds'] = round(now, 3)
            save(path, record)
            next_checkpoint = now + 1.0
        time.sleep(0.008)
    c.drain()
    record['command_sequence_complete'] = command_index == len(COMMANDS) and pending is None if args.lab == 6 else None


def summarize(args, record):
    states, lines = record['reported_states'], record['lines']
    checks = record['checks'] = []
    text = '\n'.join(line['text'] for line in lines)
    checks.append({'name': 'UART_response_received', 'passed': bool(lines)})
    observed_labs = sorted(set(map(int, re.findall(r'\| LAB (\d+) \|', text))))
    record['startup_lab_numbers_observed'] = observed_labs
    if observed_labs:
        checks.append({'name': 'received_banner_matches_requested_lab',
                       'passed': observed_labs == [args.lab]})
    record['firmware_identity_limitation'] = (
        'No reset, Flash readback, UID read or hash verification is performed by this script. '
        'Connect this evidence to the separate ISP/SWD download record. A startup banner may be missed.')
    if args.lab in (1, 4, 5, 7, 8):
        checks.append({'name': 'structured_firmware_state_received', 'passed': bool(states)})
        if states:
            checks.append({'name': 'UART_error_counter_not_increasing',
                           'passed': states[-1]['RX_ERRORS'] == states[0]['RX_ERRORS'],
                           'first': states[0]['RX_ERRORS'], 'last': states[-1]['RX_ERRORS']})
    if args.lab in (1, 2, 3, 7):
        if args.lab in (1, 7):
            sequence = [(s['seconds'], s['LED0'], s['ms']) for s in states]
        elif args.lab == 2:
            sequence = [(s['seconds'], int(s['text'][5]), None) for s in lines
                        if s['text'] in ('LED0=1 LED1=0', 'LED0=0 LED1=1')]
        else:
            sequence = [(s['seconds'], int(s['text'] == 'ON'), None) for s in lines
                        if s['text'] in ('ON', 'OFF')]
        changes = []
        for item in sequence:
            if not changes or item[1] != changes[-1][1]:
                changes.append(item)
        intervals = [b[0] - a[0] for a, b in zip(changes[1:], changes[2:])]
        expected = 0.5 if args.lab in (1, 2) else 1.0
        average = sum(intervals) / len(intervals) if intervals else None
        checks.append({'name': 'coarse_serial_report_alternation',
                       'expected_seconds': expected, 'host_intervals_seconds': intervals,
                       'host_mean_seconds': average, 'reported_changes': changes,
                       'passed': len(intervals) >= 3 and abs(average - expected) <= 0.2,
                       'scope': ('Hard-coded intent log, no independent GPIO evidence.' if args.lab in (2, 3)
                                 else 'Firmware ODR report, no independent GPIO or light measurement.'),
                       'timing_limit': 'PC serial arrivals / 200 ms polling are coarse; not calibrated instrument timing.'})
        if args.lab == 7:
            deltas = [(b['ms'] - a['ms']) & 0xFFFFFFFF for a, b in zip(states, states[1:])]
            checks.append({'name': 'firmware_reported_1000ms_steps', 'deltas_ms': deltas,
                           'passed': len(deltas) >= 3 and all(abs(v - 1000) <= 2 for v in deltas),
                           'scope': 'Same-device software clock; cannot independently prove physical 1 ms precision.'})
    if args.lab == 6:
        checks.append({'name': 'real_UART_command_replies_and_ODR_reports',
                       'passed': record['command_sequence_complete']
                                 and all(item['passed'] for item in record['command_checks'])})
    if args.lab in (4, 5, 8):
        record['manual_review'] = {
            'first_reported_state': states[0] if states else None,
            'last_reported_state': states[-1] if states else None,
            'K1_reported_down_samples': sum(s['KEY0'] for s in states),
            'K4_reported_down_samples': sum(s['KEY1'] for s in states),
            'Chinese_message_count': text.count('嵌入式实验完成啦'),
            'Chinese_message_scope': 'Auxiliary UART text only; not OLED pixel or visual verification.',
            'blink_on_count': text.count('BLINK ON'), 'blink_off_count': text.count('BLINK OFF'),
            'required': 'Review real K1/K4 operations, debounce/hold behavior and visible D1/D2. '
                        'A short physical press can fall between 200 ms queries; absence of a down sample is not proof of no press.'}
    if args.lab == 8:
        record['visible_OLED_validation'] = 'USER_OBSERVATION_REQUIRED'
        record['manual_review']['OLED_required'] = 'Observe startup, BLINK state and all eight Chinese glyphs on the physical OLED.'
    success = all(item['passed'] for item in checks)
    record['serial_checks_passed'] = success
    record['overall_experiment_passed'] = None
    record['electrical_pin_validation'] = 'NOT_PERFORMED'
    record['visible_LED_validation'] = 'USER_OBSERVATION_REQUIRED'
    record['status'] = ('CAPTURED_MANUAL_REVIEW_REQUIRED' if args.lab in (4, 5, 8)
                        else 'SERIAL_REPORT_CHECKS_PASSED') if success else 'SERIAL_CHECK_FAILED'
    return 0 if success else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lab', type=int, required=True, choices=range(1, 9))
    parser.add_argument('--port', required=True)
    parser.add_argument('--duration', type=float, default=8.0)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('--duration must be positive')
    directory = ROOT / 'Evidence' / datetime.now(TZ).strftime('%Y%m%d')
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f') + f'_serial_lab{args.lab:02d}'
    path = directory / (stem + '.json')
    raw_path, event_path = directory / (stem + '.serial.raw'), directory / (stem + '.serial.jsonl')
    record = {'started_at': stamp(), 'timezone': 'UTC+08:00', 'status': 'STARTED',
              'requested_lab': args.lab, 'port': args.port, 'duration_seconds': args.duration,
              'serial_settings': '115200 8N1, no flow control, DTR=False and RTS=False before open',
              'actual_hardware': False, 'actual_hardware_serial_data_received': False,
              'reset_performed': False, 'download_performed': False, 'GPIO_or_IRQ_injection': False,
              'evidence_file': str(path), 'raw_serial_file': str(raw_path),
              'timestamped_serial_file': str(event_path),
              'script_sha256': digest(Path(__file__).read_bytes()),
              'lines': [], 'reported_states': [], 'transmissions': [], 'command_checks': [],
              'errors': [], 'cleanup_errors': [],
              'limitations': ['LED state in STATE is firmware ODR readback, not direct pin measurement.',
                              'Lab 2/3 logs report intended actions and cannot prove electrical outputs.',
                              'Physical LED illumination requires user observation.',
                              'Host-clock timing is coarse; no claim of calibrated 1 ms accuracy.']}
    local = next((p for p in (ROOT / 'Labs').iterdir() if p.name.startswith(f'{args.lab:02d}_')), None)
    if local:
        record['local_artifacts_not_device_readback'] = {}
        for extension in ('hex', 'axf'):
            artifact = ROOT / 'Build' / ('Keil_' + local.name) / (local.name + '.' + extension)
            if artifact.exists():
                record['local_artifacts_not_device_readback'][extension] = {
                    'file': str(artifact), 'sha256': digest(artifact.read_bytes())}
    save(path, record)
    port = collector = None
    result = 1
    try:
        import serial
        port = serial.Serial(port=None, baudrate=115200, bytesize=8, parity='N', stopbits=1,
                             timeout=0, write_timeout=2, xonxoff=False, rtscts=False, dsrdtr=False)
        port.dtr = False
        port.rts = False
        port.port = args.port
        port.open()
        record['actual_hardware'] = True
        collector = SerialEvidence(port, raw_path, event_path, record)
        print(f'LAB {args.lab}: UART capture only, {args.duration:g} seconds; '
              'no reset, no download, no simulated keys.', flush=True)
        capture(collector, args, record, path)
        result = summarize(args, record)
    except KeyboardInterrupt:
        record['status'] = 'INTERRUPTED_PARTIAL_EVIDENCE'
        record['errors'].append({'at': stamp(), 'type': 'KeyboardInterrupt'})
        result = 130
    except Exception as error:
        record['status'] = 'ERROR'
        record['errors'].append({'at': stamp(), 'type': type(error).__name__,
                                 'message': str(error), 'traceback': traceback.format_exc()})
        print(f'ERROR {error}', file=sys.stderr, flush=True)
    finally:
        if collector is not None:
            try:
                collector.drain()
            except Exception as error:
                record['cleanup_errors'].append('serial drain: ' + str(error))
            try:
                collector.close()
            except Exception as error:
                record['cleanup_errors'].append('evidence close: ' + str(error))
        if port is not None:
            try:
                port.close()
            except Exception as error:
                record['cleanup_errors'].append('port close: ' + str(error))
        record['finished_at'] = stamp()
        record['exit_code'] = result
        save(path, record)
        print(record['status'], '\nEVIDENCE', path, flush=True)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
