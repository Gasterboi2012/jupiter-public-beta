"""Unit tests for the Jupiter installer logic (no disks are touched)."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "jupiter_installer", ROOT / "installer" / "jupiter-installer.py")
inst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inst)


class PartitionPath(unittest.TestCase):
    def test_sata(self):
        self.assertEqual(inst.partition_path("/dev/sda", 1), "/dev/sda1")

    def test_nvme(self):
        self.assertEqual(inst.partition_path("/dev/nvme0n1", 2), "/dev/nvme0n1p2")


class Validation(unittest.TestCase):
    def test_username_ok(self):
        self.assertIsNone(inst.validate_username("alice"))

    def test_username_bad(self):
        for bad in ["Alice", "1abc", "", "root", "a b"]:
            self.assertIsNotNone(inst.validate_username(bad), bad)

    def test_password(self):
        self.assertIsNotNone(inst.validate_password("abc", "abc"))
        self.assertIsNotNone(inst.validate_password("secret1", "secret2"))
        self.assertIsNone(inst.validate_password("secret1", "secret1"))


class Plan(unittest.TestCase):
    def test_nvidia_adds_driver_steps(self):
        names = [d for d, _ in inst.build_plan("/dev/sda", "nvidia")]
        self.assertTrue(any("NVIDIA" in n for n in names))
        self.assertFalse(any("NVIDIA" in n for n in
                             [d for d, _ in inst.build_plan("/dev/sda", "mesa")]))

    def test_base_packages_present(self):
        argv = dict(inst.build_plan("/dev/sda", "mesa"))["Installing Jupiter base (this takes a while)"]
        for pkg in ["hyprland", "dms", "helium-bin"]:
            self.assertIn(pkg, argv)

    def test_nvidia_packages_use_open_driver(self):
        self.assertIn("akmod-nvidia-open", inst.NVIDIA_PACKAGES)
        self.assertNotIn("akmod-nvidia ", " ".join(inst.NVIDIA_PACKAGES) + " ")

    def test_password_never_in_argv(self):
        for _, argv in inst.post_install_plan("alice", "nvidia"):
            self.assertFalse(any("secret" in a for a in argv))

    def test_wipe_targets_selected_disk_only(self):
        for desc, argv in inst.build_plan("/dev/nvme0n1", "mesa")[:8]:
            for a in argv:
                if a.startswith("/dev/"):
                    self.assertTrue(a.startswith("/dev/nvme0n1"), a)


class Network(unittest.TestCase):
    def test_parse_wifi_list(self):
        text = "HomeNet:WPA2\nCafe\\:Guest:\n:hidden:WPA2\nHomeNet:WPA2\n"
        self.assertEqual(inst.parse_wifi_list(text),
                         [("Cafe:Guest", "open"), ("HomeNet", "WPA2")])

    def test_wifi_list_no_networks(self):
        self.assertEqual(inst.parse_wifi_list(""), [])


class Splash(unittest.TestCase):
    def test_theme_files_present(self):
        self.assertEqual(set(inst.SPLASH_FILES),
                         {"jupiter.plymouth", "jupiter.script"})
        self.assertIn("ModuleName=script", inst.SPLASH_FILES["jupiter.plymouth"])

    def test_plymouth_packages_in_base(self):
        argv = dict(inst.build_plan("/dev/sda", "mesa"))["Installing Jupiter base (this takes a while)"]
        self.assertIn("plymouth", argv)


class Terminal(unittest.TestCase):
    def test_font_pin_is_sha256(self):
        self.assertRegex(inst.NERD_FONT_SHA256, r"^[0-9a-f]{64}$")
        self.assertIn("v3.5.1", inst.NERD_FONT_URL)

    def test_foot_uses_nerd_font_family(self):
        # fontconfig family name of IosevkaNerdFontMono (checked from the TTF name table)
        self.assertIn("font=Iosevka Nerd Font Mono:", inst.FOOT_INI)

    def test_fastfetch_config_is_valid_json(self):
        import json
        cfg = json.loads(inst.FASTFETCH_JSONC.format(user="alice"))
        self.assertEqual(cfg["logo"]["source"], "/home/alice/.config/fastfetch/logo.txt")
        self.assertIn("os", cfg["modules"])

    def test_logo_has_backslashes_intact(self):
        self.assertIn("/\\", inst.FASTFETCH_LOGO)

    def test_fonts_and_fastfetch_in_base(self):
        argv = dict(inst.build_plan("/dev/sda", "mesa"))["Installing Jupiter base (this takes a while)"]
        self.assertIn("fastfetch", argv)
        self.assertIn("fontconfig", argv)


class Fstab(unittest.TestCase):
    def test_fstab(self):
        text = inst.fstab_text("ROOT-UUID", "ESP-UUID")
        self.assertIn("UUID=ROOT-UUID\t/\text4", text)
        self.assertIn("UUID=ESP-UUID\t/boot/efi\tvfat", text)


if __name__ == "__main__":
    unittest.main()
