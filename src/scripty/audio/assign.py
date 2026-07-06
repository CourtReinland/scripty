"""Speaker attribution + segment-to-shot mapping.

Naive but honest: with no diarization model available we alternate
SPEAKER 1 / SPEAKER 2 on conversational gaps, and the dashboard's
correction loop teaches Scripty the real character names.
"""
from __future__ import annotations

from dataclasses import replace

from ..core.models import TranscriptSegment

#: A silence this long between segments suggests the other party is speaking.
GAP_FLIP_SECONDS = 1.2

_SPEAKERS = ("SPEAKER 1", "SPEAKER 2")

#: Attribution for segments whose speaker is unknown at dialogue time.
DEFAULT_CHARACTER = "VOICE (O.S.)"


def assign_speakers(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Alternation heuristic: a gap > 1.2s flips SPEAKER 1/2 unless already set.

    Segments that arrive with a non-empty ``speaker`` keep it. Input
    segments are not mutated; new instances are returned in start order.
    """
    ordered = sorted(segments, key=lambda s: (s.start_s, s.end_s))
    out: list[TranscriptSegment] = []
    current = 0
    prev_end: float | None = None
    for seg in ordered:
        if prev_end is not None and (seg.start_s - prev_end) > GAP_FLIP_SECONDS:
            current = 1 - current
        if seg.speaker:
            out.append(replace(seg))
            if seg.speaker in _SPEAKERS:
                current = _SPEAKERS.index(seg.speaker)
        else:
            out.append(replace(seg, speaker=_SPEAKERS[current]))
        prev_end = seg.end_s
    return out


def dialogue_from_segments(segments: list[TranscriptSegment],
                           shots: list[dict]) -> list[dict]:
    """Map segments onto shots by midpoint; return rows for db.add_dialogue.

    ``shots`` are db dicts (id, idx, start_s, end_s). Each returned dict has
    exactly the keys shot_id, character, text, parenthetical, start_s, end_s
    — the pipeline adds pass_id/scene_id. Segments whose midpoint lands in
    no shot get shot_id None; blank-text segments are dropped.
    """
    rows: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        midpoint = (seg.start_s + seg.end_s) / 2.0
        shot_id = None
        for shot in shots:
            if float(shot["start_s"]) <= midpoint < float(shot["end_s"]):
                shot_id = shot["id"]
                break
        if shot_id is None and shots:
            # Transcriber timestamps commonly overrun the media duration a
            # touch; a segment that STARTS inside the timeline but whose
            # midpoint lands at/after the final shot's end belongs to the
            # final shot rather than to no shot at all.
            last = max(shots, key=lambda s: float(s["end_s"]))
            if seg.start_s < float(last["end_s"]) <= midpoint:
                shot_id = last["id"]
        rows.append({
            "shot_id": shot_id,
            "character": seg.speaker or DEFAULT_CHARACTER,
            "text": text,
            "parenthetical": "",
            "start_s": seg.start_s,
            "end_s": seg.end_s,
        })
    return rows
