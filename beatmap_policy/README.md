# Beatmap Policy

This plugin provides beatmap ranking policy management for g0v0-server, allowing server administrators to control which beatmaps are eligible for performance point (PP) calculations.

This plugin is departed from the original blacklist function in g0v0-server.

## Database Tables

This plugin creates two tables:

- `plugin_beatmap_policy_allowed_beatmaps` - Stores beatmaps allowed for PP calculation (used in allowlist mode)
- `plugin_beatmap_policy_blocked_beatmaps` - Stores beatmaps blocked from PP calculation (used in blocklist mode)

Both tables support per-gamemode or all-mode entries.

### Schema

All entries in both tables have the following schema:

| Column Name | Type | Description |
|-------------|------|-------------|
| `id` | `int` | Primary key (Auto increment) |
| `beatmap_id` | `int` | The ID of the beatmap |
| `gamemode` | `GameMode` \| `null` | The gamemode this entry applies to. `null` means all gamemodes 

## Configuration

All configuration options use the `beatmap_policy_` prefix in your `.env` file.

| Environment Variable | Type | Default | Description |
|---------------------|------|---------|-------------|
| `beatmap_policy_enabled` | `bool` | `True` | Enable or disable the plugin |
| `beatmap_policy_policy` | `"allowlist"` \| `"blocklist"` | `"allowlist"` | The ranking policy mode. `allowlist` only allows PP for approved beatmaps; `blocklist` allows all except blocked beatmaps |
| `beatmap_policy_force` | `bool` | `False` | Whether to force the policy on all beatmaps (ignore whether the beatmap is ranked or not). |
| `beatmap_policy_autoban` | `bool` | `False` | Automatically ban suspicious beatmaps when fetched. Only works in `blocklist` mode |
| `beatmap_policy_suspicious_score_check` | `bool` | `True` | Check if scores exceed the maximum PP threshold |
| `beatmap_policy_max_pp` | `int` | `3000` | Maximum PP allowed for a single score. Scores exceeding this are set to 0 PP |
| `beatmap_policy_running_mode` | `"listener"` \| `"calculator"` | `"listener"` | How the plugin operates (see Running Modes below) |
| `beatmap_policy_calculator` | `str` | `"performance_server"` | The underlying calculator to use when running in `calculator` mode |

### Example `.env` Configuration

```env
# Basic blocklist configuration
beatmap_policy_enabled=True
beatmap_policy_policy=blocklist
beatmap_policy_autoban=True
beatmap_policy_max_pp=2000

# Or allowlist configuration
beatmap_policy_enabled=True
beatmap_policy_policy=allowlist
beatmap_policy_max_pp=3000
```

## Running Modes

### Listener Mode (Default)

```env
beatmap_policy_running_mode=listener
```

In listener mode, the plugin listens to score processing events and:
1. Checks if the beatmap is ranked according to the policy
2. Checks if the score exceeds the PP cap
3. If either check fails, sets score PP to 0 and removes it from best scores, and then recalculates user's total PP and accuracy.

### Calculator Mode

```env
beatmap_policy_running_mode=calculator
beatmap_policy_calculator=performance_server
CALCULATOR=-beatmap_policy
```

In calculator mode, the plugin wraps another calculator and intercepts PP calculations. You must:
1. Set `CALCULATOR` to `-beatmap_policy` in your main configuration
2. Set `beatmap_policy_calculator` to your original calculator (e.g., `performance_server`)

This mode applies PP policy checks during calculation rather than after score processing. **We recommend using this mode for better performance and compatibility. And this plugin only works in this mode when using `recalculate.py`**

## Suspicious Beatmap Detection

When `beatmap_policy_autoban` is enabled (blocklist mode only), the plugin analyzes beatmaps for:

- **Object density**: Detects impossible note density (>3000 BPM equivalent)
- **Object count**: Flags beatmaps with excessive objects (>500k for most modes, >30k for Taiko)
- **Position anomalies**: Detects objects placed outside normal play area
- **Slider abnormalities**: Flags sliders with excessive repeats (>5000)

Suspicious beatmaps are automatically added to the `plugin_beatmap_policy_blocked_beatmaps` table.

## Migrate from `banned_beatmaps` in g0v0-server

The migration script will automatically transfer data from the old `banned_beatmaps` table to the new `plugin_beatmap_policy_blocked_beatmaps` table during the first migration. The old table will be dropped after migration. If you need to revert, the downgrade script will recreate the `banned_beatmaps` table and transfer data back from `blocked_beatmaps`.
