# Agent instructions — `data` branch

This branch holds **data, never code**: the TOML configs that say *what* gets
patched, and the JSON state the watcher and build jobs read and write back. The
engine, workflows, scripts, `CONFIG.md` and `docs/` all live on `main`.

Read [README.md](README.md) on this branch first — it is the per-branch contract
for the layout below. The long-form reference is
[`docs/storage-and-branches.md`](https://github.com/nullcpy/rvb/blob/main/docs/storage-and-branches.md)
and the key-by-key TOML reference is
[`CONFIG.md`](https://github.com/nullcpy/rvb/blob/main/CONFIG.md) (both on `main`).

Every path here is consumed **verbatim** by jobs that run
`.github/scripts/fetch_data_branch.sh` right after checkout. Paths, file names and
key names are frozen interfaces, not conventions. The rules below are the ones
that cause damage when broken.

1. **Don't hand-edit generated JSON.** `state/*.json` and
   `configs/stable_build.json` / `configs/beta_build.json` are machine state:
   written by the watcher (`commit_data_branch.sh`) and, for
   `state/build_content_hashes.json`, by the build job after a successful upload.
   They are regenerable — fix a bad value by fixing the input and letting the next
   run rewrite them, or by the documented recovery path in `README.md` (`state/`
   is rebuildable; `configs/` TOMLs are not).

2. **TOMLs are the only human intent, and the only copy of it.** An app's block in
   `configs/patches/<author>.toml` is the sole record of how that app is built —
   lose it and the knowledge is gone. Keep one file per patch-source family, keep
   fields grouped as the neighbouring blocks do, and keep the comment that explains
   a non-obvious choice (`direct-dlurl` vs `archive-dlurl`, a pinned `version`, an
   `included-patches` list) with the field it explains.

3. **Disable, never delete.** An app that should stop building gets
   `enabled = false`; the block, its URLs and its comments stay for later re-enable.
   This is a project rule, not a style preference.

4. **Publish before you fetch.** `fetch_data_branch.sh` **overwrites** local
   `configs/` and `state/` from `origin/data`. Hand-edited TOMLs that were never
   published are lost. The write path is
   `bash .github/scripts/push_data_configs.sh "<msg>"` from a `main` checkout (this
   is also where the TOMLs are normally edited — they are gitignored on `main`).

5. **Writer globs are boundaries.** CI commits only `*.json` under `configs/` and
   `state/`; the human tool commits only `*.toml`. Neither can clobber the other.
   The consequence for you: CI *cannot delete* a TOML, so a **renamed or removed
   config file must be deleted on `data` explicitly**, or the old name lingers and
   keeps building/lingering as an orphan.

6. **A block's key is app identity.** The table name (`YouTube-Morphe`,
   `Reddit-Morphe-Experimental`, …) is derived into the asset file prefix and is
   the key of `state/built_shape.json` and `state/build_content_hashes.json`.
   Renaming a key, or changing `variant` / `sub-variant`, makes the watcher see a
   *new* app and forces a full build; it does not migrate state. Only do it
   deliberately.

7. **Wire formats are frozen: add, never reinterpret.** Key names here, the
   download-source keys, the generated-JSON keys, the branch names and the asset
   filename grammar (`<file-prefix>-v<version>-<arch>.apk`) are read by consumers
   already in the wild (phones polling the `update` branch, the website, Obtainium).
   A new capability gets a new key; an existing key never silently changes meaning.
   Filename/arch parsing has exactly one implementation,
   `main:.github/scripts/naming.py` — never mirror or re-derive it here.

8. **Download sources are tried in a fixed order and the first hit wins.**
   `cache_repo direct github archive apkmirror uptodown apkpure apkcombo`. The
   two URL-shaped keys mean different things and are not interchangeable:
   - `direct-dlurl` — one raw file; no version discovery, no split merging.
   - `archive-dlurl` — scrapes an archive.org item listing and merges
     `.apkm/.xapk/.apks` splits. The item's files must be named
     `<pkg>-<version>[-<code>]-<arch>.<ext>` (arch ∈
     `all|arm64-v8a|arm-v7a|x86|x86_64`) to be discoverable.
   Use `direct-dlurl` only when no store resolves and the file's name can't match
   the archive grammar; prefer a normal store key when one works.

9. **Never commit secrets or local scratch.** Signing keys are CI secrets only.
   `temp/` is scratch, `STATE.md` is a local, gitignored working note — neither
   belongs in a commit or a push. `configs/available_patches/*.txt` are generated
   reference dumps of each source's patch list (read them when choosing
   `included-patches` / `excluded-patches`); they are not inputs and are not
   hand-edited.

10. **LF line endings.** `*.json` and `*.toml` are byte-compared by the pipeline;
    a CRLF checkout breaks parsing and diffs. Keep one trailing newline.

11. **Commits:** Conventional Commits, one logical step each (`configs: add
    <app> …`, `chore: …` for state/config rewrites the tooling would produce), with
    a body that says *why*. Do not push, force-push, merge or rewrite state
    history unless asked — publishing `data` changes what the next scheduled run
    builds. In this fork all pushes and CI dispatches are the maintainer's.

12. **Validate before claiming done.** Parse every TOML you touched
    (`python3 -c 'import tomllib,sys,glob;[tomllib.load(open(f,"rb")) for f in sys.argv[1:]]' configs/*.toml configs/patches/*.toml`),
    confirm the app key you wrote matches the block's `pkg-name`/`brand`/`variant`
    intent, and remember the real proof is a build: a config that parses can still
    resolve no version, no arch, or a base-only bundle.

Fork notes: `origin` is `hisok9/rvb` (the pruned keeper-set fork); the `upstream`
push URL is disabled — never push upstream. Publishing is `git push origin data`
after a fresh fetch, done by the maintainer.
