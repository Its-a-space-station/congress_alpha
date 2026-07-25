# Lessons

Use append-only dated entries.

## Template
- Date:
- What went wrong:
- Preventive rule:
- How to check next time:

## 2026-07-25 — Verify CLIs the way users actually invoke them
- What went wrong: the M5a dashboard crashed for the user with
  `ModuleNotFoundError: No module named 'app.core'; 'app' is not a package`
  even though my smoke check passed. Root cause: `streamlit run
  app/dashboard/app.py` puts the script's directory first on sys.path, and the
  file `app.py` there shadowed the `app` package. My AppTest-based check ran
  under pytest's `pythonpath=["."]`, which resolved the import differently —
  so the verification did not replicate the user's real invocation.
- Preventive rule: for any user-facing entrypoint, verify with the EXACT
  documented command (as a subprocess), not only a library-level equivalent.
  Also: never name a script the same as its top-level package when its own
  directory can land on sys.path; keep the project pip-installed so imports
  resolve from any cwd.
- How to check next time: run the documented launch command verbatim in a
  clean subprocess (no pytest pythonpath) and curl/inspect the result before
  claiming an entrypoint works.
