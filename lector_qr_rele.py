#!/usr/bin/env python3
"""
Lector de QR + Relé - ENTRADA y SALIDA (Waveshare RPi Relay Board, 3 canales)
--------------------------------------------------------------------------------
Soporta DOS lectores QR simultáneos (por ejemplo, torno de entrada y torno
de salida), cada uno identificado por su puerto USB físico (no por número
de evento, que puede cambiar) y cada uno activando su propio relé.

Pines de la Waveshare RPi Relay Board (BCM):
    CH1 -> GPIO26
    CH2 -> GPIO20   <- usado por ENTRADA
    CH3 -> GPIO21   <- usado por SALIDA
El relé se activa poniendo el pin en HIGH.

Compatible con Raspberry Pi 5 (usa gpiozero en vez de RPi.GPIO).

Requisitos:
    sudo apt install python3-evdev python3-gpiozero

Uso:
    sudo python3 lector_qr_rele.py \\
        --entrada /dev/input/by-path/platform-xhci-hcd.1-usb-0:1:1.0-event-kbd \\
        --salida  /dev/input/by-path/platform-xhci-hcd.0-usb-0:1:1.0-event-kbd

También se puede usar con un solo lector:
    sudo python3 lector_qr_rele.py --entrada /dev/input/by-path/...
"""

import sys
import os
import socket
import time
import threading
from datetime import datetime

from gpiozero import OutputDevice
from evdev import InputDevice, categorize, ecodes

# ---------- Watchdog de systemd ----------
NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")


def _notificar_systemd(mensaje: str):
    if not NOTIFY_SOCKET:
        return
    try:
        addr = NOTIFY_SOCKET
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(addr)
        sock.sendall(mensaje.encode())
        sock.close()
    except Exception as e:
        print(f"[watchdog] No se pudo notificar a systemd: {e}")


def iniciar_latido_watchdog(hilos, estados, intervalo_segundos=10, tolerancia_caido_segundos=60):
    """Manda WATCHDOG=1 a systemd solo si el sistema está realmente sano:
    - Todos los hilos lectores siguen vivos (no han crasheado).
    - Ningún lector lleva "caído" (desconectado / sin abrir) más de
      `tolerancia_caido_segundos`. Una desconexión breve es normal
      (alguien tira del cable un segundo); una desconexión larga significa
      que algo va mal de verdad y conviene que systemd reinicie todo.
    """
    tiempo_caido_desde = {nombre: None for nombre in estados}

    def latido():
        while True:
            sano = True

            for h in hilos:
                if not h.is_alive():
                    print(f"[watchdog] Hilo '{h.name}' ha muerto. No se envía latido.")
                    sano = False

            ahora = time.time()
            for nombre, estado in estados.items():
                if estado["ok"]:
                    tiempo_caido_desde[nombre] = None
                else:
                    if tiempo_caido_desde[nombre] is None:
                        tiempo_caido_desde[nombre] = ahora
                    caido_desde_hace = ahora - tiempo_caido_desde[nombre]
                    if caido_desde_hace > tolerancia_caido_segundos:
                        print(f"[watchdog] Lector '{nombre}' lleva caído {int(caido_desde_hace)}s. No se envía latido.")
                        sano = False

            if sano:
                _notificar_systemd("WATCHDOG=1")

            time.sleep(intervalo_segundos)

    threading.Thread(target=latido, daemon=True).start()


# ---------- Configuración de relés ----------
SEGUNDOS_ACTIVADO = 2  # tiempo que permanece activado cada relé tras un escaneo

PIN_RELE_ENTRADA = 20  # CH2
PIN_RELE_SALIDA = 21   # CH3

rele_entrada = OutputDevice(PIN_RELE_ENTRADA, active_high=True, initial_value=False)
rele_salida = OutputDevice(PIN_RELE_SALIDA, active_high=True, initial_value=False)


def activar_rele(rele, nombre_canal):
    """Activa un relé unos segundos en un hilo aparte, para no bloquear la lectura."""
    def tarea():
        rele.on()
        print(f"   -> Relé {nombre_canal} ACTIVADO ({SEGUNDOS_ACTIVADO}s)")
        time.sleep(SEGUNDOS_ACTIVADO)
        rele.off()
        print(f"   -> Relé {nombre_canal} desactivado")

    threading.Thread(target=tarea, daemon=True).start()


# ---------- Mapeo de teclas (con soporte de Shift) ----------
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


def escuchar_lector(ruta_dispositivo, nombre_canal, rele, estado):
    """Escucha un lector QR en su propio hilo y activa el relé indicado
    cada vez que detecta un código completo.

    Si el dispositivo se desconecta o falla, NO mata el hilo: reintenta
    reconectar cada pocos segundos indefinidamente. `estado` es un dict
    compartido donde vamos marcando si el lector está actualmente OK o
    caído, para que el watchdog pueda consultarlo.
    """
    ESPERA_RECONEXION = 5  # segundos entre intentos si el lector no está disponible

    while True:
        try:
            dev = InputDevice(ruta_dispositivo)
        except Exception as e:
            if estado["ok"]:
                print(f"[{nombre_canal}] ERROR: no se pudo abrir {ruta_dispositivo}: {e}")
            estado["ok"] = False
            time.sleep(ESPERA_RECONEXION)
            continue

        print(f"[{nombre_canal}] Escuchando en: {dev.name} ({ruta_dispositivo})")
        estado["ok"] = True

        buffer = ""
        contador = 0
        shift_activo = False

        try:
            for evento in dev.read_loop():
                estado["ok"] = True  # seguimos recibiendo eventos con normalidad
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
                                contador += 1
                                hora = datetime.now().strftime("%H:%M:%S")
                                print(f"[{nombre_canal}] [{hora}] QR #{contador} -> {buffer}")
                                activar_rele(rele, nombre_canal)
                                buffer = ""
                        elif codigo_tecla in MAPA_TECLAS:
                            normal, con_shift = MAPA_TECLAS[codigo_tecla]
                            buffer += con_shift if shift_activo else normal
                        else:
                            print(f"[{nombre_canal}] [aviso] Tecla no mapeada: {codigo_tecla}")
        except Exception as e:
            # El dispositivo se desconectó o dejó de responder a mitad de lectura
            print(f"[{nombre_canal}] Lector desconectado ({e}). Reintentando en {ESPERA_RECONEXION}s...")
            estado["ok"] = False
            time.sleep(ESPERA_RECONEXION)
            # el bucle while True vuelve a intentar abrir el dispositivo


def main():
    ruta_entrada = None
    ruta_salida = None

    if "--entrada" in sys.argv:
        ruta_entrada = sys.argv[sys.argv.index("--entrada") + 1]
    if "--salida" in sys.argv:
        ruta_salida = sys.argv[sys.argv.index("--salida") + 1]

    if not ruta_entrada and not ruta_salida:
        print("Debes indicar al menos un lector, por ejemplo:")
        print("  sudo python3 lector_qr_rele.py --entrada /dev/input/by-path/... --salida /dev/input/by-path/...")
        sys.exit(1)

    print("=" * 60)
    print(" Sistema de lectores QR + relés iniciado")
    print(" (Ctrl+C para salir)")
    print("=" * 60)

    hilos = []
    estados = {}

    if ruta_entrada:
        estados["ENTRADA"] = {"ok": False}
        h = threading.Thread(
            target=escuchar_lector,
            args=(ruta_entrada, "ENTRADA", rele_entrada, estados["ENTRADA"]),
            name="lector-ENTRADA",
            daemon=True,
        )
        h.start()
        hilos.append(h)

    if ruta_salida:
        estados["SALIDA"] = {"ok": False}
        h = threading.Thread(
            target=escuchar_lector,
            args=(ruta_salida, "SALIDA", rele_salida, estados["SALIDA"]),
            name="lector-SALIDA",
            daemon=True,
        )
        h.start()
        hilos.append(h)

    _notificar_systemd("READY=1")
    iniciar_latido_watchdog(hilos, estados)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nPrograma finalizado por el usuario (Ctrl+C)")
    finally:
        rele_entrada.off()
        rele_salida.off()
        rele_entrada.close()
        rele_salida.close()
        print("GPIO limpiado correctamente.")


if __name__ == "__main__":
    main()
