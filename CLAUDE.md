# PLAVE YouTube Analytics

The full specification is in SPEC.md. Read it before any task and follow it exactly.

## Rules
- Implement one step of SPEC.md section 13 at a time; stop after each step so I can test.
- PostgreSQL (Supabase) only, schema `plave`. Never MySQL syntax.
- Never commit .env or print secrets.
- Classification rules live only in src/config.py.
- No SQL views.

## Commands
- Run pipeline: python src/main.py
- Tests: pytest