"""Conservative STM32F103C8 UART ROM bootloader bench tool (AN3155).

No serial port is opened by --self-test. Hardware access is explicit:
  --port COM4 --identify-backup
  --port COM4 --lab 1 --flash

Only the latter may erase/write Flash. Neither mode changes option bytes,
protection, or firmware permissions. The pin sequence assumes the complete
Puzhong Jingling board's CH340/PNP automatic reset circuit, not a bare core.
Sources: AN3155; AN2606 STM32F10xxx section; Puzhong chapter 4:
https://blog.csdn.net/2501_92850798/article/details/149832332
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

import serial
from intelhex import IntelHex

ROOT = Path(__file__).resolve().parents[1]
FLASH_START, FLASH_SIZE, PAGE_SIZE = 0x08000000, 0x10000, 1024
ACK, NACK = 0x79, 0x1F
CHINA = timezone(timedelta(hours=8))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def xor_bytes(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


def checked_address(address):
    data = struct.pack('>I', address)
    return data + bytes([xor_bytes(data)])


def validate_original_flash(data):
    """The connected board was observed running RTC firmware; blank is unexpected."""
    if len(data) != FLASH_SIZE or data in (bytes(FLASH_SIZE), b'\xFF' * FLASH_SIZE):
        raise RuntimeError('Original Flash read is incomplete/blank/all-zero; backup rejected')
    sp, reset = struct.unpack('<II', data[:8])
    if not (0x20000000 < sp <= 0x20005000 and sp % 4 == 0 and reset & 1
            and FLASH_START <= (reset & ~1) < FLASH_START + FLASH_SIZE):
        raise RuntimeError(f'Original firmware vectors invalid: SP=0x{sp:08X}, reset=0x{reset:08X}')


class Evidence:
    def __init__(self, mode, port):
        now = datetime.now(CHINA)
        directory = ROOT / 'Evidence' / now.strftime('%Y%m%d')
        directory.mkdir(parents=True, exist_ok=True)
        self.stem = directory / ('isp_' + now.strftime('%Y%m%d_%H%M%S_%f'))
        self.log = self.stem.with_suffix('.serial.jsonl').open('w', encoding='utf-8')
        self.tx = self.stem.with_suffix('.tx.bin').open('wb')
        self.rx = self.stem.with_suffix('.rx.bin').open('wb')
        self.result = {'started_at': now.isoformat(), 'mode': mode, 'port': port,
                       'status': 'RUNNING', 'flash_writes_started': False,
                       'protocol': 'STM32 AN3155 UART 115200 8E1',
                       'serial_log': str(self.stem.with_suffix('.serial.jsonl'))}

    def event(self, event, data=None):
        record = {'time': datetime.now(CHINA).isoformat(), 'event': event}
        if isinstance(data, bytes):
            record['hex'] = data.hex()
            (self.tx if event == 'TX' else self.rx).write(data)
        elif data is not None:
            record['detail'] = data
        self.log.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.log.flush()

    def close(self):
        self.result['ended_at'] = datetime.now(CHINA).isoformat()
        self.stem.with_suffix('.json').write_text(
            json.dumps(self.result, ensure_ascii=False, indent=2), encoding='utf-8')
        self.log.close()
        self.tx.close()
        self.rx.close()
        print('Evidence:', self.stem.with_suffix('.json'), flush=True)


class Bootloader:
    def __init__(self, link, evidence, timeout=2.0):
        self.link, self.evidence, self.timeout = link, evidence, timeout
        self.commands = set()

    def send(self, data):
        data = bytes(data)
        self.evidence.event('TX', data)
        if self.link.write(data) != len(data):
            raise RuntimeError('Short serial write; stop protocol transaction')
        self.link.flush()

    def receive(self, count, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        data = bytearray()
        while len(data) < count and time.monotonic() < deadline:
            chunk = self.link.read(count - len(data))
            if chunk:
                self.evidence.event('RX', chunk)
                data.extend(chunk)
        if len(data) != count:
            raise TimeoutError(f'Expected {count} bytes, received {len(data)}: {data.hex()}')
        return bytes(data)

    def ack(self, timeout=None):
        value = self.receive(1, timeout)[0]
        if value != ACK:
            raise RuntimeError('Bootloader NACK; transaction stopped' if value == NACK
                               else f'Unexpected ACK byte 0x{value:02X}; transaction stopped')

    def command(self, code):
        if self.commands and code not in self.commands:
            raise RuntimeError(f'Command 0x{code:02X} is not advertised by target')
        self.send([code, code ^ 0xFF])
        self.ack()

    def identify(self):
        self.send([0x7F])
        self.ack()
        self.command(0x00)
        count = self.receive(1)[0] + 1
        if count < 2 or count > 64:
            raise RuntimeError(f'Invalid Get response length {count}')
        response = self.receive(count)
        self.ack()
        version = response[0]
        self.commands = set(response[1:])
        if not {0x00, 0x02, 0x11}.issubset(self.commands):
            raise RuntimeError('Target does not advertise required read-only commands')
        self.command(0x02)
        count = self.receive(1)[0] + 1
        if count != 2:
            raise RuntimeError(f'Unsupported Get ID length {count}')
        pid = int.from_bytes(self.receive(count), 'big')
        self.ack()
        if pid != 0x0410:
            raise RuntimeError(f'Expected STM32F10xx medium density PID 0x0410; got 0x{pid:04X}')
        flash_kib = int.from_bytes(self.read_memory(0x1FFFF7E0, 2), 'little')
        if flash_kib != 64:
            raise RuntimeError(f'Expected documented C8 Flash size 64 KiB; target reports {flash_kib}')
        uid = self.read_memory(0x1FFFF7E8, 12)
        if uid in (bytes(12), b'\xFF' * 12):
            raise RuntimeError('Invalid all-zero/all-FF unique device identifier')
        return {'pid': f'0x{pid:04X}', 'bootloader_version': f'{version >> 4}.{version & 15}',
                'supported_commands': [f'0x{x:02X}' for x in sorted(self.commands)],
                'flash_kib': flash_kib, 'uid': uid.hex()}

    def read_memory(self, address, length):
        if not 1 <= length <= 256:
            raise ValueError('Read length must be 1..256 bytes')
        self.command(0x11)
        self.send(checked_address(address))
        self.ack()
        self.send([length - 1, (length - 1) ^ 0xFF])
        self.ack()
        return self.receive(length)

    def read_flash(self, label):
        data = bytearray()
        for offset in range(0, FLASH_SIZE, 256):
            data.extend(self.read_memory(FLASH_START + offset, 256))
            if len(data) % 8192 == 0:
                print(f'{label}: {len(data)}/{FLASH_SIZE} bytes', flush=True)
        return bytes(data)

    def erase_page(self, page):
        if not 0 <= page < FLASH_SIZE // PAGE_SIZE:
            raise ValueError('Page outside documented 64 KiB Flash')
        if 0x43 in self.commands:
            self.command(0x43)
            # One page: N=0, page index, XOR(N, page index).
            self.send([0, page, page])
        elif 0x44 in self.commands:
            self.command(0x44)
            data = b'\x00\x00' + page.to_bytes(2, 'big')
            self.send(data + bytes([xor_bytes(data)]))
        else:
            raise RuntimeError('Neither page erase command is advertised')
        self.ack(timeout=10.0)

    def write_memory(self, address, data):
        data = bytes(data)
        if not (FLASH_START <= address and address + len(data) <= FLASH_START + FLASH_SIZE):
            raise ValueError('Writing outside documented Flash is forbidden')
        if address % 4 or not 4 <= len(data) <= 256 or len(data) % 4:
            raise ValueError('Flash writes require 4-byte aligned address and length, <=256')
        self.command(0x31)
        self.send(checked_address(address))
        self.ack()
        payload = bytes([len(data) - 1]) + data
        self.send(payload + bytes([xor_bytes(payload)]))
        self.ack(timeout=5.0)


def enter_rom(link, evidence):
    # CH340 # outputs are active low: True asserts the physical low level.
    # DTR# low activates reset pull-down; RTS# low activates BOOT0 high-side PNP.
    evidence.event('ENTER_ROM', {'dtr_asserted': True, 'rts_asserted': True})
    link.dtr = True
    link.rts = True
    time.sleep(0.1)
    link.dtr = False
    time.sleep(0.1)
    link.reset_input_buffer()


def restore_flash_boot(link, evidence):
    evidence.event('RESTORE_FLASH_BOOT')
    link.dtr = True
    link.rts = False
    time.sleep(0.1)
    link.dtr = False
    time.sleep(0.1)


def save_backup(data, device, purpose):
    validate_original_flash(data)
    folder = ROOT / 'Backup' / ('ISP_' + device['uid'])
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CHINA).strftime('%Y%m%d_%H%M%S_%f')
    binary = folder / f'{stamp}_{purpose}_08000000.bin'
    binary.write_bytes(data)
    metadata = {**device, 'created_at': datetime.now(CHINA).isoformat(),
                'address': FLASH_START, 'length': len(data), 'sha256': digest(data),
                'binary': binary.name, 'purpose': purpose}
    binary.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    # Check bytes on disk, not only the buffer used to write the file.
    if digest(binary.read_bytes()) != metadata['sha256']:
        raise RuntimeError('Backup disk verification failed')
    return str(binary)


def require_matching_backup(device):
    folder = ROOT / 'Backup' / ('ISP_' + device['uid'])
    for path in sorted(folder.glob('*.json'), reverse=True):
        try:
            metadata = json.loads(path.read_text(encoding='utf-8'))
            name = metadata['binary']
            if Path(name).name != name:
                continue
            binary = path.parent / name
            data = binary.read_bytes()
            validate_original_flash(data)
            if (metadata['uid'] == device['uid'] and metadata['pid'] == '0x0410'
                    and metadata['flash_kib'] == 64 and metadata['address'] == FLASH_START
                    and metadata['length'] == FLASH_SIZE and len(data) == FLASH_SIZE
                    and digest(data) == metadata['sha256']):
                return str(binary)
        except (OSError, KeyError, TypeError, ValueError, RuntimeError):
            continue
    raise RuntimeError('No valid 64 KiB backup for this UID. Run --identify-backup first.')


def load_lab(lab):
    targets = sorted(p.name for p in (ROOT / 'Labs').iterdir() if (p / 'main.c').is_file())
    matches = [name for name in targets if name.startswith(f'{lab:02d}_')]
    if len(matches) != 1:
        raise RuntimeError(f'Cannot resolve lab {lab}')
    target = matches[0]
    path = ROOT / 'Build' / ('Keil_' + target) / (target + '.hex')
    records = json.loads((ROOT / 'Build' / 'keil_results.json').read_text(encoding='utf-8'))
    record = next((r for r in records if r.get('target') == target), {})
    actual_hash = digest(path.read_bytes())
    if not record.get('passed') or record.get('hex_sha256') != actual_hash:
        raise RuntimeError('HEX does not match a passing Keil build record; rebuild first')
    image = IntelHex(str(path))
    addresses = image.addresses()
    if not addresses or min(addresses) != FLASH_START or max(addresses) >= FLASH_START + FLASH_SIZE:
        raise RuntimeError('HEX is empty, relocated, or extends beyond 64 KiB Flash')
    address_set = set(addresses)
    if not all(FLASH_START + offset in address_set for offset in range(8)):
        raise RuntimeError('HEX has no complete initial vector table')
    vectors = bytes(image[FLASH_START + offset] for offset in range(8))
    sp, reset = struct.unpack('<II', vectors)
    if not (0x20000000 < sp <= 0x20005000 and sp % 8 == 0
            and reset & 1 and FLASH_START <= (reset & ~1) <= max(addresses)):
        raise RuntimeError('Invalid Cortex-M3 stack pointer or reset vector')
    return target, path, image, actual_hash


def merged_flash(before, image):
    if len(before) != FLASH_SIZE:
        raise ValueError('Need complete pre-flash image for read-modify-write')
    after = bytearray(before)
    for address in image.addresses():
        if not FLASH_START <= address < FLASH_START + FLASH_SIZE:
            raise ValueError('HEX address outside permitted Flash')
        after[address - FLASH_START] = image[address]
    return bytes(after)


def self_test():
    """Offline wire-format and preservation tests. No Serial instance is created."""
    class Sink:
        def event(self, *args):
            pass

    class Fake:
        def __init__(self, rx):
            self.rx, self.tx = bytearray(rx), []

        def read(self, n):
            chunk = bytes(self.rx[:n]); del self.rx[:n]
            return chunk

        def write(self, data):
            self.tx.append(data)
            return len(data)

        def flush(self):
            pass

    assert checked_address(FLASH_START) == bytes.fromhex('0800000008')
    link = Fake(b'\x79\x79\x79' + bytes(range(256)))
    boot = Bootloader(link, Sink())
    assert boot.read_memory(FLASH_START, 256) == bytes(range(256))
    assert link.tx == [bytes.fromhex('11ee'), bytes.fromhex('0800000008'), bytes.fromhex('ff00')]
    for erase, packet in [(0x43, bytes.fromhex('003f3f')), (0x44, bytes.fromhex('0000003f3f'))]:
        link = Fake(b'\x79\x79')
        boot = Bootloader(link, Sink()); boot.commands = {erase}
        boot.erase_page(63)
        assert link.tx == [bytes([erase, erase ^ 255]), packet]
    link = Fake(b'\x79\x79\x79')
    boot = Bootloader(link, Sink()); boot.commands = {0x31}
    boot.write_memory(FLASH_START, bytes(range(256)))
    assert len(link.tx[-1]) == 258 and xor_bytes(link.tx[-1]) == 0
    assert link.tx[-1][0] == 255
    try:
        boot.write_memory(0x1FFFF800, b'\xFF' * 4)
        raise AssertionError('Option bytes write accepted')
    except ValueError:
        pass
    image = IntelHex(); image[FLASH_START + 3] = 42; image[FLASH_START + 1026] = 99
    before = b'\xA5' * FLASH_SIZE
    after = merged_flash(before, image)
    assert after[3] == 42 and after[1026] == 99
    assert sum(a != b for a, b in zip(before, after)) == 2
    for bad in (bytes(FLASH_SIZE), b'\xFF' * FLASH_SIZE, b'\xA5' * FLASH_SIZE):
        try:
            validate_original_flash(bad)
            raise AssertionError('Invalid original backup accepted')
        except RuntimeError:
            pass
    good = struct.pack('<II', 0x20005000, FLASH_START + 0x101) + bytes(FLASH_SIZE - 8)
    validate_original_flash(good)
    try:
        Bootloader(Fake(b'\x1F'), Sink()).command(0x11)
        raise AssertionError('NACK accepted')
    except RuntimeError:
        pass
    print('PASS: offline AN3155 packet/checksum, NACK, Flash bounds and RMW preservation tests')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--identify-backup', action='store_true')
    mode.add_argument('--flash', action='store_true')
    mode.add_argument('--self-test', action='store_true')
    parser.add_argument('--lab', type=int, choices=range(1, 9))
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not args.port:
        parser.error('--port is required for hardware access')
    if args.flash != (args.lab is not None):
        parser.error('--flash and --lab must be specified together')
    # Validate the firmware before opening or resetting hardware.
    lab_info = load_lab(args.lab) if args.flash else None
    evidence = Evidence('flash' if args.flash else 'identify-backup', args.port)
    link = None
    exit_code = 1
    try:
        link = serial.Serial(port=None, baudrate=115200, bytesize=serial.EIGHTBITS,
                             parity=serial.PARITY_EVEN, stopbits=serial.STOPBITS_ONE,
                             timeout=0.1, write_timeout=2, rtscts=False, dsrdtr=False)
        link.dtr = False
        link.rts = False
        link.port = args.port
        link.open()
        enter_rom(link, evidence)
        boot = Bootloader(link, evidence)
        device = boot.identify()
        evidence.result['device'] = device
        print(json.dumps(device, indent=2), flush=True)
        if args.identify_backup:
            first = boot.read_flash('Backup read 1')
            second = boot.read_flash('Backup read 2')
            if first != second:
                raise RuntimeError('Repeated complete Flash reads disagree; no verified backup saved')
            path = save_backup(first, device, 'double_read_backup')
            evidence.result.update(backup=path, backup_sha256=digest(first),
                                   verification='two_complete_reads_identical')
        else:
            target, path, image, hex_hash = lab_info
            evidence.result.update(target=target, hex_file=str(path), hex_sha256=hex_hash,
                                   prerequisite_backup=require_matching_backup(device))
            if 0x31 not in boot.commands or not ({0x43, 0x44} & boot.commands):
                raise RuntimeError('Target lacks required Flash write/page erase commands')
            before = boot.read_flash('Preserve current Flash')
            evidence.result['preflash_backup'] = save_backup(before, device, 'preflash_' + target)
            expected = merged_flash(before, image)
            pages = [page for page in range(FLASH_SIZE // PAGE_SIZE)
                     if before[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
                     != expected[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
            evidence.result['pages_to_program'] = pages
            evidence.result['pages_programmed'] = []
            for page in pages:
                print(f'Programming page {page} ({pages.index(page) + 1}/{len(pages)})', flush=True)
                evidence.result['flash_writes_started'] = True
                boot.erase_page(page)
                for offset in range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE, 256):
                    boot.write_memory(FLASH_START + offset, expected[offset:offset + 256])
                evidence.result['pages_programmed'].append(page)
            actual = boot.read_flash('Verify complete Flash')
            if actual != expected:
                evidence.result['readback_sha256'] = digest(actual)
                evidence.result['expected_sha256'] = digest(expected)
                raise RuntimeError('Full 64 KiB readback differs from expected RMW image')
            evidence.result.update(readback_sha256=digest(actual),
                                   verification='complete_64k_readback_matches_expected_RMW_image')
        evidence.result['status'] = 'PASS'
        exit_code = 0
    except (Exception, KeyboardInterrupt) as error:
        evidence.result.update(status='FAIL', error=f'{type(error).__name__}: {error}')
        print('STOP:', error, file=sys.stderr, flush=True)
    finally:
        if link is not None and link.is_open:
            try:
                restore_flash_boot(link, evidence)
                evidence.result['flash_boot_reset'] = True
            except Exception as error:
                evidence.result['reset_error'] = f'{type(error).__name__}: {error}'
                evidence.result['status'] = 'FAIL'
                exit_code = 1
            finally:
                link.close()
        evidence.close()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
