"""Physical F103 bench evidence. Never injects key inputs or calls target handlers.

Examples (replace the complete probe ID and COM port):
  python bench_swd.py identify-backup --probe FULL_ID
  python bench_swd.py lab --probe FULL_ID --port COM4 --lab 1 --flash
  python bench_swd.py lab --probe FULL_ID --port COM4 --lab 5 --duration 90

The first command only attaches, identifies, halts to read 64 KiB, then resumes.
Only --flash permits sector programming, after a matching UID backup is checked.
Lab mode without --flash verifies the running image and observes without resetting.
All timings are host-timed SWD samples, NOT oscilloscope measurements. Visible LED
operation, physical buttons, and ISP/ST-Link download methods require separate evidence.
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
FLASH_BASE, FLASH_SIZE = 0x08000000, 0x10000
GPIOA = 0x40010800
PACK = r'D:\Keil5\.Download\Keil.STM32F1xx_DFP.2.4.1.pack'
VARIABLES = ('g_millis', 'g_key0_irq_count', 'g_key1_irq_count', 'g_uart_overruns')


def timestamp():
    return datetime.now(TZ).isoformat(timespec='milliseconds')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    # A complete JSON replaces the previous checkpoint atomically.
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def read_flash(target, count=FLASH_SIZE):
    return b''.join(bytes(target.read_memory_block8(FLASH_BASE + offset,
                                                  min(1024, count - offset)))
                    for offset in range(0, count, 1024))


def identify(target):
    code = target.read32(0xE0042000)
    size = target.read16(0x1FFFF7E0)
    words = list(target.read_memory_block32(0x1FFFF7E8, 3))
    return {'idcode': f'0x{code:08X}', 'device_id': code & 0xFFF,
            'revision_id': code >> 16, 'flash_size_kib': size,
            'uid': ''.join(f'{word:08X}' for word in words),
            'uid_words_low_address_first': [f'0x{word:08X}' for word in words]}


def require_identity(identity):
    if identity['device_id'] != 0x410 or identity['flash_size_kib'] != 64:
        raise RuntimeError('Expected STM32F103 medium-density ID 0x410 and exactly 64 KiB; '
                           f'got {identity}. No programming permitted.')
    if identity['uid'] in ('0' * 24, 'F' * 24):
        raise RuntimeError('Invalid all-zero/all-one device UID; no programming permitted.')


def make_backup(target, identity, record, stem):
    directory = ROOT / 'Backup'
    directory.mkdir(exist_ok=True)
    target.halt()
    binary = read_flash(target)
    if read_flash(target) != binary:
        raise RuntimeError('Two halted 64 KiB flash reads disagree; backup rejected.')
    initial_sp = int.from_bytes(binary[:4], 'little')
    reset_vector = int.from_bytes(binary[4:8], 'little')
    if binary in (b'\x00' * FLASH_SIZE, b'\xFF' * FLASH_SIZE):
        raise RuntimeError('Backup is entirely zero/erased, inconsistent with the known running RTC firmware.')
    if not (0x20000004 <= initial_sp <= 0x20005000 and initial_sp % 4 == 0
            and reset_vector & 1 and FLASH_BASE <= (reset_vector & ~1) < FLASH_BASE + FLASH_SIZE):
        raise RuntimeError('Original firmware vector table is invalid for 64 KiB Flash / 20 KiB SRAM; backup rejected.')
    path = directory / f'original_{identity["uid"]}_{stem}.bin'
    with path.open('xb') as stream:
        stream.write(binary)
    metadata = {'created_at': timestamp(), 'actual_hardware': True, **identity,
                'start_address': FLASH_BASE, 'byte_count': len(binary),
                'sha256': sha(binary), 'binary_file': path.name,
                'two_reads_identical': True, 'nonblank': True,
                'initial_sp': f'0x{initial_sp:08X}', 'reset_vector': f'0x{reset_vector:08X}',
                'method': 'SWD attach, halt, two identical read-only 64 KiB flash reads; no flash writes'}
    write_json(path.with_suffix('.json'), metadata)
    record['backup'] = {'file': str(path), **metadata}
    print(f'BACKUP {path}\nSHA256 {metadata["sha256"]}', flush=True)


def require_backup(identity):
    for metadata_path in sorted((ROOT / 'Backup').glob('original_*.json'), reverse=True):
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if metadata.get('uid') != identity['uid']:
                continue
            binary = metadata_path.parent / metadata['binary_file']
            if binary.resolve().parent != (ROOT / 'Backup').resolve():
                continue
            data = binary.read_bytes()
            if (metadata.get('actual_hardware') is True
                    and metadata.get('start_address') == FLASH_BASE
                    and metadata.get('byte_count') == FLASH_SIZE
                    and len(data) == FLASH_SIZE and sha(data) == metadata.get('sha256')):
                return {'metadata_file': str(metadata_path), 'binary_file': str(binary),
                        'uid': identity['uid'], 'sha256': sha(data)}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError('No intact 64 KiB backup for this UID. Run identify-backup first.')


def firmware(number):
    from intelhex import IntelHex
    from elftools.elf.elffile import ELFFile
    lab = next((p for p in (ROOT / 'Labs').iterdir() if p.name.startswith(f'{number:02d}_')), None)
    if lab is None:
        raise RuntimeError(f'Lab {number} directory missing')
    build = ROOT / 'Build' / ('Keil_' + lab.name)
    hex_path, axf_path = build / (lab.name + '.hex'), build / (lab.name + '.axf')
    ih = IntelHex(str(hex_path))
    if ih.minaddr() != FLASH_BASE or not FLASH_BASE <= ih.maxaddr() < FLASH_BASE + FLASH_SIZE:
        raise RuntimeError('HEX must start at 0x08000000 and stay within the 64 KiB application Flash.')
    binary = ih.tobinstr(start=FLASH_BASE, end=ih.maxaddr())
    initial_sp = int.from_bytes(binary[:4], 'little')
    reset_vector = int.from_bytes(binary[4:8], 'little')
    if not (0x20000004 <= initial_sp <= 0x20005000
            and reset_vector & 1 and FLASH_BASE <= (reset_vector & ~1) < FLASH_BASE + len(binary)):
        raise RuntimeError('HEX has an invalid C8 stack pointer or reset vector.')
    with axf_path.open('rb') as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name('.symtab')
        if table is None:
            raise RuntimeError('AXF debug symbols missing; rebuild with debug information.')
        symbols = {s.name: s['st_value'] for s in table.iter_symbols()}
        # Verify every loadable AXF byte in flash against the HEX used for download.
        checked_bytes = 0
        for segment in elf.iter_segments():
            address, size = segment['p_paddr'], segment['p_filesz']
            if segment['p_type'] == 'PT_LOAD' and size and FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
                if address + size > FLASH_BASE + len(binary):
                    raise RuntimeError('AXF load segment extends beyond the HEX image.')
                if segment.data() != binary[address - FLASH_BASE:address - FLASH_BASE + size]:
                    raise RuntimeError('AXF and HEX disagree. Rebuild before hardware verification.')
                checked_bytes += size
        if not checked_bytes:
            raise RuntimeError('No AXF flash load segment found.')
    variables = {name: symbols[name] for name in VARIABLES
                 if name in symbols and 0x20000000 <= symbols[name] <= 0x20004FFC}
    if 'g_millis' not in variables:
        raise RuntimeError('Valid g_millis SRAM symbol missing from AXF.')
    info = {'target': lab.name, 'hex_file': str(hex_path), 'axf_file': str(axf_path),
            'hex_sha256': sha(hex_path.read_bytes()), 'axf_sha256': sha(axf_path.read_bytes()),
            'binary_sha256': sha(binary), 'binary_bytes': len(binary),
            'axf_hex_compared_bytes': checked_bytes, 'variable_addresses': variables}
    return binary, variables, info


def quiet_old_interrupts(target):
    target.halt()
    target.write32(0xE000E010, 0)  # SysTick CTRL.
    count = min(8, (target.read32(0xE000E004) & 15) + 1)  # ICTR INTLINESNUM.
    for i in range(count):
        target.write32(0xE000E180 + 4 * i, 0xFFFFFFFF)  # NVIC ICER.
        target.write32(0xE000E280 + 4 * i, 0xFFFFFFFF)  # NVIC ICPR.
    target.write32(0xE000ED04, (1 << 25) | (1 << 27))  # PENDSTCLR, PENDSVCLR.
    target.flush()


def configuration(target):
    addresses = {'RCC_CR': 0x40021000, 'RCC_CFGR': 0x40021004,
                 'GPIOA_CRL': GPIOA, 'GPIOA_CRH': GPIOA + 4,
                 'AFIO_MAPR': 0x40010004, 'AFIO_EXTICR4': 0x40010014,
                 'EXTI_IMR': 0x40010400, 'EXTI_FTSR': 0x4001040C,
                 'SysTick_CTRL': 0xE000E010, 'SysTick_LOAD': 0xE000E014,
                 'USART1_BRR': 0x40013808, 'USART1_CR1': 0x4001380C}
    return {name: target.read32(address) for name, address in addresses.items()}


class Capture:
    def __init__(self, port, raw_path, serial_path, record):
        self.port, self.record = port, record
        self.raw = raw_path.open('wb')
        self.events = serial_path.open('w', encoding='utf-8')
        self.received = bytearray()
        self.started = time.perf_counter()
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.line = ''

    def elapsed(self):
        return time.perf_counter() - self.started

    def drain(self):
        data = self.port.read(min(self.port.in_waiting, 8192))
        if not data:
            return
        self.received.extend(data)
        self.raw.write(data)
        self.raw.flush()
        text = self.decoder.decode(data)
        event = {'at': timestamp(), 'seconds': round(self.elapsed(), 6),
                 'direction': 'RX', 'hex': data.hex(), 'text': text}
        self.events.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.events.flush()
        self.line += text
        while '\n' in self.line:
            line, self.line = self.line.split('\n', 1)
            print(f'SERIAL +{self.elapsed():.3f}s {line.rstrip()}', flush=True)

    def send(self, data):
        self.port.write(data)
        self.port.flush()
        event = {'at': timestamp(), 'seconds': round(self.elapsed(), 6),
                 'direction': 'TX', 'hex': data.hex(), 'text': data.decode('ascii')}
        self.events.write(json.dumps(event) + '\n')
        self.events.flush()
        self.record['serial_tx'].append(event)
        print(f'TX +{event["seconds"]:.3f}s {data!r}', flush=True)

    def close(self):
        self.record['serial_rx_bytes'] = len(self.received)
        self.record['serial_rx_sha256'] = sha(bytes(self.received))
        self.record['serial_text'] = self.received.decode('utf-8', errors='replace')
        self.raw.close()
        self.events.close()


def sample(target, symbols, capture):
    idr, odr = target.read_memory_block32(GPIOA + 8, 2)
    values = {name: target.read32(address) for name, address in symbols.items()}
    return {'seconds': round(capture.elapsed(), 6), 'GPIOA_IDR': idr, 'GPIOA_ODR': odr,
            'led_odr': [(~odr >> i) & 1 for i in (0, 1)],
            'led_idr': [(~idr >> i) & 1 for i in (0, 1)],
            'keys_down': [(~idr >> i) & 1 for i in (15, 12)], **values}


def collect(target, symbols, capture, args, record, evidence_path):
    samples = record['samples'] = []
    changes = record['changes'] = []
    command_checks = record['command_checks'] = []
    # Invalid commands are tested both with D1 on and off. These are UART inputs,
    # not injected GPIO levels, software interrupt requests, or handler calls.
    commands = [(b'Y', 1, 'OK Y LED0=1'), (b'?', 1, 'STATE'),
                (b'X', 1, None), (b'y', 1, None), (b'\r\n', 1, None),
                (b'N', 0, 'OK N LED0=0'), (b'X', 0, None),
                (b'y', 0, None), (b'\r\n', 0, None), (b'?', 0, 'STATE')]
    previous = None
    command_index, pending, last_checkpoint = 0, None, 0.0
    while capture.elapsed() < args.duration:
        capture.drain()
        item = sample(target, symbols, capture)
        samples.append(item)
        signature = (item['led_odr'], item['led_idr'], item['keys_down'])
        if signature != previous:
            changes.append(item.copy())
            print(f'GPIO +{item["seconds"]:.3f}s D1/D2={item["led_odr"]} '
                  f'pin={item["led_idr"]} K1/K4={item["keys_down"]} '
                  f'ms={item.get("g_millis")} IRQ={item.get("g_key0_irq_count")}/'
                  f'{item.get("g_key1_irq_count")}', flush=True)
            previous = tuple(value[:] for value in signature)
        now = capture.elapsed()
        if now >= 0.35 and 'startup_registers' not in record:
            record['startup_registers'] = configuration(target)
        if args.lab == 6 and now >= 0.5:
            if pending is not None and now >= pending['until']:
                capture.drain()
                reply = bytes(capture.received[pending['offset']:]).decode('utf-8', errors='replace')
                expected = pending['expected']
                response_ok = (pending['reply'] in reply if pending['reply'] else 'OK ' not in reply)
                if pending['data'] == b'?':
                    response_ok = bool(re.search(rf'\bLED0={expected}\b', reply)) and 'STATE' in reply
                check = {'command_hex': pending['data'].hex(),
                         'command': repr(pending['data']), 'reply': reply,
                         'expected_D1': expected, 'sample': item,
                         'passed': item['led_odr'][0] == expected
                                   and item['led_idr'][0] == expected and response_ok}
                command_checks.append(check)
                print('UART CHECK', 'PASS' if check['passed'] else 'FAIL', check['command'], flush=True)
                pending = None
            if pending is None and command_index < len(commands):
                data, expected, reply = commands[command_index]
                pending = {'data': data, 'expected': expected, 'reply': reply,
                           'offset': len(capture.received), 'until': capture.elapsed() + 0.25}
                capture.send(data)
                command_index += 1
        if now - last_checkpoint >= 1.0:
            record['elapsed_seconds'] = round(now, 3)
            write_json(evidence_path, record)
            last_checkpoint = now
        time.sleep(0.008)
    capture.drain()
    record['elapsed_seconds'] = round(capture.elapsed(), 3)
    record['serial_text'] = capture.received.decode('utf-8', errors='replace')
    record['command_sequence_complete'] = command_index == len(commands) and pending is None if args.lab == 6 else None


def assess(args, record):
    samples = [s for s in record['samples'] if s['seconds'] >= 0.4]
    checks = record['checks'] = []
    config = record.get('startup_registers', {})
    cfgr, cr = config.get('RCC_CFGR', 0), config.get('RCC_CR', 0)
    clock_ok = (cfgr & 0xC) == 8 and (cfgr & (1 << 16)) != 0 and (cfgr >> 18) & 15 == 7
    clock_ok = clock_ok and (cfgr >> 4) & 15 == 0 and (cr & 0x02020000) == 0x02020000
    checks.append({'name': 'HSE_PLL_72MHz_configuration', 'passed': bool(clock_ok),
                   'note': 'Register configuration, not an independently measured crystal frequency.'})
    checks.append({'name': 'SysTick_1ms_configuration',
                   'passed': config.get('SysTick_LOAD') == 71999 and config.get('SysTick_CTRL', 0) & 7 == 7})
    checks.append({'name': 'USART1_115200_configuration', 'passed': config.get('USART1_BRR') == 625})
    if args.flash:
        checks.append({'name': 'fresh_startup_banner',
                       'passed': f'| LAB {args.lab} |' in record['serial_text']})
    if not samples:
        checks.append({'name': 'sampling_duration', 'passed': False})
    if args.lab in (1, 2, 3, 7) and samples:
        edges = []
        previous = samples[0]['led_odr'][0]
        for item in samples[1:]:
            if item['led_odr'][0] != previous:
                edges.append(item['seconds'])
                previous = item['led_odr'][0]
        intervals = [b - a for a, b in zip(edges, edges[1:])]
        gaps = [b['seconds'] - a['seconds'] for a, b in zip(samples, samples[1:])]
        expected = 0.5 if args.lab in (1, 2) else 1.0
        max_gap = max(gaps, default=float('inf'))
        checks.append({'name': 'coarse_LED_period', 'expected_seconds': expected,
                       'interval_seconds': intervals, 'edge_seconds': edges,
                       'largest_sample_gap_seconds': max_gap,
                       'passed': len(intervals) >= 3 and max_gap < 0.15
                                 and all(abs(v - expected) <= 0.15 for v in intervals),
                       'method': 'Host-clock SWD sampling with 150 ms acceptance tolerance; not instrument precision.'})
        mismatches = sum(s['led_odr'] != s['led_idr'] for s in samples)
        checks.append({'name': 'LED_output_and_input_readback_agree',
                       'mismatching_samples': mismatches, 'sample_count': len(samples),
                       'passed': mismatches / len(samples) <= 0.02,
                       'note': 'Small transition-read skew allowed; this does not observe emitted light.'})
        if args.lab == 2:
            checks.append({'name': 'two_LEDs_mutually_exclusive',
                           'passed': all(s['led_odr'] in ([1, 0], [0, 1]) for s in samples)})
    if args.lab == 6:
        checks.append({'name': 'UART_command_sequence',
                       'passed': record['command_sequence_complete']
                                 and all(x['passed'] for x in record['command_checks'])})
    if args.lab in (4, 5, 8):
        text = record['serial_text']
        record['physical_input_observations'] = {
            'K1_low_samples': sum(s['keys_down'][0] for s in samples),
            'K4_low_samples': sum(s['keys_down'][1] for s in samples),
            'Chinese_message_count': text.count('嵌入式实验完成啦'),
            'Chinese_message_scope': 'Auxiliary UART text only; not OLED pixel or visual verification.',
            'blink_on_count': text.count('BLINK ON'), 'blink_off_count': text.count('BLINK OFF'),
            'first_sample': samples[0] if samples else None,
            'last_sample': samples[-1] if samples else None,
            'conclusion': 'Only real input samples were collected. Review actual button actions and visible LEDs; no automatic experiment PASS.'}
    if args.lab == 8:
        record['visible_OLED_validation'] = 'USER_OBSERVATION_REQUIRED'
    passed = all(item['passed'] for item in checks)
    record['automated_checks_passed'] = passed
    record['overall_experiment_passed'] = None
    record['status'] = ('CAPTURED_MANUAL_REVIEW_REQUIRED' if args.lab in (4, 5, 8)
                        else 'AUTOMATED_ELECTRICAL_CHECKS_PASSED') if passed else 'CHECK_FAILED'
    return 0 if passed else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('identify-backup', 'lab'))
    parser.add_argument('--probe', required=True, help='Complete, exact probe unique ID; no substring selection.')
    parser.add_argument('--port', help='Explicit Windows serial port, for example COM4; required for lab mode.')
    parser.add_argument('--lab', type=int, choices=range(1, 9))
    parser.add_argument('--flash', action='store_true', help='Program the selected lab, after UID backup validation.')
    parser.add_argument('--duration', type=float, default=7.0, help='Capture seconds, default 7; use 60-120 for physical keys.')
    parser.add_argument('--pack', default=PACK)
    parser.add_argument('--frequency', type=int, default=500000, help='SWD frequency in Hz, default 500000.')
    args = parser.parse_args()
    if args.mode == 'lab' and (not args.port or args.lab is None):
        parser.error('lab requires --port and --lab')
    if args.mode == 'identify-backup' and (args.flash or args.lab is not None):
        parser.error('identify-backup does not accept --flash or --lab')
    if args.duration <= 0 or not 10000 <= args.frequency <= 1000000:
        parser.error('duration must be positive; frequency must be 10000..1000000 Hz')

    directory = ROOT / 'Evidence' / datetime.now(TZ).strftime('%Y%m%d')
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f') + '_' + args.mode
    if args.lab:
        stem += f'_{args.lab:02d}'
    evidence = directory / (stem + '.json')
    raw_path, serial_path = directory / (stem + '.serial.raw'), directory / (stem + '.serial.jsonl')
    record = {'started_at': timestamp(), 'timezone': 'UTC+08:00', 'mode': args.mode,
              'status': 'STARTED', 'actual_hardware': False, 'probe_requested': args.probe,
              'target': 'stm32f103c8', 'pack': args.pack, 'port': args.port,
              'lab': args.lab, 'flash_requested': args.flash,
              'flash_write_attempted': False, 'flash_readback_verified': False,
              'serial_tx': [], 'errors': [], 'cleanup_errors': [],
              'evidence_file': str(evidence), 'serial_raw_file': str(raw_path),
              'serial_timestamped_file': str(serial_path),
              'limitations': ['No simulated GPIO, IRQ injection, or target handler calls.',
                              'SWD output/input registers do not establish visible LED illumination.',
                              'Host-timed samples do not establish oscilloscope-level 1 ms precision.',
                              'Physical key operation and ISP/ST-Link method acceptance need explicit evidence.']}
    write_json(evidence, record)
    session = target = port = capture = None
    code = 1
    try:
        import serial
        from pyocd.core.helpers import ConnectHelper
        from pyocd.core.session import Session
        from pyocd.core.target import Target
        from pyocd.flash.file_programmer import FileProgrammer

        if not Path(args.pack).is_file():
            raise FileNotFoundError(args.pack)
        binary = symbols = None
        if args.mode == 'lab':
            binary, symbols, record['firmware'] = firmware(args.lab)
        record['phase'] = 'probe_selection'
        probes = ConnectHelper.get_all_connected_probes(blocking=False)
        chosen = [p for p in probes if p.unique_id == args.probe]
        if len(chosen) != 1:
            raise RuntimeError(f'Expected one exact probe ID match; got {len(chosen)}. '
                               f'Available IDs: {[p.unique_id for p in probes]}')
        options = {'pack': args.pack, 'frequency': args.frequency, 'connect_mode': 'attach',
                   'auto_unlock': False, 'pack.debug_sequences.enable': False,
                   'cache.enable_memory': False, 'cache.enable_register': False,
                   'resume_on_disconnect': True, 'hide_programming_progress': True,
                   'chip_erase': 'sector', 'reset_type': 'sysresetreq'}
        session = Session(chosen[0], target_override='stm32f103c8', options=options)
        record['phase'] = 'attach_identify'
        session.open()
        target = session.target
        record['actual_hardware'] = True
        identity = record['identity'] = identify(target)
        require_identity(identity)
        print('IDENTITY', json.dumps(identity), flush=True)
        write_json(evidence, record)
        if args.mode == 'identify-backup':
            record['phase'] = 'read_only_backup'
            make_backup(target, identity, record, stem)
            record['status'] = 'IDENTIFIED_AND_BACKED_UP'
            code = 0
        else:
            if args.flash:
                record['matching_backup'] = require_backup(identity)
            # Set both control-line states before opening the CH340 serial handle.
            record['phase'] = 'open_serial'
            port = serial.Serial(port=None, baudrate=115200, bytesize=8, parity='N',
                                 stopbits=1, timeout=0, write_timeout=2,
                                 xonxoff=False, rtscts=False, dsrdtr=False)
            port.dtr = False
            port.rts = False
            port.port = args.port
            port.open()
            if args.flash:
                record['phase'] = 'sector_program'
                quiet_old_interrupts(target)
                binary_path = directory / (stem + '.bin')
                binary_path.write_bytes(binary)
                record['programmed_binary'] = str(binary_path)
                record['flash_write_attempted'] = True
                write_json(evidence, record)
                FileProgrammer(session, chip_erase='sector', smart_flash=False,
                               trust_crc=False, keep_unwritten=True).program(
                                   str(binary_path), file_format='bin', base_address=FLASH_BASE)
                target.halt()
            record['phase'] = 'verify_flash_image'
            readback = read_flash(target, len(binary))
            record['flash_readback_sha256'] = sha(readback)
            if readback != binary:
                raise RuntimeError('Flash readback differs from the selected HEX. '
                                   'Without --flash this can mean another lab is currently loaded.')
            record['flash_readback_verified'] = True
            port.reset_input_buffer()
            capture = Capture(port, raw_path, serial_path, record)
            if args.flash:
                record['phase'] = 'sysresetreq_resume'
                target.reset(Target.ResetType.SYSRESETREQ)
            target.resume()
            record['phase'] = 'capture_real_inputs'
            print(f'CAPTURE lab {args.lab} for {args.duration:g}s; physical K1/K4 only. '
                  'Use Ctrl+C to stop and preserve evidence.', flush=True)
            collect(target, symbols, capture, args, record, evidence)
            code = assess(args, record)
    except KeyboardInterrupt:
        record['status'] = 'INTERRUPTED_PARTIAL_EVIDENCE'
        record['errors'].append({'at': timestamp(), 'type': 'KeyboardInterrupt'})
        code = 130
    except Exception as exc:
        record['status'] = 'ERROR'
        record['errors'].append({'at': timestamp(), 'type': type(exc).__name__,
                                 'message': str(exc), 'traceback': traceback.format_exc()})
        print(f'ERROR: {exc}', file=sys.stderr, flush=True)
    finally:
        if capture is not None:
            try:
                capture.drain()
            except Exception as exc:
                record['cleanup_errors'].append('serial drain: ' + str(exc))
            try:
                capture.close()
            except Exception as exc:
                record['cleanup_errors'].append('serial capture close: ' + str(exc))
        if port is not None:
            try:
                port.close()
            except Exception as exc:
                record['cleanup_errors'].append('serial close: ' + str(exc))
        if target is not None:
            try:
                target.resume()
            except Exception as exc:
                record['cleanup_errors'].append('target resume: ' + str(exc))
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                record['cleanup_errors'].append('session close: ' + str(exc))
        record['finished_at'] = timestamp()
        record['exit_code'] = code
        write_json(evidence, record)
        print(f'{record["status"]}\nEVIDENCE {evidence}', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
