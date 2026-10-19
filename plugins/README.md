# Forensic-module plugins

Anything you add here is a **forensic module**: it answers one question about one media file and returns findings, confidence,
limitations, tool versions and evidence in the standard shape (`dfir.forensics.base.ModuleResult`). It then appears in
*Settings -> Forensic modules* and in every case's *Forensic modules* page, with its results stored in the evidence vault and
the chain-of-custody ledger like the built-in six.

Two ways to add one (neither touches the core code):

1. **Drop a `.py` file in this folder** (or the folder named by `DFIR_PLUGIN_DIR`). See `example_noise_floor.py.txt`: rename it to
   `.py` to try it. These files are *your own code* and run with the app's permissions; only add files you trust.
2. **Install a package** that exposes an entry point in the `dfir.forensics` group:

   ```toml
   [project.entry-points."dfir.forensics"]
   my_noiseprint = "my_package.module:NoiseprintModule"
   ```

## Optional models install independently

A module lists the model weights it needs in `model_specs` (a `dfir.core.model_store.ModelSpec`) and the packages in
`requirements`. If something is missing the module is listed as *unavailable* with the exact command to fix it, and every other
module keeps working:

```bash
cd backend && uv run python ../scripts/setup_models.py <your_model_key>   # downloads and SHA-256 pins only that model
```

## Rules a module must follow

* Mark every finding as `observation` (a measurement), `supported` (error rates measured against ground truth),
  `hypothesis` (not validated) or `unknown` (could not be assessed). Missing evidence is `unknown`, never "clean".
* Never claim an exact device or an exact AI model. Confidence is capped by the validation tier your module declares in
  `validation_results.json` (`none` caps at 0.35, `synthetic` at 0.70, `real` at 0.95).
* State what the method cannot tell you (`limitations`) and which library/model versions produced the numbers (`library_versions`).
* Set `DFIR_PLUGIN_API = 1` in drop-in files; a file written for a different contract version is refused, not half-loaded.
