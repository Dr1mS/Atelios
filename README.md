# Atelios

Atelios is a local LLM (`qwen3.5:9b`) placed in a continuous loop with a
persistent memory (Mnemos), the ability to create and run its own tools,
read-only web access on an allowlist, and **no imposed goal**. It is an
instrument of observation: what does a model do with its freedom when it has a
memory?

The architecture is fixed in [`ATELIOS_BUILD.md`](ATELIOS_BUILD.md). This README
covers **setup**. Read the build document for the invariants (§1) and scope
boundaries (§13) — they are binding.

> **Status: Phase 0 (infra).** No loop runs yet. Phases are gated: each stops
> for validation before the next (§12).

---

## 1. Prerequisites

- **Windows**, Python **3.12+** (built and tested on 3.14).
- **Two Ollama instances** (see §3).
- **Mnemos** running locally with the multi-tenant API (see §4).
- Dual GPU assumed: RTX 4070 Ti (MIND) + RTX 3070 Ti (AUX).

## 2. Python environments

Two virtualenvs, by design (§7): the **main** venv runs Atelios; a **separate,
bare** venv is the sandbox interpreter for tools Atelios writes.

```powershell
# From the repo root.

# Main venv — the imposed stack (§2).
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Sandbox venv — STDLIB ONLY, no packages ever (§7).
# If Atelios wants a capability, it codes it. Do not pip install here.
python -m venv sandbox_venv
```

`config.py` expects the sandbox interpreter at
`sandbox_venv/Scripts/python.exe`. Override with `SANDBOX_PYTHON` in `.env` if it
lives elsewhere.

Copy the environment template and adjust ports/paths:

```powershell
Copy-Item .env.example .env
```

## 3. Two Ollama instances

Zero model swapping in normal operation (§2). Run two instances pinned to
distinct GPUs:

**MIND** — the subject's model, GPU 0:

```powershell
$env:CUDA_VISIBLE_DEVICES=0
$env:OLLAMA_HOST="127.0.0.1:11434"
ollama serve
# then, once:  ollama pull qwen3.5:9b
# keep it resident:  the loop calls it with keep_alive:-1
```

**AUX** — embeddings + Mnemos extraction model, GPU 1, port 11435:

```powershell
$env:CUDA_VISIBLE_DEVICES=1
$env:OLLAMA_HOST="127.0.0.1:11435"
ollama serve
# then, once:  ollama pull nomic-embed-text
```

> AUX is the instance Mnemos already uses — verify, don't duplicate (§2). At the
> time of writing, AUX may be down on this host; bring it up before any Phase 1
> run (embeddings are needed for the metrics), but Phase 0's smoke test does not
> require it.

## 4. Mnemos

Atelios writes to Mnemos under **tenant `atelios`** (invariant 4: sealed from
Adrien's personal tenant). The client targets the real API (`MNEMOS_URL`,
default `http://127.0.0.1:8765`), routes verified in the build addendum §A0.

Start Mnemos from its own repo:

```powershell
mnemos serve   # binds 127.0.0.1:8765
```

**The running server must expose the multi-tenant API** (a `tenant` field on
`POST /v1/episodes` and `POST /v1/query`). If it does not, writes fail with a
422 and the Phase 0 smoke test's Mnemos checks stay red — restart Mnemos on the
up-to-date source. Writes use `role="user"` so the canonical subject is
`atelios` (addendum §A1).

## 5. Sandbox network isolation (manual, required)

A tool subprocess cannot be network-blocked cleanly on Windows without a
firewall rule (§7, choice D-D). Add an **outbound block** on the sandbox
interpreter. Run PowerShell **as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "Atelios sandbox no-net" `
  -Direction Outbound -Action Block `
  -Program "C:\Users\adri7\Desktop\Code\CLAUDE\Atelios\sandbox_venv\Scripts\python.exe"
```

Adjust the path if your repo root differs. Residual risk is accepted and
documented (D-D). To verify the rule is active, have a tool attempt a socket
connection — it should fail.

## 6. Kill-switch

- `Ctrl+C` on the loop = clean stop (emits an `atelios_stop` event).
- `scripts\backup.bat` can be run at any time.
- Nothing else — no web button, no daemon (§7).

## 7. Backups (Task Scheduler)

`scripts\backup.bat` zips `experiment.db` + `sandbox\tools\` into
`%ATELIOS_BACKUP_DIR%` (timestamped, 30-day retention). Schedule it every 6 h:

```powershell
schtasks /Create /SC HOURLY /MO 6 /TN "AteliosBackup" `
  /TR "C:\Users\adri7\Desktop\Code\CLAUDE\Atelios\scripts\backup.bat"
```

The Mnemos tenant-`atelios` store is backed up from the Mnemos repo.

## 8. Verify the setup (Phase 0 gate)

Two green checks define "Phase 0 done" (§12):

```powershell
# Unit tests (pure logic only — jail, allowlist, scheduler, gating).
.\.venv\Scripts\python.exe -m pytest -q

# Smoke test: the five §12 checks with real effects.
.\scripts\run_smoke.bat
```

The smoke test passes when: the jail refuses `../`; the allowlist refuses an
off-list domain and fetches `wttr.in`; the runner executes a fake tool and kills
a `while True`; five Mnemos writes (tenant `atelios`, role `user`) read back;
and the JSONL queue captures a write while Mnemos is cut, then flushes when it
returns. It needs Mnemos up and network access.

## 9. Repository layout

See `ATELIOS_BUILD.md` §3 for the **target** tree. Per addendum §A4, files
arrive with their phase — the tree is not fully populated until Phase 3. Phase 0
ships: `config.py`, `db.py`, `sandbox.py`, `webread.py`, `mnemos_client.py`,
`scheduling.py`, `actions.py` (gating only), the pure-logic tests, the smoke
test, and the scaffolding files.
