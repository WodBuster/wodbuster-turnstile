# WodBuster Turnstile

Control de acceso para torno con Raspberry Pi, lectores QR USB y placa de
relés. Valida cada lectura contra la API de WodBuster, controla los sentidos de
entrada/salida, se configura mediante QR y recibe actualizaciones OTA desde
releases públicas.

## Instalación como dispositivo

La forma recomendada de distribución es una imagen de Raspberry Pi OS Lite
preparada para el torno:

1. Descarga el `.img.xz` y su `.sha256` desde la última release.
2. Graba la imagen con Raspberry Pi Imager mediante `Usar personalizado`.
3. Conecta Ethernet, lectores y placa de relés.
4. Enciende la Raspberry.
5. Espera dos minutos y escanea el QR `wbconfig`.

Consulta [las instrucciones completas](docs/INSTALACION_IMAGEN.md).

## Mantenimiento de la imagen

La imagen es reproducible con `rpi-image-gen` y se publica como asset de la
release de aplicación correspondiente. No se almacena el binario dentro del
historial Git.

Consulta [la guía de construcción y publicación](docs/CONSTRUIR_Y_PUBLICAR_IMAGEN.md).

## Instalación existente

Las instalaciones OTA actuales conservan por compatibilidad:

```text
/home/jesus/torno_qr
```

La configuración local `acceso_config.json` nunca forma parte de Git ni de la
imagen distribuida.

