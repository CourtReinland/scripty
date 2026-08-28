# Scripty

![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Offline](https://img.shields.io/badge/runs-offline%20by%20default-8a8a8a)

A **human-in-the-loop fiction trainer**. You pick a genre desk, a tone, a length, and a story summary. Scripty writes a first draft (a complete short piece, or chapter 1). Then it writes a *challenger* with a fresh seed, temperature, and prompt mutation. You look at them side by side and say **Better** or **Worse**. You are the only judge. Cheap signals (word count, lexical novelty, a couple of contrast notes) are shown so the compare is easier to talk about — they never pick the winner.

Each genre is its own desk (short horror, literary, and so on) with a style card written in our words: tropes, beat shapes, tone notes. Verdicts become lessons that desk recalls on the next generate. There is no downloaded novel corpus. If you want a “known good” reference, paste a public-domain passage or a short excerpt you have rights to.

A King-ish small-town unease in the horror hint is a **tone hint**, not a reading list.

The older film script-supervisor (ffmpeg cuts, Fountain, Needleman–Wunsch) is still in the tree and at `/film`. It is not the product this README is for.

---

## The loop

1. Start a session: genre, tone, length, summary.
2. Get a champion draft.
3. Generate a challenger (random every time).
4. Choose Better (challenger becomes champion) or Worse (challenger is discarded).
5. Repeat until you are happy. Champion history stays visible. The desk remembers.

```
summary + desk card + desk lessons
        │
        ▼
   first draft  ──▶  CHAMPION
        │
        │  seed / temperature / mutation
        ▼
   CHALLENGER  ──▶  you: Better | Worse
        │
        ├─ Better → challenger is champion; lesson saved
        └─ Worse  → champion stays; lesson saved
```

---

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

scripty write start -g horror -t "quiet dread" -l short \
  -s "A lighthouse keeper finds a second set of footprints on the stairs every morning, always one size smaller than his own."

scripty write generate 1
scripty write judge 1 better --note "colder opening"
scripty write show 1

scripty serve    # http://127.0.0.1:8787/  — side-by-side UI
```

No API key required: the mock provider writes seed-varied offline prose so the loop is testable. If `ANTHROPIC_API_KEY` (or `SCRIPTY_PROVIDER=anthropic`) is set, the same loop uses Claude.

---

## CLI

| Command | What it does |
| --- | --- |
| `scripty write start -g GENRE -t TONE -l short\|medium\|long -s SUMMARY` | Open a desk session and write the first draft |
| `scripty write generate ID` | First draft, or a randomized challenger |
| `scripty write judge ID better\|worse` | Human verdict; only Better promotes |
| `scripty write show ID` / `history ID` | Current pair and champion lineage |
| `scripty write lessons -g GENRE` | Lessons saved on that desk |
| `scripty write ref -g GENRE --kind user_excerpt\|public_domain --title … --file …` | Attach text you have rights to |
| `scripty write next ID` | Next chapter (medium/long) |
| `scripty serve` | Writer UI at `/` (legacy film UI at `/film`) |

Desks: `horror`, `literary`, `science_fiction`, `fantasy`, `mystery`, `romance`.

---

## What this is not

- Not a “score me against Stephen King” engine.
- Not a place we scrape or vendor living authors’ books.
- Not a claim that the mock drafts are good fiction — they exist so the loop runs offline.

---

## Tests

```bash
python -m pytest -q
```

Writer tests cover first draft, Better/Worse, per-desk lessons, and “two generates are not identical,” all without a copyrighted corpus. Film-supervisor tests still run; that pipeline is legacy.

---

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCRIPTY_HOME` | `~/.scripty` | SQLite + artifacts |
| `SCRIPTY_PROVIDER` | auto (`anthropic` if credentials exist, else `mock`) | Writer / distill brain |
| `SCRIPTY_TEXT_MODEL` | `claude-opus-4-8` | Claude model when using Anthropic |
| `SCRIPTY_PORT` | `8787` | Dashboard port |

---

## Legacy film supervisor

`scripty create`, `scripty pass`, `scripty demo`, ground-truth align, and the Compare Bay still exist. See [`docs/USAGE.md`](docs/USAGE.md) (legacy section) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Prefer the writer loop above unless you are working on that stack on purpose.
