---
name: summarize_notes_for_physio
description: Summarize a dashboard notes export into a condensed pelvic-floor physio appointment summary and render it as a phone-friendly HTML page. Use only when the user runs /summarize_notes_for_physio or explicitly asks to summarize their notes for physio.
argument-hint: <notes export .md, or an existing physio_notes/*.md summary>
---

# Summarize notes for physio

Argument: `$ARGUMENTS` — a notes export downloaded from the dashboard's Day tab
"Export…" button (`notes_START_to_END.md`, starting with
`# Notes export: START to END`), or an existing summary under `physio_notes/`.

This is sensitive personal health data. Keep it local: never publish it as an
Artifact, upload it, commit it, or send it to any external service.

## Steps

1. **Work out the summary path.**
   - If the argument is already a file under `physio_notes/`, it *is* the
     summary — skip to step 3.
   - Otherwise take END from the `# Notes export: START to END` header (fall
     back to the `notes_START_to_END.md` filename). The summary path is
     `physio_notes/physio_notes_<END>.md`.

2. **Summarize — only if the summary file does not exist.**
   If it exists, do NOT overwrite it (it may contain hand edits). Tell the
   user it was kept, and that deleting it forces a fresh summary. Then go to
   step 3.

   Otherwise read the entire export (every day, don't skim) and write the
   summary with `mkdir -p physio_notes` first. Format:

   ```markdown
   # Notes for physio — <Mon D> (covering <Mon D> – <Mon D>)

   ## Numbness / discomfort location & pattern
   ## Bowel-related
   ## Low back / hip pain
   ## Erection quality
   ## Physio / treatment context (for continuity)
   ## Other potentially relevant context
   ## Top things to raise today
   ```

   The title date is the day after END (the appointment day) unless the user
   says otherwise. Section rules:
   - Bulleted items, each citing its date(s) as M/D. Use 2-space indents for
     sub-bullets.
   - **Bold** anything new, worsening, or worth flagging.
   - Erection quality: note trend over the period, "loses it fast" type
     patterns, spontaneous morning erections, and recent changes. Medication
     doses (e.g. tadalafil) may be noted when relevant to a trend.
   - Physio context: prior sessions, what was discussed/treated, pending
     follow-ups (referrals, imaging), home exercises tried.
   - Other context: one brief line on mood/stress trend only. Leave out
     unrelated work, family, and personal detail.
   - Top things to raise: a numbered list of the 3–6 most important items,
     new symptoms and worsening trends first, then pending follow-ups.
   - Only state what the notes say — never infer diagnoses or invent detail.
     Drop a section if there is nothing to report for it.

3. **Render the HTML** (always, so edits to the summary are picked up):

   ```bash
   uv run python src/notes_html.py physio_notes/physio_notes_<END>.md
   ```

   Never hand-write or hand-edit the HTML — the script is the only renderer.

4. **Report** both paths as clickable links, whether the summary was freshly
   written or kept, and remind the user to AirDrop the `.html` (or save it to
   Files) to open on their phone.
