import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_python_runtime_parses(self):
        for name in ("lector_qr_rele.py", "vigilante_red.py", "actualizador.py"):
            ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

    def test_versions_and_workflow_are_aligned(self):
        app_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        image_version = (
            ROOT / "image" / "IMAGE_VERSION"
        ).read_text(encoding="utf-8").strip()
        workflow = (
            ROOT / ".github" / "workflows" / "build-os-image.yml"
        ).read_text(encoding="utf-8")
        self.assertRegex(app_version, r"^\d+\.\d+\.\d+$")
        self.assertRegex(image_version, r"^\d+\.\d+\.\d+$")
        self.assertIn(f'default: "v{app_version}"', workflow)
        self.assertIn(f'default: "{image_version}"', workflow)

    def test_image_never_contains_local_credentials(self):
        forbidden = {
            "acceso_config.json",
            "vigilante_red_config.json",
            "estado_vigilante_red.json",
            "hardware_config.json",
        }
        tracked_image_files = {
            path.name for path in (ROOT / "image").rglob("*") if path.is_file()
        }
        self.assertTrue(forbidden.isdisjoint(tracked_image_files))

        hook = (
            ROOT
            / "image"
            / "rpi-image-gen"
            / "bdebstrap"
            / "customize90-wodbuster"
        ).read_text(encoding="utf-8")
        for name in forbidden:
            self.assertIn(name, hook)

    def test_firstboot_has_identity_expansion_and_marker(self):
        script = (
            ROOT
            / "image"
            / "rpi-image-gen"
            / "rootfs-overlay"
            / "usr"
            / "local"
            / "sbin"
            / "wodbuster-firstboot"
        ).read_text(encoding="utf-8")
        for required in (
            "systemd-machine-id-setup",
            "ssh-keygen -A",
            "growpart",
            "resize2fs",
            "/var/lib/wodbuster/firstboot-complete",
        ):
            self.assertIn(required, script)

    def test_updater_uses_runtime_allowlist(self):
        source = (ROOT / "actualizador.py").read_text(encoding="utf-8")
        self.assertIn("ARCHIVOS_RUNTIME", source)
        self.assertNotIn("for item in os.listdir(carpeta_origen)", source)
        for required in (
            "lector_qr_rele.py",
            "vigilante_red.py",
            "actualizador.py",
            "VERSION",
        ):
            self.assertIn(f'"{required}"', source)

    def test_build_info_template_is_valid_json_shape(self):
        build_script = (ROOT / "image" / "build-image.sh").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r'cat > "\$output_dir/\$base_name\.build-info\.json" <<EOF\n'
            r"(\{.*?\})\nEOF",
            build_script,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        rendered = (
            match.group(1)
            .replace("$image_version", "0.1.0")
            .replace("$app_version", "1.0.12")
        )
        rendered = re.sub(r'"\$\(.*?\)"', '"commit"', rendered)
        data = json.loads(rendered)
        self.assertEqual(data["device_layer"], "rpi5")


if __name__ == "__main__":
    unittest.main()
