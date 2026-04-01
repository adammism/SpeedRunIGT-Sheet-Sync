# SpeedRunIGT Sheet Sync

Small Python CLI for **[SpeedRunIGT](https://github.com/RedLime/SpeedRunIGT)** to upload completions to a google sheet automatically.

**Source:** [qalue/SpeedRunIGT-Sheet-Sync](https://github.com/qalue/SpeedRunIGT-Sheet-Sync)

**Example Sheet (mine):** [open in Google Sheets](https://docs.google.com/spreadsheets/d/1bQQxu4zg-OugU3jmwAKL7npmBW6-B6cXuyM2jCJZMOE/edit?usp=sharing)

---

## Prerequisites

- **Python 3.9+**
- Google Cloud project with **Google Sheets API** (and Drive scope as used by the client) enabled
- A **service account** JSON key; share your spreadsheet with that account’s email (**Editor**)
- SpeedRunIGT writing `*.json` runs into a folder you set in config

---

## Install

```bash
git clone https://github.com/qalue/SpeedRunIGT-Sheet-Sync.git
cd SpeedRunIGT-Sheet-Sync
python -m venv .venv
```

**Windows (PowerShell)**

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies: `gspread`, `google-auth`, `watchdog`.

---

## Google setup (once)

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. Enable **Google Sheets API** (Drive is included in the auth scopes this tool uses).
3. **IAM & Admin → Service accounts → Create** → **Keys → Add key → JSON** and download the key file.
4. In Google Sheets, **Share** the target spreadsheet with the service account email (`…@….iam.gserviceaccount.com`) as **Editor**.
5. Copy the spreadsheet ID from the URL:  
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`

---

## Configuration

```bash
copy config.example.json config.json
```

On macOS/Linux: `cp config.example.json config.json`

### `config.json` fields

| Key | Description |
|-----|-------------|
| `records_dir` | Folder where SpeedRunIGT writes `*.json` run files. |
| `credentials_path` | Path to the service account JSON key. |
| `spreadsheet_id` | ID from the sheet URL. |
| `worksheet_name` | Worksheet tab name (e.g. `Raw Data`). Created if it does not exist. |

Paths that are **not** absolute are resolved relative to the **directory containing `config.json`** (not your shell's current directory).

```json
{
  "records_dir": "<RECORDS PATH>",
  "credentials_path": "service-account.json",
  "spreadsheet_id": "<SPREADSHEET_ID>",
  "worksheet_name": "Raw Data"
}
```

---

## Usage

```bash
python main.py --help
```

| Command | Description |
|---------|-------------|
| `python main.py --watch` | Watch `records_dir` for new/changed `.json` files. **Ctrl+C** to stop. |
| `python main.py --scan-all` | Upload every `*.json` in `records_dir` (respects sync state unless forced). |
| `python main.py --file run.json` | One file: full path or filename inside `records_dir`. |
| `python main.py --scan-all --force` | Re-upload all runs, ignoring `.synced_runs.json` dedupe. |

Use `--config path\to\config.json` to point at a config file elsewhere.

### Behaviour

- Only **`is_completed: true`** runs are uploaded.
- Runs with **`is_cheat_allowed: true`** are skipped.
- **Layout:** row 1 = headers; rows 2–3 blank; **data from row 4**. New rows **insert at row 4** (newest first).
- Durations are sent as **`H:MM:SS`** with user-entered parsing for Google Sheets.
- Console may print an inferred **spawn biome** (Adventuring Time); it is **not** written to the sheet.

