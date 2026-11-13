#!/usr/bin/env python3
"""
Terry Jar pechat agenti: serverdan OTK cheklarini olib, printerlarga yuboradi.
- LAN printer (host = IP): ESC/POS baytlari 9100-portga yuboriladi.
- USB printer (host = "usb:<nomi>"): shu kompyuterdagi printer navbatiga xom (raw) holda `lp` orqali (macOS / Linux).
Faqat Python standart kutubxonasi (python3.8+), o'rnatish va sudo shart emas.

Sozlama shu papkadagi agent.conf faylida:
    server = https://terryjar.uz
    token  = <superadmin → Chek printerlari sahifasidagi token>

Ishga tushirish:           python3 terryjar_print_agent.py
LAN printerni sinash:      python3 terryjar_print_agent.py --test 192.168.1.51
USB printerni sinash:      python3 terryjar_print_agent.py --test usb:XP80
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
POLL_SECONDS = 2
PRINTER_TIMEOUT = 8


def log(message):
    print(time.strftime('%d.%m %H:%M:%S'), message, flush=True)


def read_config():
    config = {}
    path = os.path.join(HERE, 'agent.conf')
    if not os.path.exists(path):
        sys.exit(f"agent.conf topilmadi: {path}")
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    if not config.get('server') or not config.get('token'):
        sys.exit("agent.conf da server va token bo'lishi kerak")
    config['server'] = config['server'].rstrip('/')
    return config


def api(config, path, body=None):
    request = urllib.request.Request(
        config['server'] + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'X-Agent-Token': config['token'], 'Content-Type': 'application/json'},
        method='POST' if body is not None else 'GET',
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def send_to_printer(host, port, data):
    if host.startswith('usb:'):
        result = subprocess.run(['lp', '-d', host[4:], '-o', 'raw'], input=data, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors='replace').strip() or 'lp xatosi')
        return
    with socket.create_connection((host, int(port)), timeout=PRINTER_TIMEOUT) as sock:
        sock.sendall(data)


def run():
    config = read_config()
    log(f"Agent ishga tushdi: {config['server']}")
    while True:
        try:
            jobs = api(config, '/printers/agent/jobs/').get('jobs', [])
        except urllib.error.HTTPError as exc:
            log(f"Server xatosi {exc.code}" + (" — token noto'g'ri" if exc.code == 403 else ''))
            time.sleep(15)
            continue
        except Exception as exc:   # internet yo'q va h.k. — kutib qayta urinadi
            log(f"Serverga ulanib bo'lmadi: {exc}")
            time.sleep(10)
            continue

        for job in jobs:
            try:
                send_to_printer(job['host'], job['port'], base64.b64decode(job['data']))
                result = {'ok': True}
                log(f"Chiqdi: {job['host']} · {job['title']}")
            except Exception as exc:
                result = {'ok': False, 'error': f"{job['host']}: {exc}"}
                log(f"Printer xatosi: {job['host']} · {exc}")
            try:
                api(config, f"/printers/agent/jobs/{job['id']}/result/", result)
            except Exception as exc:
                log(f"Natijani yuborib bo'lmadi: {exc}")
        time.sleep(POLL_SECONDS if not jobs else 0.2)


def test(host):
    data = (b'\x1b@\x1ba\x01\x1d!\x11TERRY JAR\n\x1d!\x00Printer ishlayapti\n' + host.encode() +
            b'\n\n\n\x1dVB\x00')
    send_to_printer(host, 9100, data)
    print("Yuborildi. Chek chiqdimi?")


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--test':
        test(sys.argv[2])
    else:
        run()
