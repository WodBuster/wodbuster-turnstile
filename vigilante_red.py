#!/usr/bin/env python3
"""
Vigilante de red para el sistema de torno QR
------------------------------------------------
Comprueba periódicamente si hay conexión a internet (funciona igual si
usas WiFi, cable RJ45, o ambos - no le importa la interfaz, solo si
"hay salida a internet" de verdad).

Es un observador pasivo: una caída de Internet, un firewall o un problema
de un DNS público nunca reinician la red ni la Raspberry. Esos síntomas no
demuestran un fallo local y un falso positivo sería peor que no intervenir.

Requisitos: ninguno extra, solo librería estándar de Python.

Uso: se ejecuta como servicio systemd continuo (ver
torno-network-watchdog.service), no se lanza a mano normalmente.
"""

import json
import os
import socket
import time

# ---------- Configuración ----------
# Todos los tiempos se pueden leer de un archivo config.json editable. Si no
# existe o es inválido se usan valores por defecto, sin escribir en la SD.
CONFIG_FILE = "/home/jesus/torno_qr/vigilante_red_config.json"

CONFIG_POR_DEFECTO = {
    "intervalo_comprobacion_segundos": 120,          # cada cuánto comprobar la conexión
    "hosts_de_prueba": [
        ["8.8.8.8", 53],
        ["1.1.1.1", 53]
    ]
}


def cargar_configuracion():
    """Lee config.json si existe; nunca crea ni modifica archivos locales."""
    if not os.path.exists(CONFIG_FILE):
        return dict(CONFIG_POR_DEFECTO)

    try:
        with open(CONFIG_FILE) as f:
            config_usuario = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"Configuración no utilizable ({e}); se usan valores por defecto.")
        return dict(CONFIG_POR_DEFECTO)
    if not isinstance(config_usuario, dict):
        log("Configuración no utilizable; se usan valores por defecto.")
        return dict(CONFIG_POR_DEFECTO)

    # Si el usuario borró alguna clave, rellenamos con el valor por defecto
    # para que nunca falte un parámetro y el script no se rompa.
    config = dict(CONFIG_POR_DEFECTO)
    config.update(config_usuario)
    try:
        intervalo = int(config["intervalo_comprobacion_segundos"])
        if intervalo < 30:
            raise ValueError
        config["intervalo_comprobacion_segundos"] = intervalo
        hosts = []
        for host, puerto in config["hosts_de_prueba"]:
            puerto = int(puerto)
            if not isinstance(host, str) or not host or not 1 <= puerto <= 65535:
                raise ValueError
            hosts.append((host, puerto))
        if not hosts:
            raise ValueError
        config["hosts_de_prueba"] = hosts
    except (TypeError, ValueError):
        log("Configuración no válida; se usan valores por defecto.")
        return dict(CONFIG_POR_DEFECTO)
    return config

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


def main():
    log("Vigilante de red iniciado.")
    config = cargar_configuracion()
    log(f"Configuración cargada desde {CONFIG_FILE}: {config}")
    conexion_anterior = None

    while True:
        # Releemos la config en cada vuelta: así, si editas el archivo a
        # mano mientras el servicio corre, los cambios se aplican solos
        # en el siguiente ciclo, sin tener que reiniciar nada.
        config = cargar_configuracion()

        if hay_conexion(config):
            if conexion_anterior is False:
                log("Conexión recuperada.")
            conexion_anterior = True
        else:
            if conexion_anterior is not False:
                log("Sin conexión detectada. No se realizará ningún reinicio automático.")
            conexion_anterior = False

        time.sleep(config["intervalo_comprobacion_segundos"])


if __name__ == "__main__":
    main()
