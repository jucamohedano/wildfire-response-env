# Extraction provenance and release restrictions

Source repository: https://github.com/jucamohedano/Los_Panaderos

Source revision: `ba9cf90a4f4834d76e30a3e97f87d420e4cabdee`

Source branch: `codex/regional-wildfire-response-v2`

The source fork derives from https://github.com/Fedeloz/Los_Panaderos. Authorship
and earlier development remain attributable to that project's contributors; the
snapshot is not a claim of sole authorship. Its history remains available at the
source revision. This repository starts a deliberately scoped snapshot, not an
import of the entire application history or unrelated historic secrets.

Imported into `envs/wildfire_response/core/src/`:

- `envs/__init__.py` and tracked `envs/wildfire_response/**` source, tests,
  synthetic scenario/evidence metadata and previously measured reports.
- `simulator/{__init__,engine,terrain,contacts,policy,oracle}.py` and
  `simulator/static/maps/brunete-illustrated.json`.

The contact helper is replaced by a synthetic-only implementation. The old
monorepo-specific nested package metadata/build backend and container recipes
are superseded by the new core/OpenEnv packages. Core benchmark inputs are
unchanged and independently checksum-verified. Public facades and root tooling
are new. The Hackspain checkout is not deleted, rewired or otherwise modified.

Excluded: live application/workflows/MCP configuration, `.env`, credentials,
contact destinations, runtime downloads, cloud connection details, model weights,
checkpoints, private scoring traces, browser assets and unrelated project files.

The root code licence was not established in the source project. No new open-source
licence is assumed or invented here. The bootstrap map/data assets also need
redistribution review before public release. Qwen model licensing does not license
this simulator. Public evidence source citations and source hashes remain with
the corresponding data packs; synthetic labels are not real-world observations.

Layout reference only: https://github.com/adithya-s-k/FineEnvs/tree/410fb3454a903646f814bb752513c56fed3eae78/07-simulation-environments/portsim-v1

No PortSimEnv code, figures or Barcelona data were copied. Keep this repository
private until the outstanding rights are resolved. No Hugging Face publication
or claim of benchmark/model readiness is implied by repository creation.
