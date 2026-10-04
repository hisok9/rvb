# AI edit log

Build-pruning + fork setup. Local checkout: `data`.

---

## Scope

- **8 keepers** in `configs/patches/*.toml` (43 files, 183 tables → 175 flips):

  | File | Keep |
  |---|---|
  | `photos.toml` | `GooglePhotos-DeVanced`, `GooglePhotos-rushiranpise`, `GooglePhotos-AkashSriram` |
  | `morphe.toml` | `YouTube-Morphe`, `YouTubeMusic-Morphe` |
  | `bufferk.toml` | `Truecaller-bufferk` |
  | `paresh.toml` | `Truecaller-Paresh` |
  | `hoodles.toml` | `CamScanner-hoodles` |

- **Mechanism:** `enabled = false` **on each table**, never file-level —
  `build.sh:168` does `toml_get "$t" enabled) || enabled=true` (table only, falls
  back to *true*). Deleting the line re-enables. Config was never deleted.
- **Pools:** `compile_patch_configs.py` → stable = 8, beta = 4
  (beta = the 4 keepers with `patches-version = "both"`).
- **Never touched:** `TG_TOKEN` (unset → notify skips), `APKS_REPO` (unset).

---

## Publish & triggers

- Publish = `git push origin data`. Nothing else.
- **No `push:` trigger** — `ci.yml` is schedule + dispatch only; `trace-verify.yml`
  matches only `scripts/`, `.github/traces/**`. A `configs/**` push runs nothing.
- Crons UTC: `00:11 04:46 08:08 12:52 16:29 20:03`. Or Actions → CI → Run workflow.
- **Manual CI:** `manual-ci.yml` never recompiles — `build_resolve_context.sh` only
  reads `patches-version`. Use `config_file=configs/config.manual.toml`
  (deterministic hand-written TOML). The `*_build.json` choices were upstream-stale.
- **Repo setting: Workflow permissions → Read and write.** `manual-ci.yml` declares
  **no `permissions:` block**, and `ci.yml`'s `build_beta`/`build_stable` don't
  either, while the nested `build.yml` requests `contents: write`.

### Fork traps

| Trap | Resolution |
|---|---|
| Fork inherits upstream's *generated* `configs/*_build.json` (17 apps), not your TOMLs | self-corrects on the first `ANYTHING_CHANGED=1` run (steps 7→8 order guarantees it) |
| `ANYTHING_CHANGED` never looks at TOMLs — flipping `enabled` sets no trigger | step 3 (base compile) always runs; steps 7 & 12 wait for an upstream signal |
| inherited `archive/beta.json` on `website` | **deleted** — `merge_archive_branch.sh:47` has an absent-case `else`; no `beta` release exists so it was never read |
| `config.manual.toml` | now the `GooglePhotos-AkashSriram` fixture |
| `data` branch has **no `.gitignore`** | keep scratch files outside the repo |

---

## Site — `hisok9.github.io`

- Created by **fork + rename** `nullcpy/nullcpy.github.io` → `hisok9/hisok9.github.io`
  (the `owner.github.io` name is what makes the default Pages URL resolve).
- Retarget: `rebuild-catalog.yml:41,56,60` `RVB_REPO: hisok9/rvb`; `script.js:8`
  `"owner"` (builds every download URL); `index.html` ×6; `_config.yml`;
  `rebuild_catalog.py` default.
- Rebrand **NullStore → My Store** (11 hits) + all `nullcpy` → `hisok9`.
- Rebuild gates cleared **via env only** — `rebuild_catalog.py` untouched:
  `MIN_RELEASES_THRESHOLD="1"` (default 10 vs 2 releases); `FORCE` exposed as a
  `force` dispatch input **defaulting false**, so the `MIN_RATIO 0.6` shrink breaker
  still protects scheduled runs.
- Result: 122 → **1 app**, 718 → **2 builds**, 2500 → **0** `nullcpy`.
- Workflows active: `rebuild-catalog`, `deploy-pages`, `notify`.

---

## Signing key

### The default is public

| Piece | Where |
|---|---|
| `ks.keystore` (BKS, 3708 B), `ks-p12.keystore` (PKCS12, 4178 B) | committed on `origin/main` |
| password `123456789` | `scripts/utils.sh:24` |
| alias `jhc` | `scripts/utils.sh:25` |
| fallback | `install_keystore.sh:13-16` → *"using repo keystores"* when both B64 secrets absent (always, on a fork — secrets aren't inherited) |

Opens with that password: `CN=ReVanced`, RSA-4096, 2023→2047,
SHA-256 `63:7C:22:6C:67:AE:C0:CD:BC:6F:49:CD:47:6D:52:47:F9:99:12:26:06:28:62:73:E1:62:33:A9:13:A0:88:B4`.
Blob `609170a1…` is byte-identical to `nullcpy/rvb`. Anyone can sign an update your
phone accepts as legitimate — hence replacing it.

### What signs what

| File | Format | Consumer | Lands on |
|---|---|---|---|
| `ks.keystore` | **BKS** (`00000002 00000014`) | patch CLI `--keystore=` (`utils.sh:3243`) | **the final APK** |
| `ks-p12.keystore` | PKCS12 (`30 82…`) | `apksigner sign` (`utils.sh:1444`) | merged-stock intermediate |

Same key pair, two formats. Plain `keytool` rejects `ks.keystore` until the BC
provider is loaded — the build does that in `build_install_bouncy_castle.sh`.
`check_sig` (`utils.sh:3326`) only validates *stock* downloads against `sig.txt`,
which is empty → never sees your key.

### Generate — **alphanumeric password only** (`utils.sh:1444`/`:3245` interpolate it unquoted)

```bash
mkdir -p ~/keys/my-store && cd ~/keys/my-store   # outside the repo, never commit
PASS='<strong-alphanumeric-password>'
ALIAS='mystore'

# 1. PKCS12 -> ks-p12.keystore (apksigner)
keytool -genkeypair -alias "$ALIAS" -keyalg RSA -keysize 4096 -validity 10000 \
  -dname "CN=My Store, OU=hisok9" \
  -keystore ks-p12.keystore -storetype PKCS12 -storepass "$PASS" -keypass "$PASS"

# 2. BKS -> ks.keystore (patch CLI — signs the final APK)
curl -sL -o bcprov.jar \
  https://repo1.maven.org/maven2/org/bouncycastle/bcprov-jdk18on/1.81/bcprov-jdk18on-1.81.jar
keytool -importkeystore \
  -srckeystore ks-p12.keystore -srcstoretype PKCS12 \
  -srcstorepass "$PASS" -srckeypass "$PASS" -srcalias "$ALIAS" \
  -destkeystore ks.keystore -deststoretype BKS \
  -deststorepass "$PASS" -destkeypass "$PASS" \
  -providerclass org.bouncycastle.jce.provider.BouncyCastleProvider \
  -providerpath ./bcprov.jar

# 3. both must report the SAME fingerprint
keytool -list -keystore ks-p12.keystore -storetype PKCS12 -storepass "$PASS"
keytool -list -keystore ks.keystore -storetype BKS \
  -providerclass org.bouncycastle.jce.provider.BouncyCastleProvider \
  -providerpath ./bcprov.jar -storepass "$PASS"

# 4. secrets, without printing key material
base64 < ks-p12.keystore | tr -d '\n' | pbcopy   # -> KEYSTORE_P12_B64
base64 < ks.keystore     | tr -d '\n' | pbcopy   # -> KEYSTORE_B64
```

**Back up both files + password offline first** — no escrow; lose them and no
installed app can ever be updated again.

### Four secrets on `hisok9/rvb`

`KEYSTORE_B64`, `KEYSTORE_P12_B64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`
→ `install_keystore.sh:23-34` overwrites the repo files and exports
`RVB_KEYSTORE_PASS`/`RVB_KEY_ALIAS`. Upstream's keystores stay committed, inert.

### Verified — Manual CI run #3, release `260177`

```
Installed custom ks.keystore (3783 bytes).
Installed custom ks-p12.keystore (4266 bytes).
Signing identity exported (alias: ***).      <- masked = registered secret
```

Both APKs, `apksigner verify --print-certs`:

```
CN=My Store, OU=hisok9
3549720878d33cc24562d81c9c3117cf08d1e031bf01c9d46ca497b00a68f70b
```

old `63:7C:22:6C…` absent; control (truncated APK) → `exit=1`, so `exit=0` is real.
`stable` release assets overwritten → site links serve the new build.
**All installed apps must be uninstalled + reinstalled** (signature changed).

---

## Build numbering — left alone

`build_resolve_version.sh`: `YY` + max(`26NNNN` release **or** git tag) + 1.
100 tags `260077`–`260177` + releases `260176`/`260177` → next is `260178`.
Resetting needs both sources cleared *or* a script edit. Decided against.

`NEXT_VER_CODE` feeds only the release tag, changelog/manifest, Telegram message
and the **Magisk module** `versionCode` (`utils.sh:4818`). The APK's Android
`versionCode` is the per-app `version-code` key (`build.sh:267`) — unaffected.

---

## State & outstanding

| | |
|---|---|
| `hisok9/rvb` `data` | `8fdf75d9`, clean, 0 unpushed |
| `hisok9/rvb` `website` | `d4f394a7` — manifests `260176`+`260177`, `beta.json` removed |
| `hisok9/hisok9.github.io` | `9edf048` — retargeted, rebranded, catalog rebuilt; live as **My Store** |
| signing | own key from `260177` on — keep `35497208…` stable forever |

**TODO**

- Catalog sizes stale by 4–8 B (`data.json` predates run #3) — URLs fine, self-heals
  at `Rebuild Catalog` (`23 */6 * * *`). Dispatch needs **admin**
- 7 of 8 keepers have never built — next `ci.yml` cron.
- Release `260176` still holds old-key APKs; nothing links to it, safe to delete.
