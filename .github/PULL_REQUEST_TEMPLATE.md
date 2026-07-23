## What this changes


## Why


## Safety checklist

- [ ] Nothing here can submit an application (no `.submit()`, `.requestSubmit()`,
      submit-button click, or Enter keypress in a field)
- [ ] Nothing here can put a claim on a resume that isn't already true
- [ ] Any limit or cap I added is reported to the user as a number, not silent
- [ ] No personal data or secrets in tracked files, logs or error messages
- [ ] Tests point at a temp directory, never real user data

## Checks run

```
python3 scripts/check_never_submits.py
python3 scripts/check_extension_never_submits.py
python3 scripts/check_safe_logging.py
python3 scripts/release_check.py
```

## How I verified this

> Measurements beat assertions — if this touches the scan, say what board you
> ran it against and what the numbers were.
