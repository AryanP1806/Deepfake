# Vendored third-party detector code

| Dir | Upstream | Commit | Licence |
|---|---|---|---|
| `spai/` | https://github.com/mever-team/spai | 8ff7b3b6779b4fcb43cf313471d9cb1c62d129a4 | Apache-2.0 |
| `TruFor/` | https://github.com/grip-unina/TruFor | ae54475df6f41a491d7615100feb19263dec13f7 | GRIP licence — informational / non-profit use only |
| `SelfBlendedImages/` | https://github.com/mapooon/SelfBlendedImages | d6d9a351f32334ae9a3a250872c05836e0dd0f2c | Research use only |

Local patches (kept minimal):
- `spai/spai/models/backbones.py`: `import clip` wrapped in try/except (OpenAI CLIP only used by non-default backbones).

Weights are downloaded by `scripts/setup_models.py` and pinned by SHA-256 in `models.lock.json`
(TruFor zip also matches the upstream MD5 7bee48f3476c75616c3c5721ab256ff8).
