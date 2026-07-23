# Construir y publicar WodBuster Turnstile OS

Este documento es la fuente de verdad para mantenedores y futuras sesiones de
Codex. La imagen se construye desde cero; no se clona una SD utilizada.

## Decisiones del diseño actual

- Generador fijado: `raspberrypi/rpi-image-gen` v2.7.0.
- Base: Raspberry Pi OS Trixie arm64 minimal.
- Dispositivo: `rpi5`. `rpi-image-gen` exige una capa concreta que proporcione
  `rpi-device`; la capa familiar `rpi-generic64` no puede utilizarse sola en la
  versión 2.7.0.
- Imagen: MBR, boot FAT y raíz ext4 de 3 GiB.
- El primer arranque amplía la raíz a la capacidad de la microSD.
- Aplicación instalada en `/home/jesus/torno_qr` para mantener compatibilidad
  con el actualizador y los servicios existentes.
- Estado y backups permanecen fuera de los artefactos reemplazables.
- Sin `acceso_config.json`, Wi-Fi, tokens o claves privadas.
- `journald` usa almacenamiento volátil limitado a 32 MiB.
- Ethernet DHCP es el único método de red garantizado por la imagen pública.

## Por qué la imagen no se guarda en el historial Git

GitHub rechaza objetos Git individuales superiores a 100 MB. Las imágenes se
adjuntan como assets de GitHub Release, que admiten ficheros de hasta 2 GiB.

No se deben crear releases independientes con etiquetas `os-*` en este
repositorio: `actualizador.py` consulta `/releases/latest` y espera que la
etiqueta sea una versión de aplicación. Cada imagen se adjunta a la release de
aplicación que contiene.

## Construcción local soportada

El camino recomendado por Raspberry Pi es un host arm64 con Raspberry Pi OS o
Debian Bookworm/Trixie. La compilación cruzada en Ubuntu/Debian amd64 funciona,
pero no es el entorno oficialmente soportado.

Requisitos mínimos del host:

- Linux.
- 4 GiB libres como mínimo; se recomiendan 12 GiB.
- 2 GiB de RAM.
- Acceso a Internet.

Preparación:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  binfmt-support debian-archive-keyring git qemu-user-static xz-utils

git clone --depth 1 --branch v2.7.0 \
  https://github.com/raspberrypi/rpi-image-gen.git \
  /tmp/rpi-image-gen

sudo /tmp/rpi-image-gen/install_deps.sh
```

Construcción pública, sin acceso remoto:

```bash
chmod +x image/build-image.sh
image/build-image.sh \
  --rpi-image-gen-dir /tmp/rpi-image-gen \
  --image-version 0.1.0
```

Construcción gestionada con clave SSH pública:

```bash
image/build-image.sh \
  --rpi-image-gen-dir /tmp/rpi-image-gen \
  --image-version 0.1.0 \
  --ssh-public-key-file "$HOME/.ssh/id_ed25519.pub"
```

Salida:

```text
dist/wodbuster-turnstile-os-0.1.0-app-X.Y.Z.img.xz
dist/wodbuster-turnstile-os-0.1.0-app-X.Y.Z.img.xz.sha256
dist/wodbuster-turnstile-os-0.1.0-app-X.Y.Z.build-info.json
```

## Construcción y publicación en GitHub

1. Publica primero la release de aplicación correspondiente.
2. Abre `Actions`.
3. Ejecuta `Construir imagen WodBuster Turnstile OS`.
4. Indica:
   - `image_version`: versión de la base, por ejemplo `0.1.0`.
   - `release_tag`: release de aplicación, por ejemplo `v1.0.12`.
5. El workflow:
   - Construye arm64.
   - Comprime a `.img.xz`.
   - Genera SHA-256 e información de construcción.
   - Comprueba que el asset no supera 2 GiB.
   - Conserva una copia temporal durante 14 días.
   - Adjunta los tres archivos a la release.

## Contenido del primer arranque

`wodbuster-firstboot.service`:

1. Genera `machine-id` si falta.
2. Genera claves SSH únicas.
3. Crea un hostname `wodbuster-<serial>`.
4. Amplía partición y ext4.
5. Marca `/var/lib/wodbuster/firstboot-complete`.

La configuración de acceso no forma parte del primer arranque. La crea
`lector_qr_rele.py` exclusivamente después de validar un QR `wbconfig`.

## Checklist obligatorio antes de anunciar una imagen

- [ ] El workflow termina correctamente.
- [ ] SHA-256 válido.
- [ ] Raspberry Pi Imager graba y verifica `.img.xz`.
- [ ] Arranque sin teclado ni pantalla.
- [ ] Hostname y `machine-id` únicos en dos tarjetas distintas.
- [ ] No existe `acceso_config.json` antes del enrolamiento.
- [ ] Ethernet obtiene DHCP.
- [ ] Ambos lectores aparecen en sus rutas esperadas.
- [ ] Primer QR de configuración crea JSON 0600.
- [ ] QR normal permitido activa el relé correcto.
- [ ] QR denegado no activa relés.
- [ ] Desconectar y reconectar cada lector se recupera.
- [ ] Corte de red no reinicia la Raspberry.
- [ ] OTA actualiza y conserva configuración.
- [ ] Rollback restaura aplicación y unidades.
- [ ] Diagnóstico manual exige parar antes el servicio.
- [ ] Journal reside en `/run/log/journal`, no en `/var/log/journal`.
- [ ] Reinicio y corte de corriente dejan ambos relés apagados.

No se debe declarar soporte para Pi 3/4/Zero 2 W hasta repetir físicamente este
checklist en cada modelo.
