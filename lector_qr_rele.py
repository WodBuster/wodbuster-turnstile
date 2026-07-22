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
import json
import socket
import time
import threading
import signal
import urllib.request
import urllib.error
import urllib.parse
import base64
import re
import tempfile
from datetime import datetime

# FIFO (pipe con nombre) para ver accesos en tiempo real por SSH.
# No guarda nada en disco — las líneas se muestran y desaparecen.
# Para leer: cat /tmp/torno_live  (en otra sesión SSH)
FIFO_PATH = "/tmp/torno_live"
_fifo_lock = threading.Lock()


def iniciar_fifo():
    """Crea el FIFO si no existe."""
    if not os.path.exists(FIFO_PATH):
        os.mkfifo(FIFO_PATH)


def log_live(mensaje):
    """Escribe en el FIFO de forma no bloqueante.
    Si nadie está leyendo, descarta el mensaje (no bloquea el programa)."""
    try:
        linea = f"{datetime.now().strftime('%H:%M:%S')} {mensaje}\n"
        # O_NONBLOCK: si no hay nadie leyendo, falla silenciosamente
        fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        with _fifo_lock:
            os.write(fd, linea.encode())
            os.close(fd)
    except OSError:
        pass  # nadie está leyendo, descartamos el mensaje

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


def iniciar_latido_watchdog(hilos, estado_torno, intervalo_segundos=10, limite_operacion_segundos=20):
    """Manda WATCHDOG=1 solo mientras el programa puede atender accesos.

    Un lector USB sin eventos puede estar perfectamente sano, por lo que una
    desconexión no fuerza un reinicio de la Pi. En cambio, si un hilo muere o
    una validación queda bloqueada más allá de su límite, dejamos que systemd
    reinicie el proceso de forma controlada.
    """

    def latido():
        while True:
            sano = True

            for h in hilos:
                if not h.is_alive():
                    print(f"[watchdog] Hilo '{h.name}' ha muerto. No se envía latido.")
                    sano = False

            with estado_torno["lock"]:
                inicio = estado_torno["operacion_iniciada"]
                sentido = estado_torno["sentido"]
            if inicio is not None:
                duracion = time.monotonic() - inicio
                if duracion > limite_operacion_segundos:
                    print(f"[watchdog] Operación de '{sentido}' bloqueada durante {int(duracion)}s. No se envía latido.")
                    sano = False

            if sano:
                _notificar_systemd("WATCHDOG=1")

            time.sleep(intervalo_segundos)

    threading.Thread(target=latido, daemon=True).start()


# ---------- Configuración de la API ----------
API_CONFIG_FILE = "/home/jesus/torno_qr/acceso_config.json"
CONFIG_QR_SCHEME = "wbconfig"
CONFIG_QR_HOST = "configure"
CODIGO_PRUEBA_CONFIG = "config"
RESULTADO_CONFIG_VALIDA = "valida"
RESULTADO_CONFIG_401 = "401"
RESULTADO_CONFIG_INDETERMINADO = "indeterminado"
PATRON_BOX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")


def cargar_config_api():
    """Lee una configuración existente; nunca crea credenciales de ejemplo."""
    if not os.path.exists(API_CONFIG_FILE):
        return None
    if (os.stat(API_CONFIG_FILE).st_mode & 0o777) != 0o600:
        os.chmod(API_CONFIG_FILE, 0o600)
    with open(API_CONFIG_FILE) as f:
        config = json.load(f)
    for clave in ("url", "usuario", "password"):
        if not isinstance(config.get(clave), str) or not config[clave]:
            raise ValueError(f"Configuración API inválida: falta {clave}")
    return config


def guardar_config_api(config):
    """Guarda credenciales validadas de forma atómica y con permisos 0600."""
    directorio = os.path.dirname(API_CONFIG_FILE)
    fd, ruta_temporal = tempfile.mkstemp(prefix=".acceso_config_", dir=directorio)
    try:
        os.chmod(ruta_temporal, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(ruta_temporal, API_CONFIG_FILE)
        os.chmod(API_CONFIG_FILE, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(ruta_temporal)
        except OSError:
            pass
        raise


def _crear_peticion_api(codigo, config_api):
    datos = json.dumps({"Codigo": codigo}).encode("utf-8")
    credenciales = base64.b64encode(
        f"{config_api['usuario']}:{config_api['password']}".encode()
    ).decode()
    return urllib.request.Request(
        config_api["url"],
        data=datos,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credenciales}",
        },
        method="POST",
    )


def comprobar_config_api(config_api):
    """Hace una lectura normal 'config' y distingue únicamente el HTTP 401."""
    try:
        req = _crear_peticion_api(CODIGO_PRUEBA_CONFIG, config_api)
        with urllib.request.urlopen(
            req, timeout=config_api.get("timeout_segundos", 5)
        ) as resp:
            resp.read()
            return (
                RESULTADO_CONFIG_VALIDA
                if 200 <= resp.status < 300
                else RESULTADO_CONFIG_INDETERMINADO
            )
    except urllib.error.HTTPError as e:
        return RESULTADO_CONFIG_401 if e.code == 401 else RESULTADO_CONFIG_INDETERMINADO
    except Exception:
        return RESULTADO_CONFIG_INDETERMINADO


def _parametro_unico(parametros, nombre, obligatorio=True):
    valores = parametros.get(nombre, [])
    if len(valores) > 1 or (obligatorio and len(valores) != 1):
        raise ValueError(f"Parámetro de configuración inválido: {nombre}")
    return valores[0] if valores else None


def config_desde_qr(codigo_qr):
    """Convierte un wbconfig:// en configuración sin permitir una URL arbitraria."""
    uri = urllib.parse.urlsplit(codigo_qr)
    if uri.scheme.lower() != CONFIG_QR_SCHEME or uri.netloc.lower() != CONFIG_QR_HOST:
        raise ValueError("QR de configuración no reconocido")
    parametros = urllib.parse.parse_qs(uri.query, keep_blank_values=True)
    permitidos = {"v", "box", "usuario", "pwd", "password"}
    if set(parametros) - permitidos:
        raise ValueError("El QR contiene parámetros no permitidos")
    if _parametro_unico(parametros, "v") != "1":
        raise ValueError("Versión de configuración no soportada")

    box = _parametro_unico(parametros, "box").strip()
    usuario = (_parametro_unico(parametros, "usuario", obligatorio=False) or box).strip()
    password = _parametro_unico(parametros, "pwd", obligatorio=False)
    password_largo = _parametro_unico(parametros, "password", obligatorio=False)
    if password is not None and password_largo is not None:
        raise ValueError("La contraseña está duplicada")
    password = password if password is not None else password_largo

    if not PATRON_BOX.fullmatch(box):
        raise ValueError("Código de box inválido")
    if not usuario or ":" in usuario or len(usuario) > 128:
        raise ValueError("Usuario API inválido")
    if not password or len(password) > 512:
        raise ValueError("Contraseña API inválida")

    return {
        "url": f"https://{box.lower()}.wodbuster.com/api/acceso",
        "usuario": usuario,
        "password": password,
        "timeout_segundos": 5,
    }


def procesar_qr_configuracion(codigo_qr):
    """Valida y, solo si procede, sustituye atómicamente la configuración."""
    try:
        candidata = config_desde_qr(codigo_qr)
    except ValueError:
        return False

    if os.path.exists(API_CONFIG_FILE):
        try:
            actual = cargar_config_api()
        except Exception:
            # Un fichero presente pero corrupto no equivale a una instalación nueva.
            return False
        if comprobar_config_api(actual) != RESULTADO_CONFIG_401:
            return False
        if comprobar_config_api(actual) != RESULTADO_CONFIG_401:
            return False

    # Esta lectura normal deja en el servidor la traza Codigo="config"
    # asociada al usuario que se va a configurar. TieneAcceso puede ser False.
    if comprobar_config_api(candidata) != RESULTADO_CONFIG_VALIDA:
        return False
    guardar_config_api(candidata)
    return True


def validar_qr_con_api(codigo_qr, config_api):
    """Llama a la API de WodBuster para validar el código QR.

    Devuelve True si tiene acceso, False en cualquier otro caso
    (acceso denegado, error de red, timeout...) — política fail closed.
    """
    try:
        req = _crear_peticion_api(codigo_qr, config_api)

        with urllib.request.urlopen(req, timeout=config_api.get("timeout_segundos", 5)) as resp:
            respuesta = json.loads(resp.read().decode("utf-8"))

        if not respuesta.get("IsOk"):
            return False

        return respuesta.get("Data", {}).get("TieneAcceso", False)

    except Exception:
        return False  # fail closed: cualquier error = no abrir



SEGUNDOS_ACTIVADO = 2  # tiempo que permanece activado cada relé tras un escaneo
# Un lector QR tipo teclado envía todos los caracteres de una lectura en pocos
# milisegundos. Si hay una pausa mayor, descartamos el fragmento anterior para
# no mezclar una lectura incompleta con la siguiente.
MAX_PAUSA_ENTRE_CARACTERES_QR = 1.0
# Sin sensor de paso, mantenemos el torno ocupado un margen tras apagar el
# relé. Es una política de seguridad de flujo: evita órdenes opuestas o una
# segunda autorización inmediata, pero el mecanismo del torno debe ser quien
# garantice físicamente una única rotación por cada apertura.
SEGUNDOS_BLOQUEO_POSTERIOR = 2

PIN_RELE_ENTRADA = 20  # CH2
PIN_RELE_SALIDA = 21   # CH3

rele_entrada = OutputDevice(PIN_RELE_ENTRADA, active_high=True, initial_value=False)
rele_salida = OutputDevice(PIN_RELE_SALIDA, active_high=True, initial_value=False)


def activar_rele(rele, nombre_canal):
    """Activa un relé durante el tiempo configurado y siempre lo deja apagado."""
    rele.on()
    print(f"   -> Relé {nombre_canal} ACTIVADO ({SEGUNDOS_ACTIVADO}s)")
    try:
        time.sleep(SEGUNDOS_ACTIVADO)
    finally:
        rele.off()
        print(f"   -> Relé {nombre_canal} desactivado")


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


def escuchar_lector(ruta_dispositivo, nombre_canal, rele, estado_lector, estado_torno):
    """Escucha un lector QR en su propio hilo y activa el relé indicado
    cada vez que detecta un código completo.

    Si el dispositivo se desconecta o falla, NO mata el hilo: reintenta
    reconectar cada pocos segundos indefinidamente. `estado` es un dict
    compartido donde vamos marcando si el lector está actualmente OK o
    caído, para que el watchdog pueda consultarlo.
    """
    ESPERA_RECONEXION = 5  # segundos entre intentos si el lector no está disponible

    def procesar_lectura(codigo_qr):
        """Valida una lectura y libera el carril cuando termina.

        No hay cola: mientras se valida un QR o el rele esta abierto, el bucle
        del lector sigue consumiendo eventos y descarta nuevas lecturas.
        """
        try:
            if codigo_qr.lower().startswith(f"{CONFIG_QR_SCHEME}:"):
                procesar_qr_configuracion(codigo_qr)
                return

            config_api = cargar_config_api()
            if config_api is None:
                log_live(f"[{nombre_canal}] ACCESO DENEGADO")
                return
            tiene_acceso = validar_qr_con_api(codigo_qr, config_api)
            if tiene_acceso:
                log_live(f"[{nombre_canal}] ACCESO PERMITIDO")
                activar_rele(rele, nombre_canal)
            else:
                log_live(f"[{nombre_canal}] ACCESO DENEGADO")
            if tiene_acceso:
                time.sleep(SEGUNDOS_BLOQUEO_POSTERIOR)
        except Exception:
            # Ante un error interno, el acceso se mantiene cerrado.
            log_live(f"[{nombre_canal}] ERROR INTERNO")
        finally:
            with estado_torno["lock"]:
                estado_torno["ocupado"] = False
                estado_torno["operacion_iniciada"] = None
                estado_torno["sentido"] = None

    while True:
        try:
            dev = InputDevice(ruta_dispositivo)
        except Exception as e:
            if estado_lector["ok"]:
                print(f"[{nombre_canal}] ERROR: no se pudo abrir {ruta_dispositivo}: {e}")
            estado_lector["ok"] = False
            time.sleep(ESPERA_RECONEXION)
            continue

        print(f"[{nombre_canal}] Escuchando en: {dev.name} ({ruta_dispositivo})")
        estado_lector["ok"] = True

        buffer = ""
        contador = 0
        shift_activo = False
        ultimo_caracter = None
        descartando_hasta_enter = False

        try:
            for evento in dev.read_loop():
                estado_lector["ok"] = True  # seguimos recibiendo eventos con normalidad
                if evento.type == ecodes.EV_KEY:
                    tecla = categorize(evento)
                    codigo_tecla = tecla.keycode
                    if isinstance(codigo_tecla, list):
                        codigo_tecla = codigo_tecla[0]

                    if codigo_tecla in TECLAS_SHIFT:
                        shift_activo = tecla.keystate in (tecla.key_down, tecla.key_hold)
                        continue

                    if tecla.keystate == tecla.key_down:
                        if descartando_hasta_enter:
                            if codigo_tecla == 'KEY_ENTER':
                                descartando_hasta_enter = False
                            continue

                        with estado_torno["lock"]:
                            torno_ocupado = estado_torno["ocupado"]
                        if torno_ocupado:
                            # Si una lectura empieza con el torno ocupado,
                            # descartamos todos sus caracteres hasta Enter.
                            # Así no puede mezclarse con la siguiente.
                            buffer = ""
                            ultimo_caracter = None
                            descartando_hasta_enter = codigo_tecla != 'KEY_ENTER'
                            continue

                        if codigo_tecla == 'KEY_ENTER':
                            ahora = time.monotonic()
                            if (
                                buffer
                                and ultimo_caracter is not None
                                and ahora - ultimo_caracter > MAX_PAUSA_ENTRE_CARACTERES_QR
                            ):
                                # Enter tardío de una lectura incompleta: no
                                # se envía ni se mezcla con una lectura futura.
                                buffer = ""
                                ultimo_caracter = None
                            if buffer:
                                codigo_qr = buffer
                                buffer = ""
                                ultimo_caracter = None
                                with estado_torno["lock"]:
                                    if estado_torno["ocupado"]:
                                        # Entrada y salida comparten el mismo
                                        # torno: no se encolan ni reintentan.
                                        continue
                                    estado_torno["ocupado"] = True
                                    estado_torno["operacion_iniciada"] = time.monotonic()
                                    estado_torno["sentido"] = nombre_canal
                                threading.Thread(
                                    target=procesar_lectura,
                                    args=(codigo_qr,),
                                    name=f"acceso-{nombre_canal}",
                                    daemon=True,
                                ).start()
                        elif codigo_tecla in MAPA_TECLAS:
                            ahora = time.monotonic()
                            if (
                                ultimo_caracter is not None
                                and ahora - ultimo_caracter > MAX_PAUSA_ENTRE_CARACTERES_QR
                            ):
                                buffer = ""
                            normal, con_shift = MAPA_TECLAS[codigo_tecla]
                            buffer += con_shift if shift_activo else normal
                            ultimo_caracter = ahora
                        else:
                            print(f"[{nombre_canal}] [aviso] Tecla no mapeada: {codigo_tecla}")
        except Exception as e:
            # El dispositivo se desconectó o dejó de responder a mitad de lectura
            print(f"[{nombre_canal}] Lector desconectado ({e}). Reintentando en {ESPERA_RECONEXION}s...")
            estado_lector["ok"] = False
            time.sleep(ESPERA_RECONEXION)
            # el bucle while True vuelve a intentar abrir el dispositivo


def main():
    iniciar_fifo()
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
    estado_torno = {
        "ocupado": False,
        "operacion_iniciada": None,
        "sentido": None,
        "lock": threading.Lock(),
    }
    detener = threading.Event()

    def solicitar_parada(signum, frame):
        print("[sistema] Parada solicitada.")
        detener.set()

    signal.signal(signal.SIGTERM, solicitar_parada)
    signal.signal(signal.SIGINT, solicitar_parada)

    if ruta_entrada:
        estados["ENTRADA"] = {
            "ok": False,
        }
        h = threading.Thread(
            target=escuchar_lector,
            args=(ruta_entrada, "ENTRADA", rele_entrada, estados["ENTRADA"], estado_torno),
            name="lector-ENTRADA",
            daemon=True,
        )
        h.start()
        hilos.append(h)

    if ruta_salida:
        estados["SALIDA"] = {
            "ok": False,
        }
        h = threading.Thread(
            target=escuchar_lector,
            args=(ruta_salida, "SALIDA", rele_salida, estados["SALIDA"], estado_torno),
            name="lector-SALIDA",
            daemon=True,
        )
        h.start()
        hilos.append(h)

    _notificar_systemd("READY=1")
    iniciar_latido_watchdog(hilos, estado_torno)

    try:
        while not detener.wait(1):
            pass
    finally:
        rele_entrada.off()
        rele_salida.off()
        rele_entrada.close()
        rele_salida.close()
        print("GPIO limpiado correctamente.")


if __name__ == "__main__":
    main()
