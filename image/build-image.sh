#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
rpi_image_gen_dir=""
image_version=""
ssh_public_key_file=""
output_dir="$repo_root/dist"

usage() {
  cat <<'EOF'
Uso:
  image/build-image.sh \
    --rpi-image-gen-dir /ruta/rpi-image-gen \
    --image-version 0.1.0 \
    [--ssh-public-key-file /ruta/id_ed25519.pub] \
    [--output-dir /ruta/salida]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rpi-image-gen-dir)
      rpi_image_gen_dir="$2"
      shift 2
      ;;
    --image-version)
      image_version="$2"
      shift 2
      ;;
    --ssh-public-key-file)
      ssh_public_key_file="$2"
      shift 2
      ;;
    --output-dir)
      output_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Argumento desconocido: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "La imagen solo se puede construir en Linux." >&2
  exit 1
fi
if [[ ! "$image_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "--image-version debe ser una versión como 0.1.0" >&2
  exit 2
fi
if [[ ! -x "$rpi_image_gen_dir/rpi-image-gen" ]]; then
  echo "No se encontró rpi-image-gen ejecutable en: $rpi_image_gen_dir" >&2
  exit 2
fi
if [[ -n "$ssh_public_key_file" && ! -f "$ssh_public_key_file" ]]; then
  echo "No existe la clave pública SSH: $ssh_public_key_file" >&2
  exit 2
fi

app_version="$(tr -d '\r\n' < "$repo_root/VERSION")"
build_root="$repo_root/image/.work"
mkdir -p "$build_root" "$output_dir"
stage="$(mktemp -d "$build_root/stage.XXXXXXXX")"
workroot="$build_root/rpi-image-gen-$image_version"

cleanup() {
  if [[ "$stage" == "$build_root"/stage.* && -d "$stage" ]]; then
    rm -rf -- "$stage"
  fi
}
trap cleanup EXIT

cp -a "$script_dir/rpi-image-gen/." "$stage/"

app_target="$stage/rootfs-overlay/home/jesus/torno_qr"
units_target="$stage/rootfs-overlay/etc/systemd/system"
mkdir -p "$app_target" "$units_target"

for file in \
  lector_qr_rele.py \
  vigilante_red.py \
  actualizador.py \
  VERSION \
  LICENSE; do
  install -m 0644 "$repo_root/$file" "$app_target/$file"
done

for file in \
  lector-qr.service \
  torno-network-watchdog.service \
  torno-updater.service \
  torno-updater.timer; do
  install -m 0644 "$repo_root/$file" "$app_target/$file"
  install -m 0644 "$repo_root/$file" "$units_target/$file"
done

chmod 0755 "$stage/bdebstrap/customize90-wodbuster"
chmod 0755 "$stage/rootfs-overlay/usr/local/sbin/wodbuster-firstboot"

ssh_public_key=""
if [[ -n "$ssh_public_key_file" ]]; then
  ssh_public_key="$(< "$ssh_public_key_file")"
fi

if [[ "$workroot" == "$build_root"/rpi-image-gen-* && -d "$workroot" ]]; then
  rm -rf -- "$workroot"
fi

"$rpi_image_gen_dir/rpi-image-gen" build \
  -S "$stage" \
  -c "$stage/config/wodbuster-access.yaml" \
  -- \
  "IGconf_artefact_version=$image_version" \
  "IGconf_sys_workroot=$workroot" \
  "IGconf_ssh_pubkey_user1=$ssh_public_key"

raw_image="$(find "$workroot" -type f -name 'wodbuster-turnstile-os.img' -print -quit)"
if [[ -z "$raw_image" ]]; then
  echo "rpi-image-gen terminó sin producir wodbuster-turnstile-os.img" >&2
  exit 1
fi

base_name="wodbuster-turnstile-os-${image_version}-app-${app_version}"
uncompressed="$output_dir/$base_name.img"
compressed="$uncompressed.xz"
rm -f -- "$uncompressed" "$compressed" "$compressed.sha256" "$output_dir/$base_name.build-info.json"
cp --sparse=always "$raw_image" "$uncompressed"
xz -T0 -6 -f "$uncompressed"
(
  cd "$output_dir"
  sha256sum "$(basename "$compressed")" > "$(basename "$compressed").sha256"
)

cat > "$output_dir/$base_name.build-info.json" <<EOF
{
  "image_version": "$image_version",
  "application_version": "$app_version",
  "application_commit": "$(git -C "$repo_root" rev-parse HEAD)",
  "rpi_image_gen_commit": "$(git -C "$rpi_image_gen_dir" rev-parse HEAD)",
  "base": "Raspberry Pi OS Trixie arm64 minimal",
  "device_layer": "rpi5"
}
EOF

echo "Imagen creada:"
echo "  $compressed"
echo "  $compressed.sha256"
echo "  $output_dir/$base_name.build-info.json"
