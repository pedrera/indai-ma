import os
import unittest
from unittest.mock import patch

from runtime_config import LLMRuntimeConfig


class LLMRuntimeConfigTests(unittest.TestCase):
    def test_llm_timeout_default_is_640_seconds(self):
        # Isolate the implicit default from both process and local .env values.
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(LLMRuntimeConfig.from_environment().timeout_seconds, 640)

    def test_explicit_llm_timeout_environment_value_overrides_default(self):
        with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "77"}, clear=True):
            self.assertEqual(LLMRuntimeConfig.from_environment().timeout_seconds, 77)


if __name__ == "__main__":
    unittest.main()
