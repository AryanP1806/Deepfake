"""DFIR — Deepfake Investigation & Response System."""
import os

# Model weights are fetched only by scripts/setup_models.py. At runtime, never let
# huggingface_hub / open_clip contact the Hub (it would leak which models are in use).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
