#!/usr/bin/env python3
"""
lector_qr_debug.py — versión de PRUEBAS
-----------------------------------------
Igual que lector_qr_rele.py pero muestra por FIFO la respuesta completa
de la API para facilitar la depuración. NO usar en producción.

Para ver la salida en tiempo real:
    tail -f /tmp/torno_debug

Para ejecutar (sin systemd, a mano):
    sudo python3 lector_qr_debug.py \\
        --entrada /dev/input/by-path/platform-xhci-hcd.1-usb-0:1:1.0-event-kbd \\
        --salida  /dev/input/by-path/platform-xhci-hcd.0-usb-0:1:1.0-event-kbd
"""

import sys
import os
import json
import socket
import time
import threading
import urllib.request
import urllib.error
import base64
from datetime import datetime

from gpiozero import OutputDevice
from evdev import InputDevice, categorize, ecodes

# ---------- FIFO de debug ----------
FIFO_PATH = "/tmp/torno_debug"
_fifo_lock = threading.Lock()


def iniciar_fifo():
    if not os.path.exists(FIFO_PATH):
        os.mkfifo(FIFO_PATH)
    print(f"FIFO listo. En otra terminal ejecuta: tail -f {FIFO_PATH}")


def log(mensaje):
    """Escribe en el FIFO y también en stdout (para ver en la terminal donde corre el script)."""
    linea = f"{datetime.now().strftime('%H:%M:%S')} {mensaje}\n"
    print(linea, end="", flush=True)
    try:
        fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        with _fifo_lock:
            os.write(fd, linea.encode())
            os.close(fd)
    except OSError:
        pass


# ---------- Configuración de la API ----------
API_CONFIG_FILE = "/home/jesus/torno_qr/acceso_config.json"


def cargar_config_api():
    with open(API_CONFIG_FILE) as f:
        return json.load(f)


def validar_qr_con_api_debug(codigo_qr, config_api):
    """Llama a la API y muestra la respuesta completa por FIFO."""
    log(f"→ Llamando a API: {config_api['url']}")
    log(f"→ Código QR: {codigo_qr}")

    try:
        datos = json.dumps({"Codigo": codigo_qr}).encode("utf-8")
        credenciales = base64.b64encode(
            f"{config_api['usuario']}:{config_api['password']}".encode()
        ).decode()

        req = urllib.request.Request(
            config_api["url"],
            data=datos,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {credenciales}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=config_api.get("timeout_segundos", 5)) as resp:
            status = resp.status
            respuesta_raw = resp.read().decode("utf-8")

        log(f"← HTTP {status}")
        log(f"← Respuesta raw: {respuesta_raw}")

        respuesta = json.loads(respuesta_raw)
        log(f"← IsOk: {respuesta.get('IsOk')}")

        data = respuesta.get("Data", {})
        log(f"← TieneAcceso: {data.get('TieneAcceso')}")
        log(f"← Data completa: {json.dumps(data, ensure_ascii=False)}")

        if not respuesta.get("IsOk"):
            log(f"✗ API devolvió IsOk=False | Error: {respuesta.get('Error', 'sin detalle')}")
            return False

        tiene_acceso = data.get("TieneAcceso", False)
        if tiene_acceso:
            log("✓ ACCESO PERMITIDO")
        else:
            log("✗ ACCESO DENEGADO")
        return tiene_acceso

    except urllib.error.HTTPError as e:
        log(f"✗ HTTP Error {e.code}: {e.reason}")
        body = e.read().decode("utf-8", errors="replace")
        log(f"✗ Respuesta del servidor: {body}")
        return False
    except urllib.error.URLError as e:
        log(f"✗ Error de red: {e.reason}")
        return False
    except Exception as e:
        log(f"✗ Error inesperado: {type(e).__name__}: {e}")
        return False


# ---------- Configuración de relés ----------
PIN_RELE_ENTRADA = 20
PIN_RELE_SALIDA = 21
SEGUNDOS_ACTIVADO = 2

rele_entrada = OutputDevice(PIN_RELE_ENTRADA, active_high=True, initial_value=False)
rele_salida = OutputDevice(PIN_RELE_SALIDA, active_high=True, initial_value=False)


def activar_rele(rele, nombre_canal):
    def tarea():
        rele.on()
        log(f"[{nombre_canal}] Relé ACTIVADO ({SEGUNDOS_ACTIVADO}s)")
        time.sleep(SEGUNDOS_ACTIVADO)
        rele.off()
        log(f"[{nombre_canal}] Relé desactivado")
    threading.Thread(target=tarea, daemon=True).start()


# ---------- Mapeo de teclas ----------
MAPA_TECLAS = {
    'KEY_A': ('a', 'A'), 'KEY_B': ('b', 'B'), 'KEY_C': ('c', 'C'),
    'KEY_D': ('d', 'D'), 'KEY_E': ('e', 'E'), 'KEY_F': ('f', 'F'),
    'KEY_G': ('g', 'G'), 'KEY_H': ('h', 'H'), 'KEY_I': ('i', 'I'),
    'KEY_J': ('j', 'J'), 'KEY_K': ('k', 'K'), 'KEY_L': ('l', 'L'),
    'KEY_M': ('m', 'M'), 'KEY_N': ('n', 'N'), 'KEY_O': ('o', 'O'),
    'KEY_P': ('p', 'P'), 'KEY_Q': ('q', 'Q'), 'KEY_R': ('r', 'R'),
    'KEY_S': ('s', 'S'), 'KEY_T': ('t', 'T'), 'KEY_U': ('u', 'U'),
    'KEY_V': ('v', 'V'), 'KEY_W': ('w', 'W'), 'KEY_X': ('x', 'X'),
    'KEY_Y': ('y', 'Y'), 'KEY_Z': ('z', 'Z'),
    'KEY_0': ('0', ')'), 'KEY_1': ('1', '!'), 'KEY_2': ('2', '@'),
    'KEY_3': ('3', '#'), 'KEY_4': ('4', '$'), 'KEY_5': ('5', '%'),
    'KEY_6': ('6', '^'), 'KEY_7': ('7', '&'), 'KEY_8': ('8', '*'),
    'KEY_9': ('9', '('),
    'KEY_MINUS': ('-', '_'), 'KEY_EQUAL': ('=', '+'),
    'KEY_SLASH': ('/', '?'), 'KEY_DOT': ('.', '>'), 'KEY_COMMA': (',', '<'),
    'KEY_SEMICOLON': (';', ':'), 'KEY_APOSTROPHE': ("'", '"'),
    'KEY_LEFTBRACE': ('[', '{'), 'KEY_RIGHTBRACE': (']', '}'),
    'KEY_BACKSLASH': ('\\', '|'), 'KEY_GRAVE': ('`', '~'),
    'KEY_SPACE': (' ', ' '),
}
TECLAS_SHIFT = ('KEY_LEFTSHIFT', 'KEY_RIGHTSHIFT')


def escuchar_lector(ruta_dispositivo, nombre_canal, rele):
    while True:
        try:
            dev = InputDevice(ruta_dispositivo)
        except Exception as e:
            log(f"[{nombre_canal}] No se pudo abrir {ruta_dispositivo}: {e}. Reintentando...")
            time.sleep(5)
            continue

        log(f"[{nombre_canal}] Escuchando en: {dev.name}")
        buffer = ""
        shift_activo = False

        try:
            for evento in dev.read_loop():
                if evento.type == ecodes.EV_KEY:
                    tecla = categorize(evento)
                    codigo_tecla = tecla.keycode
                    if isinstance(codigo_tecla, list):
                        codigo_tecla = codigo_tecla[0]

                    if codigo_tecla in TECLAS_SHIFT:
                        shift_activo = tecla.keystate in (tecla.key_down, tecla.key_hold)
                        continue

                    if tecla.keystate == tecla.key_down:
                        if codigo_tecla == 'KEY_ENTER':
                            if buffer:
                                log(f"\n[{nombre_canal}] ── Nuevo escaneo ──────────────────")
                                config_api = cargar_config_api()
                                tiene_acceso = validar_qr_con_api_debug(buffer, config_api)
                                if tiene_acceso:
                                    activar_rele(rele, nombre_canal)
                                buffer = ""
                        elif codigo_tecla in MAPA_TECLAS:
                            normal, con_shift = MAPA_TECLAS[codigo_tecla]
                            buffer += con_shift if shift_activo else normal
        except Exception as e:
            log(f"[{nombre_canal}] Lector desconectado ({e}). Reintentando...")
            time.sleep(5)


def main():
    iniciar_fifo()
    log("=== MODO DEBUG - lector_qr_debug.py ===")
    log(f"Monitoriza con: tail -f {FIFO_PATH}")

    ruta_entrada = None
    ruta_salida = None

    if "--entrada" in sys.argv:
        ruta_entrada = sys.argv[sys.argv.index("--entrada") + 1]
    if "--salida" in sys.argv:
        ruta_salida = sys.argv[sys.argv.index("--salida") + 1]

    if not ruta_entrada and not ruta_salida:
        print("Uso: sudo python3 lector_qr_debug.py --entrada /dev/input/... --salida /dev/input/...")
        sys.exit(1)

    hilos = []
    if ruta_entrada:
        h = threading.Thread(target=escuchar_lector, args=(ruta_entrada, "ENTRADA", rele_entrada), daemon=True)
        h.start()
        hilos.append(h)
    if ruta_salida:
        h = threading.Thread(target=escuchar_lector, args=(ruta_salida, "SALIDA", rele_salida), daemon=True)
        h.start()
        hilos.append(h)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Debug finalizado.")
    finally:
        rele_entrada.off()
        rele_salida.off()
        rele_entrada.close()
        rele_salida.close()


if __name__ == "__main__":
    main()
