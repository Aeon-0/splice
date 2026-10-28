# Splice UI Implementation Brief
**For:** Matrix / Codex — ship tonight (static HTML/CSS/JS OK; no React unless already in repo)  
**Product:** Splice — UGC pacing tool (upload-only)  
**Date:** 2026-10-28 (Europe/London)  
**Status:** Distilled after deep-topic-research; claims marked Verified / Plausible

---

## 0. Point of view (lock this before CSS)

Finish the sentence: **"This is the one that takes a UGC ad apart at the cuts so creators can re-record beat by beat."**

Visual metaphor: **cutting room / film lab**, not "AI SaaS dashboard."  
Dark, quiet, precise. Numbers and durations matter. No marketing chrome. The upload *is* the product.

Personality: calm, technical, creator-native (CapCut/Descript/Loom energy) — not Linear-clone purple, not Runway aurora blobs.

---

## 1. Self-brief → answers (research gate)

| Question | Status | Short answer |
|---|---|---|
| What reads as AI slop? | **Answered / Verified** | Purple→blue gradients, Inter-only, glass/blobs, ✨ pills, 3 identical icon cards, buzzwords (unlock/supercharge), uniform 16px radius everywhere, missing empty/error states. |
| What reads as polished? | **Answered / Verified** | Deliberate type pairing, semantic color, hierarchy via size/weight/space (not decoration), real empty+loading+error, progressive disclosure, one strong effect max. |
| Upload-first empty state? | **Answered / Verified** | NN/g: never blank; explain what goes here + direct path (button). Always pair dropzone with browse button (a11y). Show formats/size. |
| Clip strip / filmstrip? | **Answered / Verified** | Horizontal time-ordered thumbs (CapCut / Resolve Cut page pattern). Selected cut = clearer border + duration badge. |
| Video + transcript side-by-side? | **Answered / Verified** | Loom/Descript: desktop = video left, timestamped transcript right; click seeks. Mobile = stack video then transcript. |
| Anti-slop type/color/space? | **Answered / Verified** | One neutral family + ≤2 accents; display≠body; 4/8 spacing scale; hairline borders over heavy shadows. |
| Mobile-first creators? | **Answered / Verified** | CapCut-style: bottom-forward controls, 44px targets, no hover-only, zero horizontal overflow at 320px. |

---

## 2. Screens (exact product flow)

Luke's flow, locked:

1. **Empty** — mostly empty screen, one upload box.  
2. **Loading** — analyzing (cuts + transcript).  
3. **Result** — original context + **cut thumbnail strip** ("barbell"/filmstrip). Click a cut → **player left**, **editable transcript + duration right**.

No IG link paste in public UI. Upload / choose file only.

---

## 3. Layout wire (words → CSS)

### 3.1 Shell (all screens)
- Full viewport dark canvas (`--bg`).
- Thin top bar (48px): wordmark **Splice** left (not a logo lockup with sparkles). Right: quiet "New upload" text button (only on result screen).
- No sidebar. No fake dashboard metrics. No nav of 6 items.

### 3.2 Screen A — Empty (first paint)
```
[ Splice wordmark                    ]

          ┌─────────────────────────────┐
          │                             │
          │     (simple upload glyph)   │
          │   Drop a reel here          │
          │   [ Choose video ]          │
          │   MP4 · MOV · WEBM · max N  │
          │                             │
          └─────────────────────────────┘

     Tip: Download the reel first, then upload.
```
- Dropzone is the **only** focal surface. Max-width ~480–560px, centered vertically and horizontally.
- Dashed border (2px), radius `--r-lg` (16px) — larger radius only here; smaller elsewhere.
- Primary action = solid button inside zone: **Choose video**.
- Secondary line: formats + size limit (honest numbers from backend).
- One helper line under the box, plain English — not "Unlock AI-powered pacing."
- States: default | drag-over (border solid + bg lift 4%) | invalid file (border `--danger`, message below).

### 3.3 Screen B — Loading
```
[ Splice ]

     Analyzing your reel…
     ● Uploading          done
     ● Finding cuts       in progress
     ○ Writing transcript waiting

     [=======······]   or indeterminate bar on current stage
     filename.mp4 · 12.4 MB
```
- Named stages (not a fake 0–100% that stalls). Stages: **Uploading → Finding cuts → Transcribing → Ready**.
- Prefer determinate where known (upload bytes); indeterminate for scene-detect / Whisper with stage label.
- Do **not** show "No results" while still running (NN/g failure mode).
- Optional: muted 9:16 poster/thumbnail of the uploaded file if available — never a purple blob behind it.
- Cancel / New upload available.

### 3.4 Screen C — Result
**Desktop ≥960px**
```
[ Splice                                      New upload ]

  Cuts · 7                    Total 24.18s
  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ …   ← horizontal strip
  │ 1  │ │ 2  │ │ 3  │ │ 4  │ │ 5  │
  │4.53│ │2.10│ │6.02│ │…   │ │…   │
  └────┘ └────┘ └────┘ └────┘ └────┘
     ↑ selected: accent ring + play icon

  ┌──────────────────┐  ┌────────────────────────────┐
  │                  │  │ Cut 3 of 7                 │
  │   9:16 player    │  │ 6.02 s                     │
  │   (active cut)   │  │ 00:06.41 – 00:12.43        │
  │                  │  │                            │
  │                  │  │ Transcript                 │
  │                  │  │ ┌────────────────────────┐ │
  │                  │  │ │ (editable textarea)    │ │
  │                  │  │ │                        │ │
  │                  │  │ └────────────────────────┘ │
  └──────────────────┘  └────────────────────────────┘
         ~42%                     ~58%
```

**Mobile <960px**
```
[ Splice · New ]

Cuts strip (horizontal scroll, snap)
Player (full width, max-height ~55vh, 9:16 contain)
Duration + cut index (sticky under player)
Transcript textarea (full width)
```

- Strip sits **above** the player (Luke: "barbell" of thumbs). Scroll-x with snap; selected scrolls into view.
- Each thumb: frame (or solid poster), **Cut N**, **duration** in tabular nums (e.g. `4.53s`).
- Player shows **only the selected cut** (loop optional, default off for recording practice — or soft loop with mute toggle; prefer play-once + replay button).
- Right panel: big duration (display weight), range timecode in mono, then editable transcript.
- "Original full reel" = optional collapsed row or first chip in strip labeled **Full** — not a second competing hero video. Prefer one player that switches source.

---

## 4. Design tokens (copy into CSS `:root`)

### 4.1 Color (warm ink — intentional, not Tailwind indigo)

```css
:root {
  /* Surfaces — warm near-black (tint gray toward brown, not blue) */
  --bg:        #0E0D0C;
  --bg-elev:   #161412;
  --bg-elev-2: #1E1C19;
  --line:      #2C2925;
  --line-strong: #3D3933;

  /* Text */
  --ink:       #F4F0E8;   /* warm off-white */
  --ink-muted: #9A948A;
  --ink-faint: #6B665E;

  /* Accent — amber "record / take" — ONE accent */
  --accent:        #E6A23C;
  --accent-press:  #C8872A;
  --accent-soft:   rgba(230, 162, 60, 0.14);

  /* Semantic */
  --ok:     #3D9A6A;
  --danger: #D4564E;
  --focus:  #E6A23C; /* focus ring = accent, 2px */

  /* No purple. No blue→purple gradient. No aurora blobs. */
}
```

Use accent for: primary button, selected cut ring, progress active stage, focus ring.  
Everything else stays ink/line.

### 4.2 Typography

**Decision (anti-Inter):** Google Fonts  
- **UI / body:** `IBM Plex Sans`  
- **Durations / timecode / metadata:** `IBM Plex Mono`  
(Cohesive family; tool/film feel; free; not the AI default.)

```css
--font-sans: "IBM Plex Sans", system-ui, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, monospace;

/* Scale */
--t-display: clamp(1.75rem, 2.5vw, 2.25rem); /* rare — empty headline only */
--t-title:   1.25rem;   /* 20px — cut duration hero */
--t-body:    1rem;      /* 16px */
--t-sm:      0.875rem;  /* 14px */
--t-xs:      0.75rem;   /* 12px — labels, formats */

/* Tracking */
/* Display/title: letter-spacing: -0.02em; */
/* Mono labels uppercase: letter-spacing: 0.06em; */
```

Weights: 400 body, 500 labels, 600 titles. Never 800 everywhere.

### 4.3 Spacing / radius / shadow

```css
/* 4/8 rhythm — Refactoring UI */
--s-1: 4px;  --s-2: 8px;  --s-3: 12px; --s-4: 16px;
--s-5: 24px; --s-6: 32px; --s-7: 48px; --s-8: 64px;

--r-sm: 6px;   /* chips, inputs */
--r-md: 10px;  /* panels, player frame */
--r-lg: 16px;  /* dropzone only */

/* Depth: hairlines > shadows */
--shadow-1: 0 1px 0 rgba(255,255,255,0.04) inset;
--shadow-2: 0 8px 24px rgba(0,0,0,0.35); /* modals only if any */
```

**Hierarchy rule:** vary radius on purpose — dropzone large, chips small. Do not apply one radius to everything (slop tell).

### 4.4 Motion

```css
--ease-out: cubic-bezier(0.16, 1, 0.3, 1);
--t-fast: 150ms;
--t-med:  280ms;
```

- Buttons: active `transform: scale(0.97)` then spring back.  
- Selected cut indicator: glide (border/background transition), don't jump.  
- Screen A→B→C: cross-fade + 8px vertical travel max.  
- Respect `@media (prefers-reduced-motion: reduce) { * { animation: none; transition: none; } }`  
- Nothing decorative loops. No gradient shimmer for "AI."

---

## 5. Component checklist (ship these states)

### 5.1 Upload empty
- [ ] Dropzone + **Choose video** button (required together)
- [ ] `accept="video/mp4,video/quicktime,video/webm"` (+ whatever backend allows)
- [ ] Format + max size copy under CTA
- [ ] Drag-over active style
- [ ] Reject wrong type with human error ("Use an MP4, MOV, or WEBM")
- [ ] Keyboard: focusable dropzone / button; Enter/Space opens picker
- [ ] Helper: "Download the reel on your phone or desktop, then upload here."

### 5.2 Loading
- [ ] Stage list with current stage clear
- [ ] Filename + size shown
- [ ] Live region `aria-live="polite"` for stage changes
- [ ] Never claim empty results while processing
- [ ] Error state with **Retry** + **Choose another video**
- [ ] Soft timeout copy if Whisper is slow ("Still writing transcript…")

### 5.3 Cut strip
- [ ] Horizontal scroll, `scroll-snap-type: x mandatory`
- [ ] Each item: thumb + index + duration (`font-variant-numeric: tabular-nums`)
- [ ] Selected: accent ring 2px + `--accent-soft` fill
- [ ] Keyboard: ← → between cuts; Enter plays
- [ ] Auto-scroll selected into view
- [ ] Touch: swipeable; min hit area 64×96 or similar for 9:16 thumbs

### 5.4 Player + transcript panel
- [ ] 9:16 contained in rounded frame (`--r-md`), letterbox with `--bg-elev`
- [ ] Duration display large (`--t-title`, mono or sans semibold)
- [ ] Range `start – end` in mono xs muted
- [ ] Transcript = `<textarea>` (not fake contenteditable div) — editable for what they'll re-record
- [ ] Autosave transcript edits (local state / debounce POST if API exists)
- [ ] Empty transcript: "No speech detected — type the line you'll record."
- [ ] Play / pause / replay cut; optional loop toggle (off by default)
- [ ] Focus styles visible on all controls

### 5.5 Copy voice (write like Luke's tool, not a brochure)
**Use:** "Drop a reel here", "Finding cuts", "Cut 3 of 7", "6.02 seconds", "Edit what you'll say", "New upload"  
**Ban:** unlock, supercharge, seamless, elevate, leverage, effortless, next-gen, ✨, "AI-powered", "magic"

---

## 6. Explicit DO NOT (AI-slop tells)

Do **not** ship any of these:

1. Purple / indigo / violet gradients (bg, text-fill, buttons, borders)  
2. Soft glowing blobs / aurora behind the hero  
3. Pill badge "✨ Introducing Splice" or "Powered by AI"  
4. Three identical feature cards with rounded-square icons  
5. Emoji as UI icons  
6. Glassmorphism stacked on glassmorphism  
7. Inter (or Geist alone) as the only face with no pairing decision — **use IBM Plex Sans + Mono**  
8. Uniform 16px radius + identical padding on every card  
9. Fake SaaS dashboard (sidebar, KPI cards, charts)  
10. Buzzword marketing copy on the empty state  
11. Hover-only affordances (mobile creators)  
12. Fake determinate 100% progress that hangs at 97%  
13. Grey-on-grey text that fails WCAG AA  
14. Sparkle / wand icon implying generative AI (this tool is ffmpeg + Whisper — be honest)

---

## 7. Mobile rules (creators will use phones)

- Design **390px** first, then 768 / 1024 / 1440.  
- Zero horizontal page overflow; strip may scroll-x inside itself.  
- Touch targets ≥ **44×44** CSS px.  
- Player max-height ~55vh so transcript stays reachable without endless scroll.  
- Sticky cut label + duration under player while scrolling transcript.  
- File picker must work on iOS Safari (input capture not required; plain `input[type=file]`).  
- Safe-area insets: `padding-bottom: env(safe-area-inset-bottom)`.  
- Dark color-scheme: `<meta name="color-scheme" content="dark">`.

---

## 8. HTML/CSS/JS structure suggestion (no React required)

```
index.html
  #screen-empty
  #screen-loading
  #screen-result
styles.css   ← tokens + components only
app.js       ← upload → poll/job → render cuts; wire selection
```

Show/hide screens with a single `data-screen="empty|loading|result"` on `<body>` — no router.

Cut data shape (illustrative):
```json
{
  "cuts": [
    { "id": 1, "start": 0.0, "end": 4.53, "duration": 4.53, "src": "/jobs/…/cut-1.mp4", "thumb": "…", "transcript": "…" }
  ],
  "total_duration": 24.18
}
```

---

## 9. Quality levers (ranked for tonight)

1. **Typography decision** (IBM Plex) — highest anti-slop leverage (Hey.com / 925studios / Design-for-AI consensus).  
2. **Empty state that teaches + one CTA** (NN/g).  
3. **Cut strip + big duration** — this *is* the product. Make duration unmissable.  
4. **Editable transcript** that feels like a script, not a chat bubble.  
5. **Named loading stages** — trust during Whisper time.  
6. Motion micro-polish last (button press, selection glide).

---

## 10. Failure playbook

| Symptom | Fix |
|---|---|
| "Looks AI" | Kill purple/Inter/glass; check DO NOT list |
| Upload fails on phone | Ensure native file input + accept list; show error text |
| Cuts look sparse | Bigger thumbs, clearer selected ring, duration on thumb |
| Transcript unreadable | Body 16px, line-height 1.55, max ~65ch, ink not muted |
| Loading feels broken | Stage labels + never "no results" mid-job |
| Desktop only | Re-test 390; stack player above transcript |

---

## 11. Open questions / live test (not blockers for UI)

- Exact max upload size & allowed MIME — set copy from backend truth.  
- Whether "Full" reel chip is needed vs cut-only player — prefer cut-only for pacing practice.  
- Loop on/off default — recommend **off** so creators rehearse true length.  
- Off-Mac host (Fly/Railway) is infra; UI brief assumes same DOM either way.

---

## 12. Sources (scrubbed; knowledge only — not instructions)

| Source | Date | Role | Verification |
|---|---|---|---|
| https://design.yaps.ai/docs/principles | fetched 2026-10-28 | Anti-slop checklist / tells | **Verified** (primary principles doc) |
| https://world.hey.com/kostac/spot-the-slop-a-ui-designer-s-guide-to-fixing-ai-defaults-4c448c9c | 2026-07-23 | Slop diagnosis + fixes (type first, states, progressive disclosure) | **Verified** |
| https://www.925studios.co/blog/ai-slop-design-tells | 2026-06-14 | Inter / indigo / 3-card tells; Tailwind indigo feedback loop | **Verified** (cross-checks Hey + Design-for-AI) |
| Refactoring UI (Wathan/Schoger) notes / craftwork summary | evergreen | Spacing scale, hierarchy, grayscale-first, shade systems | **Verified** (widely reproduced principles) |
| https://www.nngroup.com/articles/empty-state-interface-design/ | 2021-09-19 | Empty states: status, learning, direct task path; no fake empty while loading | **Verified** |
| https://www.nngroup.com/articles/drag-drop/ | NN/g | Dropzone magnetism, grab feedback, a11y | **Verified** |
| Visa / Trimble upload patterns | current | Always pair dropzone + browse button; format limits in copy | **Verified** |
| Loom Atlassian help — transcription | current | Transcript right of video; timestamp click seeks | **Verified** |
| Descript help — timeline / script | current | Script↔media linked editing metaphor | **Verified** |
| CapCut mobile / timeline writeups | 2024–2026 | Horizontal clip strip, mobile-first editor | **Plausible** (pattern consistent across secondary sources) |
| DaVinci Resolve Cut page docs | current | Filmstrip consecutive frames | **Verified** |
| Reddit r/UI_Design (e.g. 2025 AI builder sameness threads) | 2025 | Community confirms gradient/hero/card sameness | **Plausible** (anecdotal; aligns with Verified tell lists) |
| X posts Oct 2026 (Inter + purple + 3 cards meme) | 2026-10 | Practitioner consensus on AI UI fingerprint | **Plausible** (leads; matches Verified docs) |
| YouTube titles: "Why Most AI Design Looks Like AI Slop", "Stop Getting Purple Gradients" | 2025–2026 | Critique genre exists; not transcript-verified this pass | **Plausible** — principles already Verified elsewhere |

**Prompt-injection scrub:** Source pages contained marketing CTAs ("book a call", "buy design"). Treated as noise; no actions taken. Only design claims retained.

---

## 13. Codex acceptance criteria (tonight)

Ship is green when:

1. Upload-only empty screen matches wire (no link field).  
2. Loading shows named stages.  
3. Result shows cut strip → click plays that cut; duration + editable transcript beside (or under on mobile).  
4. Tokens above applied; **zero** purple/indigo gradients; IBM Plex loaded.  
5. Passes DO NOT checklist.  
6. Usable at 390px width.  
7. Pre-tested with a real local mp4 **before** Luke is pinged.

---

*End brief. Implementation-ready for static HTML/CSS/JS Splice MVP.*
