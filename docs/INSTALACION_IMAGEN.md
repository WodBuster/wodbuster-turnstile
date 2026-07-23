# Instalar WodBuster Turnstile OS

## Estado de soporte

La primera imagen distribuible está preparada con:

- Raspberry Pi OS Trixie minimal de 64 bits.
- Aplicación WodBuster Turnstile instalada.
- Lector QR, relés, watchdog pasivo y actualizaciones OTA.
- Logs de `systemd-journald` únicamente en memoria.
- Configuración de acceso ausente: se crea al escanear un QR `wbconfig`.

La capa de sistema es compatible con Raspberry Pi de 64 bits, pero el soporte
funcional inicial se limita al hardware probado:

- Raspberry Pi 5.
- Waveshare RPi Relay Board con entrada en GPIO20 y salida en GPIO21.
- Los dos lectores conectados en los mismos puertos físicos usados durante las
  pruebas.

Raspberry Pi 3B+, 4 y Zero 2 W no deben anunciarse como soportadas hasta
completar el checklist físico.

## Requisitos

- Raspberry Pi 5.
- MicroSD de 16 GB o más.
- Cable Ethernet con DHCP y salida a Internet.
- Los lectores QR y la placa de relés ya conectados.
- Raspberry Pi Imager reciente.
- QR de configuración válido.

La imagen pública no contiene Wi-Fi, contraseñas, claves SSH privadas ni
credenciales de ningún centro. El primer arranque requiere Ethernet.

## Descargar y verificar

1. Abre la última release de
   `WodBuster/wodbuster-turnstile`.
2. Descarga:
   - `wodbuster-turnstile-os-<imagen>-app-<app>.img.xz`
   - El fichero del mismo nombre terminado en `.sha256`.
3. Verifica el SHA-256.

En Linux:

```bash
sha256sum -c wodbuster-turnstile-os-*.img.xz.sha256
```

En PowerShell:

```powershell
Get-FileHash .\wodbuster-turnstile-os-*.img.xz -Algorithm SHA256
```

Compara el resultado con el contenido del `.sha256`.

## Grabar la microSD

1. Abre Raspberry Pi Imager.
2. Pulsa `Elegir dispositivo` y selecciona Raspberry Pi 5.
3. En `Elegir sistema operativo`, selecciona `Usar personalizado`.
4. Selecciona el fichero `.img.xz`.
5. Selecciona la microSD correcta.
6. No introduzcas credenciales Wi-Fi ni contraseñas en la imagen pública.
7. Graba y permite que Imager verifique la escritura.

## Primer arranque

1. Con la Raspberry apagada, introduce la microSD.
2. Conecta Ethernet, lectores QR y placa de relés.
3. Enciende la Raspberry.
4. Espera al menos dos minutos.
5. Escanea una vez el QR `wbconfig`.

Si la API responde HTTP 2xx, se crea:

```text
/home/jesus/torno_qr/acceso_config.json
```

El torno queda operativo y el actualizador comprueba releases públicas cada
hora.

## Configuración mediante QR

Formato:

```text
wbconfig://configure?v=1&box=CODIGO_BOX&usuario=USUARIO&pwd=PASSWORD_URL_ENCODED
```

- No incluyas comillas.
- Los nombres de parámetros van en minúsculas.
- Codifica caracteres especiales de la contraseña como URL.
- El QR de configuración nunca activa el relé.

## Acceso remoto

La imagen pública no permite iniciar sesión con contraseña. Para una imagen
gestionada por WodBuster, el mantenedor debe construirla con una clave pública
SSH:

```bash
image/build-image.sh \
  --rpi-image-gen-dir /ruta/rpi-image-gen \
  --image-version 0.1.0 \
  --ssh-public-key-file /ruta/id_ed25519.pub
```

Nunca se incluye la clave privada.

## Diagnóstico

Antes de ejecutar el lector manualmente hay que detener el servicio:

```bash
sudo systemctl stop lector-qr.service
cd /home/jesus/torno_qr
sudo ./lector_qr_rele.py --diagnostico \
  --entrada /dev/input/by-path/platform-xhci-hcd.1-usb-0:1:1.0-event-kbd \
  --salida /dev/input/by-path/platform-xhci-hcd.0-usb-0:1:1.0-event-kbd
```

Finaliza con `Ctrl+C` y restaura el servicio:

```bash
sudo systemctl reset-failed lector-qr.service
sudo systemctl start lector-qr.service
```

No dejes simultáneamente el proceso de diagnóstico y el servicio: competirían
por el GPIO.

