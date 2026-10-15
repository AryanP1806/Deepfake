import os
import tempfile

# isolate every test run in a throwaway data directory (must be set before dfir is imported)
os.environ.setdefault("DFIR_DATA_DIR", tempfile.mkdtemp(prefix="dfir_test_"))
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["DFIR_NO_DOTENV"] = "1"  # tests must never call real external APIs with the user's keys
