#!/usr/bin/env python3
"""
Vigilante de red para el sistema de torno QR
------------------------------------------------
Comprueba periódicamente si hay conexión a internet (funciona igual si
usas WiFi, cable RJ45, o ambos - no le importa la interfaz, solo si
"hay salida a internet" de verdad).

Si detecta que lleva un rato sin conexión, escala la respuesta poco a
poco, sin "volverse loco":

    1. Sin conexión unos minutos  -> reinicia el servicio de red
                                      (no afecta al torno QR, que sigue
                                      funcionando sin depender de internet)
    2. Sigue sin conexión bastante más tiempo -> reinicia la Raspberry Pi
                                      entera, como último recurso

Para evitar bucles de reinicios si el problema es externo (por ejemplo,
se ha caído el router o el proveedor de internet, algo que reiniciar la
Pi no puede arreglar), se respetan tiempos mínimos entre intentos
("cooldown"), y el de la Pi se recuerda en un archivo para que sobreviva
al propio reinicio.

Requisitos: ninguno extra, solo librería estándar de Python.

Uso: se ejecuta como servicio systemd continuo (ver
torno-network-watchdog.service), no se lanza a mano normalmente.
"""

import json
import os
import socket
import subprocess
import time

# ---------- Configuración ----------
# Todos los tiempos se leen de un archivo config.json editable, para poder
# ajustarlos sin tocar el código (es un torno de acceso real, conviene
# poder afinar esto en caliente). Si el archivo no existe, se crea uno
# con valores por defecto razonables la primera vez que arranca.
CONFIG_FILE = "/home/jesus/torno_qr/vigilante_red_config.json"

CONFIG_POR_DEFECTO = {
    "intervalo_comprobacion_segundos": 120,          # cada cuánto comprobar la conexión
    "espera_antes_de_reiniciar_red_segundos": 300,   # 5 min sin conexión -> reinicia el servicio de red
    "espera_antes_de_reiniciar_pi_segundos": 1200,   # 20 min sin conexión -> reinicia la Pi entera
    "cooldown_reinicio_red_segundos": 600,           # no reiniciar la red más de 1 vez cada 10 min
    "cooldown_reboot_pi_segundos": 3600,             # no reiniciar la Pi más de 1 vez por hora
    "hosts_de_prueba": [
        ["8.8.8.8", 53],
        ["1.1.1.1", 53]
    ]
}


def cargar_configuracion():
    """Lee config.json si existe; si no, lo crea con los valores por defecto."""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(CONFIG_POR_DEFECTO, f, indent=4)
        return dict(CONFIG_POR_DEFECTO)

    with open(CONFIG_FILE) as f:
        config_usuario = json.load(f)

    # Si el usuario borró alguna clave, rellenamos con el valor por defecto
    # para que nunca falte un parámetro y el script no se rompa.
    config = dict(CONFIG_POR_DEFECTO)
    config.update(config_usuario)
    return config

# Candidatos de servicio de red, en orden de preferencia (Raspberry Pi OS
# moderno usa NetworkManager; versiones antiguas usaban dhcpcd)
SERVICIOS_RED_CANDIDATOS = ["NetworkManager", "dhcpcd", "networking"]

ARCHIVO_ESTADO = "/home/jesus/torno_qr/estado_vigilante_red.json"


def log(mensaje):
    print(f"[vigilante-red] {mensaje}", flush=True)


def hay_conexion(config):
    """Intenta una conexión TCP rápida a servidores DNS públicos.
    Más fiable que hacer ping, y no necesita permisos especiales."""
    for host, puerto in config["hosts_de_prueba"]:
        try:
            with socket.create_connection((host, puerto), timeout=5):
                return True
        except OSError:
            continue
    return False


def cargar_estado():
    if os.path.exists(ARCHIVO_ESTADO):
        try:
            with open(ARCHIVO_ESTADO) as f:
                return json.load(f)
        except Exception:
            pass
    return {"ultimo_reinicio_red": None, "ultimo_reboot_pi": None}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w") as f:
        json.dump(estado, f)


def reiniciar_servicio_red():
    for servicio in SERVICIOS_RED_CANDIDATOS:
        resultado = subprocess.run(
            ["systemctl", "restart", servicio],
            capture_output=True, text=True
        )
        if resultado.returncode == 0:
            log(f"Servicio de red '{servicio}' reiniciado.")
            return True
        # si el servicio ni siquiera existe en este sistema, probamos el siguiente
    log("No se pudo reiniciar ningún servicio de red conocido.")
    return False


def reiniciar_pi():
    log("Reiniciando la Raspberry Pi como último recurso...")
    subprocess.run(["systemctl", "reboot"])


def main():
    log("Vigilante de red iniciado.")
    config = cargar_configuracion()
    log(f"Configuración cargada desde {CONFIG_FILE}: {config}")
    estado = cargar_estado()

    sin_conexion_desde = None

    while True:
        # Releemos la config en cada vuelta: así, si editas el archivo a
        # mano mientras el servicio corre, los cambios se aplican solos
        # en el siguiente ciclo, sin tener que reiniciar nada.
        config = cargar_configuracion()

        if hay_conexion(config):
            if sin_conexion_desde is not None:
                log("Conexión recuperada.")
            sin_conexion_desde = None
        else:
            ahora = time.time()
            if sin_conexion_desde is None:
                sin_conexion_desde = ahora
                log("Sin conexión detectada. Vigilando...")

            caido_desde_hace = ahora - sin_conexion_desde

            # --- Paso 2: reboot de la Pi (más drástico, se comprueba primero
            #     para no reiniciar la red inútilmente justo antes de rebotar) ---
            ultimo_reboot = estado.get("ultimo_reboot_pi")
            puede_rebotar = (
                ultimo_reboot is None
                or (ahora - ultimo_reboot) > config["cooldown_reboot_pi_segundos"]
            )

            if caido_desde_hace >= config["espera_antes_de_reiniciar_pi_segundos"] and puede_rebotar:
                estado["ultimo_reboot_pi"] = ahora
                guardar_estado(estado)
                reiniciar_pi()
                # el proceso muere aquí al reiniciar el sistema

            # --- Paso 1: reinicio del servicio de red ---
            elif caido_desde_hace >= config["espera_antes_de_reiniciar_red_segundos"]:
                ultimo_reinicio = estado.get("ultimo_reinicio_red")
                puede_reiniciar_red = (
                    ultimo_reinicio is None
                    or (ahora - ultimo_reinicio) > config["cooldown_reinicio_red_segundos"]
                )
                if puede_reiniciar_red:
                    log(f"Llevamos {int(caido_desde_hace)}s sin conexión. Reiniciando red...")
                    reiniciar_servicio_red()
                    estado["ultimo_reinicio_red"] = ahora
                    guardar_estado(estado)

        time.sleep(config["intervalo_comprobacion_segundos"])


if __name__ == "__main__":
    main()
