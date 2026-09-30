# Full setup: HealthRelay with a Hermes Agent

This guide sets up the whole chain, in order, for one person on one machine:

1. **HealthRelay**: the iPhone app and your own receiver (this repository).
2. **[hermes-healthrelay](https://github.com/mwdearing/hermes-healthrelay)**: read-only access to the receiver database for your agent.
3. **[hermes-health-insights](https://github.com/mwdearing/hermes-health-insights)**: local analysis (trends, concern checks, nutrition, labs).
4. **[hermes-medlog](https://github.com/mwdearing/hermes-medlog)**: a deterministic medication log that can import dose events from your receiver.

Each step ends with a check. Do not start the next step until the check passes. Steps 2 to 4 are independent add-ons to step 1; you can stop after any of them.

> [!IMPORTANT]
> Informational only, not medical advice. None of these tools decides, changes or advises on a dose, a schedule or a treatment; questions like that belong to your prescriber or pharmacist.
> Health data is sensitive. Use a local model, or one you trust with it, and keep names, doses and raw values out of public channels, notes and issues. Never paste your database path, pairing page or pairing code into chat logs.

You will need: an iPhone on iOS 18 or later (medication dose events need the Health app's Medications feature on iOS 26 or later), a macOS or Linux computer that stays on, [`uv`](https://docs.astral.sh/uv/), `pipx`, [Hermes Agent](https://github.com/NousResearch/hermes-agent), and Python 3.11 or later.

## 1. HealthRelay: app and receiver

Follow the [README](../README.md#set-up-the-bridge) and the [setup guide](setup.md): build and sign the app (use the latest **stable** release, not a beta), choose and verify a private route, install the receiver, run `health-bridge setup`, keep the printed receiver command running under your service manager, then pair and turn on Automatic Sync.

**Checks**
- The printed local health URL returns `{"status":"ok"}`, and so does the same URL opened on the physical iPhone.
- After the first sync, `health-bridge status --db <database>` shows recent syncs and record counts (or open the app's Activity Log): at least one successful upload.
- For medication dose events: in Health > Profile > Privacy > Apps > HealthRelay, medication access is on for each medication you want (access can be per medication).

Note the path of the receiver database (the file the printed receiver command uses). You will write it down once, in step 2.

## 2. hermes-healthrelay: read-only access for your agent

```bash
hermes plugins install mwdearing/hermes-healthrelay --no-enable
hermes plugins enable healthrelay
```

Write the database path on one line into `~/.config/healthrelay/db-path` (create the folder if needed). This one file is shared by steps 2 to 4. It must be in the HOME of the process that runs Hermes.

**Checks**
- `"${HERMES_HOME:-$HOME/.hermes}/plugins/healthrelay/bin/healthrelay-mcp" --check` ends with `result: OK (could start`.
- Start a NEW Hermes session (the MCP server only loads at session start). Ask the agent to run `get_bridge_status`, then `list_synced_metrics`: recent syncs and a non-empty metric list.
- If the tools are missing, load the `healthrelay-troubleshoot` skill (it covers `mcp package not installed` and a wrong HOME).

## 3. hermes-health-insights: local analysis

```bash
pipx install git+https://github.com/mwdearing/hermes-health-insights
hermes plugins install mwdearing/hermes-health-insights --no-enable
hermes plugins enable health-insights
```

Run the plugin install in an interactive terminal: it asks before installing its PyYAML dependency.

**Checks**
- `health-insights --version` prints a version. Try it on synthetic data first: `health-insights demo --out demo.sqlite && health-insights weekly --db demo.sqlite`.
- `health-insights concerns` (no `--db`; it reads the same `db-path` file) prints findings or a line saying nothing is flagged. If it prints `cannot open the receiver database`, fix the path from step 2; do not guess one.
- The optional profile, units and modules: see the `health-insights-setup` and `health-insights-modules` skills.

## 4. hermes-medlog: the medication log

```bash
pipx install git+https://github.com/mwdearing/hermes-medlog
hermes plugins install mwdearing/hermes-medlog --no-enable
hermes plugins enable medlog
```

**Checks**
- `medlog --version` prints a version. Try it on scratch data first: `export MEDLOG_HOME=$(mktemp -d)`, then `medlog add-med demo --name "Demo medicine" --time 08:00`, `medlog log demo --time 08:05`, `medlog missing --days 3`. Then close that shell so the scratch folder is not used by accident.
- Decide where the real log lives and make every process agree on it. Environment variables set in one shell are not seen by the scheduler or the agent's terminal, so put the settings in the config file `~/.config/medlog/config.json` (`{"data_dir": "...", "timezone": "..."}`). Environment beats the file, the file beats the defaults; an invalid file is an error.
- Register each medication with the `medlog-register-medication` skill: you give every value (name as the Health app shows it, dose, unit, times, start date); nothing is inferred. Then connect the import:
  ```bash
  medlog import-bridge --db "$(head -1 ~/.config/healthrelay/db-path)"          # preview: writes nothing
  medlog unmapped --days 14 --db "$(head -1 ~/.config/healthrelay/db-path)"     # names that reach no medication yet
  medlog map-apple "<name or concept id>" <id>                                  # only after you confirm the pairing
  medlog import-bridge --db "$(head -1 ~/.config/healthrelay/db-path)" --apply
  medlog check-med <id>                                                         # exit 0 means complete
  ```
  The database is opened read-only. A rerun finds nothing new, and a `--since YYYY-MM-DD` backfill skips duplicates.
- To let health-insights count missed doses (counts only, never names or doses): `health-insights modules enable medication_adherence`. It runs `medlog --json missing`, so it needs the same `medlog` and the same data folder as above.

## 5. Optional: scheduled jobs and automatic import
- A daily check-in (`medlog-checkin`) and a weekly report (`medlog-weekly-report`) are read-only recipes. The `medlog-checkin-cron` and `medlog-weekly-report-cron` skills walk you through a Hermes cron job: you approve it, run it once with delivery `local` and read the output, and only then switch it to a private channel.
- Importing after every sync is your choice (`medlog-healthrelay-import` skill): a user systemd path unit that runs `medlog import-bridge --db ... --auto`. `--auto` applies only clean, small changes and holds anything else for you to preview.

## Final check: the whole chain

| Question | Expected |
| --- | --- |
| `get_bridge_status` in a new Hermes session | recent syncs |
| `health-insights concerns` | findings or "nothing flagged", no database error |
| `medlog import-bridge --db ...` (preview) | `events=` a number, `unmapped=0` for medications you registered |
| `medlog missing --days 3` | only real gaps |
| `health-insights concerns` with the adherence module on | a counts-only line when doses were repeatedly missed, otherwise nothing |

## Upgrade and uninstall
- Plugins pinned to a commit move with `hermes plugins install <owner>/<repo> --force --ref <40-character sha>` (interactive terminal for health-insights), and the tools with `pipx install --force git+https://github.com/<owner>/<repo>@<sha>`.
- Uninstall in reverse order: `hermes plugins uninstall medlog health-insights healthrelay` one by one, `pipx uninstall medlog health-insights`, and remove `~/.config/healthrelay/db-path` if you want. Your medication log and the receiver database are yours and are not touched by any uninstall.
