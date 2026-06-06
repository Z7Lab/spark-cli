"""Config single-source-of-truth guards (lib/sparkcore.py `_CONFIG`)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import sparkcore

REPO = Path(__file__).resolve().parent.parent


class TestConfigSchema(unittest.TestCase):
    def test_keys_and_envs_unique(self):
        keys = [c["key"] for c in sparkcore.config_schema()]
        envs = [c["env"] for c in sparkcore.config_schema()]
        self.assertEqual(len(keys), len(set(keys)), "duplicate config key")
        self.assertEqual(len(envs), len(set(envs)), "duplicate env var")

    def test_defaults_and_envmap_derived_from_schema(self):
        self.assertEqual(set(sparkcore._DEFAULTS), {c["key"] for c in sparkcore.config_schema()})
        self.assertEqual(set(sparkcore._ENV_MAP), {c["env"] for c in sparkcore.config_schema()})

    def test_every_row_complete(self):
        for c in sparkcore.config_schema():
            for field in ("key", "default", "env", "type", "help", "init"):
                self.assertIn(field, c, f"{c.get('key')} missing {field}")
            self.assertIn(c["type"], ("str", "int", "bool"))

    def test_shipped_example_matches_schema(self):
        """templates/spark.json.example must equal the derived example (regenerate on change)."""
        shipped = json.loads((REPO / "templates" / "spark.json.example").read_text())
        self.assertEqual(shipped, sparkcore.config_example(),
                         "spark.json.example is stale — regenerate from sparkcore.config_example()")

    def test_env_override_is_typed(self):
        os.environ["SPARK_COMFY_PORT"] = "9999"
        os.environ["SPARK_DOCKER_ROOTLESS"] = "true"
        try:
            cfg = sparkcore.load_config()
            self.assertEqual(cfg["comfy_port"], 9999)      # int-coerced
            self.assertIs(cfg["docker_rootless"], True)     # bool-coerced
        finally:
            del os.environ["SPARK_COMFY_PORT"], os.environ["SPARK_DOCKER_ROOTLESS"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
