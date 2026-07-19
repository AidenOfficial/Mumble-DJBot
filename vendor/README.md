# vendor/

Third-party dependencies bundled with the repo so that `pip install` never
needs to reach an external git host at build time (the NAS network cannot
reliably clone from codeberg.org).

## pymumble

- Upstream: <https://codeberg.org/pymumble/pymumble>
- Version: 2.0.0, commit `34c47212b0162a87baa5b74638d50f8ee45777bf`
- License: GPLv3 (see `pymumble/LICENSE`)
- Installed via `./vendor/pymumble` in the top-level `requirements.txt`.

To update: clone upstream at the desired commit, replace the `pymumble/`
directory (drop `.git`, `docs/`, `examples/`, `tests/`, lock files), and
record the new commit hash here.
