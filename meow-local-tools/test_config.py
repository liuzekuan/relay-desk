import json
from pathlib import Path
import tempfile
import unittest

from provider_config import ImportIssue, load_connections


class ConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.toml"
            path.write_text(text, encoding="utf-8")
            return load_connections(path)

    def test_multiple_relays_use_shared_model_and_responses(self):
        result = self.load('''\ufeffmodel = "custom-codex"
[[providers]]
name = "First"
api = "https://first.example/v1"
key = "fixture-first-key"
[[providers]]
name = "Second"
api = "https://second.example/v1"
key = "fixture-second-key"
''')
        self.assertEqual(len(result), 2)
        self.assertEqual({item["preset"]["mode"] for item in result}, {"gpt"})
        self.assertEqual({item["preset"]["model"] for item in result}, {"custom-codex"})
        self.assertNotIn("fixture-first-key", json.dumps([item["preset"] for item in result]))

    def test_duplicate_names_are_rejected(self):
        block = '[[providers]]\nname="Relay"\napi="https://api.example/v1"\nkey="fixture-key"\n'
        with self.assertRaises(ImportIssue):
            self.load(block + block.replace('"Relay"', '"relay"'))

    def test_key_rotation_keeps_same_identity(self):
        block = '[[providers]]\nname="Relay"\napi="https://api.example/v1"\nkey="fixture-old-key"\n'
        first = self.load(block)[0]
        second = self.load(block.replace("fixture-old-key", "fixture-new-key"))[0]
        self.assertEqual(first["preset"]["id"], second["preset"]["id"])
        self.assertNotEqual(first["key"], second["key"])

    def test_invalid_entries_never_echo_secrets(self):
        for text in (
            '[[providers]]\nname="Relay"\napi="https://api.example"\nkey="fixture-secret',
            '[[providers]]\nname="Relay"\napi="https://api.example?key=fixture-secret"\nkey="fixture-secret"',
            '[[providers]]\nname="Relay"\napi="http://api.example"\nkey="fixture-secret"',
            '[[providers]]\nname="Relay"\napi="https://api.example:99999"\nkey="fixture-secret"',
            '[[providers]]\nname="Relay"\napi="https://api.example"\nkey=""',
        ):
            with self.subTest(text=text):
                with self.assertRaises(ImportIssue) as error:
                    self.load(text)
                self.assertNotIn("fixture-secret", str(error.exception))

if __name__ == "__main__":
    unittest.main()
