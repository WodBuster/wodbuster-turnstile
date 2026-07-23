#!/usr/bin/env python3
"""
Actualizador OTA para el sistema de torno QR
------------------------------------------------
Funciona como las actualizaciones de Android:
    1. Consulta la última "Release" publicada en un repositorio de GitHub.
    2. Compara esa versión con la que hay instalada localmente (archivo VERSION).
    3. Si hay una versión más nueva, la descarga, hace una copia de
       seguridad de la instalación actual, aplica los archivos nuevos y
       reinicia el servicio.
    4. Si tras el reinicio el servicio no arranca bien en unos segundos,
       deshace el cambio automáticamente (rollback) y deja la versión
       anterior funcionando.

CONFIGURA ESTO ANTES DE USARLO:
    - GITHUB_REPO: pon aquí tu repositorio, formato "usuario/repositorio"
    - Publica versiones en GitHub como "Release" con una etiqueta tipo v1.0.0
      (Releases -> Draft a new release -> Tag: v1.0.1 -> Publish)

Requisitos: ninguno extra, usa solo librería estándar de Python.

Uso manual (para probarlo a mano):
    sudo python3 actualizador.py

Normalmente se ejecuta solo, mediante un temporizador de systemd
(ver torno-updater.timer / torno-updater.service).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

# ---------- CONFIGURACIÓN - AJUSTA ESTO ----------
GITHUB_REPO = "WodBuster/wodbuster-turnstile"
INSTALL_DIR = "/home/jesus/torno_qr"
SERVICE_NAME = "lector-qr.service"
VERSION_FILE = os.path.join(INSTALL_DIR, "VERSION")
BACKUP_DIR = "/home/jesus/torno_qr_backups"
SEGUNDOS_ESPERA_VERIFICACION = 8   # cuánto esperar tras reiniciar para comprobar que arrancó bien
MAX_BACKUPS = 3                    # cuántos backups se conservan (los más recientes)
UNIDADES_SYSTEMD = (
    "lector-qr.service",
    "torno-network-watchdog.service",
    "torno-updater.service",
    "torno-updater.timer",
)
DIRECTORIO_BACKUP_SYSTEMD = "_systemd_units"
# ---------------------------------------------------


def log(mensaje):
    print(f"[actualizador] {mensaje}", flush=True)


def obtener_version_remota():
    """Consulta la API de GitHub y devuelve (version, url_descarga) de la última release."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            datos = json.loads(resp.read().decode())
        version = datos["tag_name"].lstrip("v")   # "v1.2.0" -> "1.2.0"
        url_descarga = datos["tarball_url"]
        return version, url_descarga
    except Exception as e:
        log(f"No se pudo consultar GitHub: {e}")
        return None, None


def version_actual():
    """Lee la versión instalada localmente. Si no existe el archivo, asumimos 0.0.0."""
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE) as f:
            return f.read().strip()
    return "0.0.0"


def version_mas_nueva(v_remota, v_local):
    """Compara versiones tipo '1.2.0' numéricamente (no como texto)."""
    def a_tupla(v):
        return tuple(int(x) for x in v.split("."))
    try:
        return a_tupla(v_remota) > a_tupla(v_local)
    except Exception:
        # Si el formato no es el esperado, comparamos como texto por seguridad
        return v_remota != v_local


def _ignorar_archivos_especiales(directorio, nombres):
    """Para usar con shutil.copytree: evita que falle por archivos que no son
    ni ficheros normales, ni carpetas, ni enlaces (pipes, sockets, etc.),
    que a veces dejan otros programas (editores, IDEs) como archivos temporales."""
    ignorar = []
    for nombre in nombres:
        ruta = os.path.join(directorio, nombre)
        try:
            modo = os.lstat(ruta).st_mode
        except FileNotFoundError:
            continue
        if not (stat.S_ISREG(modo) or stat.S_ISDIR(modo) or stat.S_ISLNK(modo)):
            print(f"[actualizador] Ignorando archivo especial (no se copia): {ruta}")
            ignorar.append(nombre)
    return ignorar


def limpiar_backups_antiguos():
    """Borra los backups más antiguos, manteniendo solo los últimos MAX_BACKUPS."""
    if not os.path.exists(BACKUP_DIR):
        return
    backups = sorted([
        d for d in os.listdir(BACKUP_DIR)
        if os.path.isdir(os.path.join(BACKUP_DIR, d))
    ])  # orden alfabético = orden cronológico (nombre backup_YYYYMMDD_HHMMSS)

    a_borrar = backups[:-MAX_BACKUPS] if len(backups) > MAX_BACKUPS else []
    for nombre in a_borrar:
        ruta = os.path.join(BACKUP_DIR, nombre)
        shutil.rmtree(ruta)
        log(f"Backup antiguo eliminado: {nombre}")


def hacer_backup():
    """Copia la instalación actual antes de tocar nada."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    marca_tiempo = time.strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"backup_{marca_tiempo}")
    shutil.copytree(INSTALL_DIR, destino, ignore=_ignorar_archivos_especiales)
    hacer_backup_unidades_systemd(destino)
    log(f"Backup creado en {destino}")
    return destino


def hacer_backup_unidades_systemd(destino_backup):
    """Guarda las unidades que el actualizador tiene permitido modificar."""
    directorio = os.path.join(destino_backup, DIRECTORIO_BACKUP_SYSTEMD)
    os.makedirs(directorio, exist_ok=True)
    presentes = []
    for nombre in UNIDADES_SYSTEMD:
        origen = os.path.join("/etc/systemd/system", nombre)
        if os.path.isfile(origen):
            shutil.copy2(origen, os.path.join(directorio, nombre))
            presentes.append(nombre)
    with open(os.path.join(directorio, "estado.json"), "w") as f:
        json.dump({"presentes": presentes}, f)


def restaurar_unidades_systemd(ruta_backup):
    """Restaura exactamente las unidades existentes antes de actualizar."""
    directorio = os.path.join(ruta_backup, DIRECTORIO_BACKUP_SYSTEMD)
    with open(os.path.join(directorio, "estado.json")) as f:
        presentes = set(json.load(f)["presentes"])

    for nombre in UNIDADES_SYSTEMD:
        destino = os.path.join("/etc/systemd/system", nombre)
        origen = os.path.join(directorio, nombre)
        if nombre in presentes:
            shutil.copy2(origen, destino)
        elif os.path.exists(destino):
            os.remove(destino)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    log("Unidades systemd restauradas.")


def descargar_y_extraer(url_descarga):
    """Descarga el tarball de la release y lo extrae a una carpeta temporal.
    Devuelve la ruta de la carpeta extraída (la que contiene los archivos del repo)."""
    tmp_dir = tempfile.mkdtemp(prefix="torno_update_")
    tar_path = os.path.join(tmp_dir, "release.tar.gz")

    log("Descargando nueva versión...")
    urllib.request.urlretrieve(url_descarga, tar_path)

    with tarfile.open(tar_path) as tar:
        tar.extractall(tmp_dir, filter="data")

    # Borramos el tarball ya extraído para no acumular archivos en /tmp
    os.remove(tar_path)

    # GitHub mete todo dentro de una única carpeta tipo "usuario-repo-hash"
    carpetas = [
        d for d in os.listdir(tmp_dir)
        if os.path.isdir(os.path.join(tmp_dir, d))
    ]
    if not carpetas:
        raise RuntimeError("El archivo descargado no contiene ninguna carpeta")

    return os.path.join(tmp_dir, carpetas[0]), tmp_dir


def aplicar_actualizacion(carpeta_origen, nueva_version):
    """Copia los archivos nuevos sobre la instalación actual,
    e instala en systemd los archivos .service y .timer si han cambiado."""
    # Solo desplegamos el runtime explícitamente permitido. Así, documentación,
    # workflows y herramientas para construir la imagen nunca terminan copiados
    # en la SD por una actualización OTA.
    ARCHIVOS_RUNTIME = {
        "lector_qr_rele.py",
        "vigilante_red.py",
        "actualizador.py",
        "VERSION",
        "LICENSE",
        *UNIDADES_SYSTEMD,
    }

    for item in sorted(ARCHIVOS_RUNTIME):
        origen = os.path.join(carpeta_origen, item)
        if not os.path.exists(origen):
            raise RuntimeError(f"La release no contiene el archivo obligatorio: {item}")
        destino = os.path.join(INSTALL_DIR, item)
        if os.path.isdir(origen):
            if os.path.exists(destino):
                shutil.rmtree(destino)
            shutil.copytree(origen, destino)
        else:
            shutil.copy2(origen, destino)

    # Instalar / actualizar archivos de systemd si vienen en la actualización
    archivos_systemd = [
        archivo for archivo in UNIDADES_SYSTEMD
        if os.path.isfile(os.path.join(carpeta_origen, archivo))
    ]
    if archivos_systemd:
        for archivo in archivos_systemd:
            origen = os.path.join(carpeta_origen, archivo)
            destino = f"/etc/systemd/system/{archivo}"
            shutil.copy2(origen, destino)
            log(f"Servicio systemd actualizado: {archivo}")
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        log("systemd recargado.")

    with open(VERSION_FILE, "w") as f:
        f.write(nueva_version)

    log(f"Archivos actualizados a la versión {nueva_version}")


def reiniciar_servicio():
    subprocess.run(["systemctl", "restart", SERVICE_NAME], check=True)


def servicio_esta_activo():
    resultado = subprocess.run(
        ["systemctl", "is-active", SERVICE_NAME],
        capture_output=True, text=True
    )
    return resultado.stdout.strip() == "active"


def restaurar_backup(ruta_backup):
    log("¡La nueva versión falló! Restaurando la copia de seguridad...")
    # Paramos la nueva versión antes de modificar código o unidades. El lector
    # recibe SIGTERM y deja los relés en estado seguro antes del siguiente arranque.
    subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)
    # Vaciamos el directorio de instalación y volvemos a poner el backup
    for item in os.listdir(INSTALL_DIR):
        ruta = os.path.join(INSTALL_DIR, item)
        try:
            modo = os.lstat(ruta).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(modo):
            shutil.rmtree(ruta)
        elif stat.S_ISREG(modo) or stat.S_ISLNK(modo):
            os.remove(ruta)
        else:
            print(f"[actualizador] Ignorando archivo especial al limpiar: {ruta}")

    for item in os.listdir(ruta_backup):
        if item == DIRECTORIO_BACKUP_SYSTEMD:
            continue
        origen = os.path.join(ruta_backup, item)
        destino = os.path.join(INSTALL_DIR, item)
        if os.path.isdir(origen):
            shutil.copytree(origen, destino, ignore=_ignorar_archivos_especiales)
        else:
            shutil.copy2(origen, destino)

    restaurar_unidades_systemd(ruta_backup)
    reiniciar_servicio()
    log("Rollback completado. Versión anterior restaurada y en marcha.")


def main():
    log("Comprobando actualizaciones...")

    v_remota, url_descarga = obtener_version_remota()
    if v_remota is None:
        log("No se pudo comprobar la versión remota. Se reintentará en el próximo ciclo.")
        return

    v_local = version_actual()
    log(f"Versión instalada: {v_local} | Versión disponible: {v_remota}")

    if not version_mas_nueva(v_remota, v_local):
        log("Ya tienes la última versión. Nada que hacer.")
        return

    log(f"Nueva versión detectada: {v_remota}. Aplicando actualización...")

    ruta_backup = hacer_backup()
    tmp_dir = None

    try:
        carpeta_extraida, tmp_dir = descargar_y_extraer(url_descarga)
        aplicar_actualizacion(carpeta_extraida, v_remota)
        reiniciar_servicio()

        log(f"Esperando {SEGUNDOS_ESPERA_VERIFICACION}s para comprobar que arrancó bien...")
        time.sleep(SEGUNDOS_ESPERA_VERIFICACION)

        if servicio_esta_activo():
            log(f"Actualización a {v_remota} completada con éxito.")
            limpiar_backups_antiguos()
        else:
            restaurar_backup(ruta_backup)

    except Exception as e:
        log(f"Error durante la actualización: {e}")
        restaurar_backup(ruta_backup)

    finally:
        # Limpiamos siempre los archivos temporales de la descarga
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log("Archivos temporales de descarga eliminados.")


if __name__ == "__main__":
    main()
