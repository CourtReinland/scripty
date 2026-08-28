# Scripty

![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Offline](https://img.shields.io/badge/runs-offline%20by%20default-8a8a8a)

A **human-in-the-loop fiction trainer**. Pick a genre desk, set tone / length / summary, optionally upload a private training chunk. Scripty writes a champion, then a *challenger* that explores (new seed, temperature, and beat plan every time). You say **Better** or **Worse**. You are the only judge.

Each desk is its own author: short horror, literary, romance, thriller, slice-of-life, sci-fi, a generic/custom desk, plus fantasy and mystery. Lessons and champion history stay on that desk.

You feed cleaned reference chunks yourself. They are used **internally** as abstract style notes (pace, speech, opening move). They are never shown again in the UI, never quoted in a draft, never written into logs you see, and never exported. No living-author corpus is downloaded or vendored.

The older film script-supervisor still lives at `/film`. It is not this product.

---

## The loop

1. Start: **desk**, tone, length, story summary, optional reference upload.
2. First draft becomes the champion.
3. Generate a challenger — randomness is how the system explores a genre it does not yet understand.
4. Side-by-side. You click Better (promote) or Worse (discard).
5. Only Better is saved as the new champion. History stays visible.
6. The next generate recalls that desk’s lessons.

```
desk + summary + desk lessons + abstract style notes
        │
        ▼
   first draft  ──▶  CHAMPION
        │
        │  seed / temperature / plan mutation
        ▼
   CHALLENGER  ──▶  you: Better | Worse
        │
        ├─ Better → challenger is champion; lesson saved on the desk
        └─ Worse  → champion stays; lesson saved on the desk
```

---

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

scripty write start -g thriller -t "tight" -l short \
  -s "A courier has ninety minutes to deliver a key she has already lost."

scripty write generate 1
scripty write judge 1 better --note "faster cut"
scripty serve    # http://127.0.0.1:8787/
```

Offline, the mock provider writes seed-varied original prose so `pytest` and the UI work with no key.

**Live writing** uses xAI **grok-4.6** at reasoning effort **xhigh** when `XAI_API_KEY` is set (`https://api.x.ai/v1`). That is the default writer. Override with `SCRIPTY_WRITER_MODEL` / `SCRIPTY_WRITER_REASONING_EFFORT` if you must; do not silently drop to `high`.

```bash
export XAI_API_KEY=...
scripty write start -g literary -t "spare" -l short -s "Two siblings divide a house."
```

---

## CLI

| Command | What it does |
| --- | --- |
| `scripty write start -g DESK -t TONE -l short\|medium\|long -s SUMMARY [--ref-file]` | Open a desk session and write the first draft |
| `scripty write generate ID` | First draft, or an exploring challenger |
| `scripty write judge ID better\|worse` | Human verdict; only Better promotes |
| `scripty write show ID` / `history ID` | Current pair and champion lineage |
| `scripty write lessons -g DESK` | Lessons saved on that desk |
| `scripty write ref -g DESK --kind user_excerpt\|public_domain --title … --file …` | Private ingest (not printed back) |
| `scripty write next ID` | Next chapter (medium/long) |
| `scripty serve` | Writer UI at `/` (legacy film UI at `/film`) |

Desks: `horror`, `literary`, `romance`, `thriller`, `slice_of_life`, `science_fiction`, `custom`, `fantasy`, `mystery`.

---

## What this is not

- Not a scorer against any living author.
- Not a place we scrape or vendor books.
- Not a quote machine. References never reappear as text.

---

## Tests

```bash
python -m pytest -q
```

Writer tests use a tiny original fixture sentence — not a corpus. They cover first draft, Better/Worse, per-desk lessons and history, private refs (no echo, no quote), and non-identical exploring generates.

---

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCRIPTY_HOME` | `~/.scripty` | SQLite + artifacts |
| `XAI_API_KEY` | unset | Live writer → grok-4.6 |
| `SCRIPTY_WRITER_MODEL` | `grok-4.6` | xAI model id for fiction |
| `SCRIPTY_WRITER_REASONING_EFFORT` | `xhigh` | max reasoning (`low`/`medium`/`high`/`xhigh`) |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | xAI API root |
| `SCRIPTY_PROVIDER` | auto (`xai` if `XAI_API_KEY`, else `mock`) | Writer prose never uses Claude. `mock` keeps pytest offline. Film vision still uses Anthropic/mock |
| `SCRIPTY_TEXT_MODEL` | `claude-opus-4-8` | Claude model for the legacy film/distill path |
| `SCRIPTY_PORT` | `8787` | Dashboard port |

---

## Legacy film supervisor

`scripty create`, `scripty pass`, `scripty demo`, and `/film` still exist. See [`docs/USAGE.md`](docs/USAGE.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
