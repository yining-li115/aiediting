"""Command line entry point.

Block A ships the commands that do not depend on any model: database setup,
timeline validation, rendering, and a self-contained demo that proves the
acceptance criterion end to end. The pipeline commands are declared but refuse
to run, so the surface is visible without pretending the work is done.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .config import settings
from .db.session import init_db
from .renderer import FFmpegRenderer, probe_duration
from .specs import EditPlanSpec, TimelineSpec, validate_timeline

app = typer.Typer(add_completion=False, help="AI video editor (Lite MVP)")
db_app = typer.Typer(help="Database maintenance")
app.add_typer(db_app, name="db")


def _load_timeline(path: Path) -> TimelineSpec:
    return TimelineSpec.model_validate_json(path.read_text(encoding="utf-8"))


def _load_sources(spec: TimelineSpec, media_root: Path) -> dict[str, Path]:
    """Resolve source_id -> file. Ids map to files under the media root."""
    sources: dict[str, Path] = {}
    for clip in spec.clips:
        if clip.source_id in sources:
            continue
        candidates = list(media_root.glob(f"{clip.source_id}.*"))
        if not candidates:
            raise typer.BadParameter(
                f"no media file for source_id {clip.source_id!r} under {media_root}"
            )
        sources[clip.source_id] = candidates[0]
    return sources


@db_app.command("init")
def db_init() -> None:
    """Create every table."""
    init_db()
    typer.echo(f"database ready at {settings.db_url}")


@app.command()
def validate(
    timeline: Path = typer.Argument(..., help="TimelineSpec JSON"),
    plan: Path = typer.Option(None, help="EditPlanSpec JSON, enables intent checks"),
    media_root: Path = typer.Option(None, help="checks source ranges against files"),
) -> None:
    """Check a timeline against the invariants."""
    spec = _load_timeline(timeline)
    plan_spec = (
        EditPlanSpec.model_validate_json(plan.read_text(encoding="utf-8"))
        if plan
        else None
    )
    durations = None
    if media_root:
        durations = {
            asset_id: probe_duration(path)
            for asset_id, path in _load_sources(spec, media_root).items()
        }

    violations = validate_timeline(spec, plan_spec, durations)
    hard = [v for v in violations if v.severity == "hard"]
    soft = [v for v in violations if v.severity == "soft"]

    for v in violations:
        marker = "HARD" if v.severity == "hard" else "soft"
        where = f" ({v.location})" if v.location else ""
        typer.echo(f"[{marker}] {v.code}{where}: {v.message}")

    if not violations:
        typer.echo(f"OK: {len(spec.clips)} clips, {spec.duration:.2f}s, no violations")
    else:
        typer.echo(f"\n{len(hard)} hard, {len(soft)} soft")
    raise typer.Exit(1 if hard else 0)


@app.command()
def render(
    timeline: Path = typer.Argument(..., help="TimelineSpec JSON"),
    out: Path = typer.Option(Path("out/render.mp4"), help="output file"),
    quality: str = typer.Option("hq", help="proxy | hq"),
    media_root: Path = typer.Option(None, help="defaults to AIVE_MEDIA_ROOT"),
    start: float = typer.Option(None, help="partial render start, seconds"),
    end: float = typer.Option(None, help="partial render end, seconds"),
) -> None:
    """Render a timeline to MP4."""
    spec = _load_timeline(timeline)
    root = media_root or settings.media_root
    sources = _load_sources(spec, root)
    time_range = (start, end) if start is not None and end is not None else None

    result = FFmpegRenderer().render(spec, quality, out, sources, time_range)
    typer.echo(
        f"{result.path}  {result.duration_sec:.2f}s  "
        f"{result.file_size / 1024:.0f} KB  {result.render_time_ms} ms"
    )


@app.command()
def segments(
    project: str = typer.Option(..., help="project id"),
    all_: bool = typer.Option(False, "--all", help="include rejected segments"),
) -> None:
    """List the candidate pool, so 'why was this clip unused?' is answerable."""
    from .material import load_segments

    rows = load_segments(project)
    if not all_:
        rows = [r for r in rows if r.is_usable]
    typer.echo(f"{len(rows)} segments in {project}")
    for seg in sorted(rows, key=lambda s: (s.source_id, s.start)):
        mark = " " if seg.is_usable else "x"
        quality = f"{seg.technical_quality:.2f}" if seg.technical_quality else " -  "
        kind = "photo" if seg.kind == "photo" else "     "
        typer.echo(
            f" {mark}{kind} {seg.segment_id:<22} {seg.start:6.2f}-{seg.end:6.2f}"
            f" q={quality} {seg.shot_type:<16} {seg.semantic_role:<20}"
            f" {seg.reject_reason or (seg.semantic or '')[:48]}"
        )


@app.command()
def inspect(
    path: Path = typer.Argument(..., help="a spec JSON file"),
) -> None:
    """Print a human-readable summary of a spec."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "slots" in raw:
        plan = EditPlanSpec.model_validate(raw)
        typer.echo(f"EditPlanSpec {plan.plan_id}  mode={plan.mode}")
        typer.echo(
            f"  target {plan.intent.target_duration:.1f}s, "
            f"planned {plan.planned_duration():.1f}s, {len(plan.slots)} slots"
        )
        for slot in plan.slots:
            typer.echo(
                f"  #{slot.order:02d} {slot.slot_id}  {slot.semantic_role:<20}"
                f" {slot.shot_type:<18} {slot.target_duration:5.2f}s"
                f"  energy={slot.energy:.2f}"
            )
        return

    spec = TimelineSpec.model_validate(raw)
    typer.echo(f"TimelineSpec {spec.timeline_id}  plan={spec.plan_id}")
    typer.echo(f"  {spec.duration:.2f}s, {len(spec.clips)} clips")
    for clip in spec.clips:
        typer.echo(
            f"  {clip.timeline_start:6.2f}-{clip.timeline_end:6.2f}  "
            f"{clip.source_id}[{clip.source_in:.2f}-{clip.source_out:.2f}]"
            f"  x{clip.speed:.2f}  slot={clip.slot_id}  {clip.transition_out}"
        )
    for record in spec.adaptation_log:
        typer.echo(f"  adapt {record.slot_id}: {record.action} - {record.detail}")


@app.command()
def models() -> None:
    """Show the active model stack and what still needs verifying."""
    from .model_registry import candidates_for, open_unknowns, stack_summary
    from .roles import Role

    typer.echo("active stack (edit ACTIVE_STACK in aive/model_registry.py):")
    for row in stack_summary():
        if row.get("key") is None:
            typer.echo(f"  {row['role']:<12} -")
            continue
        typer.echo(
            f"  {row['role']:<12} {row['key']:<26} {row['status']:<12}"
            f" {row['vendor']}/{row['model']}"
        )

    typer.echo("\ncandidates per role:")
    for role in Role:
        names = [c.key for c in candidates_for(role)]
        if names:
            typer.echo(f"  {role.value:<12} {', '.join(names)}")

    unknowns = open_unknowns()
    if unknowns:
        typer.echo("\nmust be confirmed against a vendor console before freezing:")
        for key, items in unknowns.items():
            for item in items:
                typer.echo(f"  [{key}] {item}")


@app.command("list-models")
def list_models(
    vendor: str = typer.Option(
        "google", help="which credentials to use: matches a card's *_env vars"
    ),
    filter: str = typer.Option(None, help="only show ids containing this"),
) -> None:
    """Ask the configured endpoint which models it actually offers.

    Catalog entries are written from documentation, which goes stale. The
    account is the only authority on what exists and what it is called, so
    confirming a model id here is a prerequisite for pinning it.
    """
    import asyncio
    import os

    import httpx

    from .model_registry import CATALOG

    card = next(
        (c for c in CATALOG.values() if c.vendor.startswith(vendor)), None
    )
    if card is None:
        raise typer.BadParameter(f"no catalog entry for vendor {vendor!r}")

    base_url = os.environ.get(card.base_url_env or "", "").rstrip("/")
    api_key = os.environ.get(card.api_key_env or "", "")
    if not base_url or not api_key:
        typer.echo(
            f"set {card.base_url_env} and {card.api_key_env} first "
            f"(they are read from the environment, never from a file we print)"
        )
        raise typer.Exit(1)

    async def fetch() -> list[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            body = response.json()
        rows = body.get("data") or body.get("models") or []
        return sorted(
            r.get("id") or r.get("name", "") for r in rows if isinstance(r, dict)
        )

    try:
        ids = asyncio.run(fetch())
    except Exception as exc:
        typer.echo(f"failed: {exc}")
        raise typer.Exit(1)

    shown = [i for i in ids if not filter or filter.lower() in i.lower()]
    typer.echo(f"{len(shown)} of {len(ids)} models at {base_url}")
    for model_id in shown:
        typer.echo(f"  {model_id}")

    # Endpoints differ on whether ids carry a namespace prefix ("models/x"),
    # while the completions API accepts either. Compare on the bare name so a
    # naming convention does not read as a missing model.
    def bare(model_id: str) -> str:
        return model_id.rsplit("/", 1)[-1]

    available = {bare(i) for i in ids}
    pinned = {c.model_id for c in CATALOG.values() if c.vendor.startswith(vendor)}
    missing = sorted(p for p in pinned if bare(p) not in available)

    rolling = sorted(i for i in shown if i.endswith("-latest"))
    if rolling:
        typer.echo(
            f"\nrolling aliases present ({', '.join(bare(r) for r in rolling)})"
            "\n  -> never pin these: a silent upstream change would alter output"
            "\n     quality with nothing in the logs to explain it (FD-M10)"
        )
    if missing:
        typer.echo(
            f"\ncatalog pins ids this account does not list: {missing}"
            "\n  -> update model_registry.py before relying on them"
        )


@app.command()
def logs(
    tail: int = typer.Option(20, help="how many recent calls to show"),
    errors: bool = typer.Option(False, "--errors", help="only failed calls"),
    show: str = typer.Option(None, help="print the full record for this subject"),
) -> None:
    """Read the AI call log.

    Model behaviour is the least predictable part of the system. When a label
    looks wrong, this is how to tell a bad prompt from a bad model from a bad
    frame: the exact request and the exact response are both here.
    """
    import glob
    import json as _json

    from .calllog import log_dir

    files = sorted(glob.glob(str(log_dir() / "ai-calls-*.jsonl")))
    if not files:
        typer.echo(f"no logs under {log_dir()}/")
        raise typer.Exit(1)

    entries = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            entries.extend(_json.loads(line) for line in handle if line.strip())

    if show:
        for entry in entries:
            if entry.get("subject") == show:
                typer.echo(_json.dumps(entry, ensure_ascii=False, indent=2))
        return

    if errors:
        entries = [e for e in entries if e.get("status") != "ok"]

    ok = sum(1 for e in entries if e.get("status") == "ok")
    typer.echo(f"{len(entries)} calls  ({ok} ok, {len(entries) - ok} failed)")
    for entry in entries[-tail:]:
        usage = entry.get("usage") or {}
        tokens = f"{usage.get('tokens_in') or '-'}/{usage.get('tokens_out') or '-'}"
        typer.echo(
            f"  {entry['ts'][11:19]} {entry['role']:<9} {entry['status']:<6}"
            f" {entry.get('mode', '-'):<12} {entry.get('latency_ms', 0):>6}ms"
            f" tok={tokens:<10} {entry.get('subject') or ''}"
        )
        if entry.get("error"):
            typer.echo(f"      {entry['error'][:150]}")

    if entries:
        typer.echo(f"\nfull records: aive logs --show <subject>")


@app.command("add-reference")
def add_reference_cmd(
    file: Path = typer.Option(..., help="reference video file"),
    project: str = typer.Option(..., help="project id"),
    analyze_now: bool = typer.Option(True, "--analyze/--no-analyze"),
) -> None:
    """Add a reference video and analyse its editing.

    A file only. There is no URL option on purpose: a "paste a link" box is a
    scraping channel built into the product, which contradicts the data
    position in section 16.1.
    """
    import asyncio

    from .providers.cost import install_db_cost_sink
    from .reference import add_reference, analyze_reference

    init_db()
    install_db_cost_sink()

    reference_id, key = add_reference(file, project)
    typer.echo(f"reference {reference_id}")
    typer.echo(f"  stored: {key}")
    if not analyze_now:
        return

    def report(shot) -> None:
        typer.echo(
            f"  shot {shot.index:>3}  {shot.start:6.2f}-{shot.end:6.2f}"
            f"  {shot.shot_type:<18} {shot.semantic_role:<20}"
            f" {shot.transition_out}"
        )

    analysis = asyncio.run(analyze_reference(reference_id, on_shot=report))
    _print_reference(analysis)


@app.command("reference")
def reference_show(
    project: str = typer.Option(..., help="project id"),
    show: str = typer.Option(None, help="reference id, to print its analysis"),
) -> None:
    """List a project's references, or print one analysis."""
    from .reference import list_references, load_analysis

    init_db()
    if show:
        analysis = load_analysis(show)
        if analysis is None:
            typer.echo(f"no analysis for {show}")
            raise typer.Exit(1)
        _print_reference(analysis)
        return

    rows = list_references(project)
    if not rows:
        typer.echo(f"no references in {project}")
        raise typer.Exit(1)
    for reference_id, key, analysed in rows:
        mark = "analysed" if analysed else "not analysed"
        typer.echo(f"  {reference_id:<20} {mark:<14} {key}")


def _print_reference(analysis) -> None:
    stats = analysis.shot_duration
    typer.echo("")
    typer.echo(f"  duration:    {analysis.duration:.2f}s, {analysis.shot_count} shots")
    typer.echo(
        f"  shot length: median {stats.median:.2f}s"
        f"  (p10 {stats.p10:.2f} - p90 {stats.p90:.2f}, sd {stats.std:.2f})"
    )
    typer.echo(f"  pace:        {analysis.pace}")
    typer.echo(f"  transitions: density {analysis.transition_density:.2f}")
    music = analysis.music
    if music.bpm:
        typer.echo(
            f"  music:       {music.bpm:.1f} bpm, {music.beat_count} beats,"
            f" cuts on beat {music.beat_sync_strength:.2f}"
            f" (offset median {music.cut_to_beat_offset_ms.median:.0f}ms)"
        )
    else:
        typer.echo("  music:       none detected")
    typer.echo(f"  scale seq:   {' '.join(analysis.shot_scale_sequence[:14])}")
    typer.echo(f"  roles:       {' '.join(analysis.semantic_structure[:10])}")
    typer.echo(f"  style tags:  {analysis.style_tags}")
    typer.echo(f"  captions:    {analysis.text.subtitle_density}")

    extra = analysis.provenance.extra or {}
    if extra.get("unlabelled_shots"):
        typer.echo(
            f"  UNLABELLED:  {extra['unlabelled_shots']}/{analysis.shot_count}"
            f" shots have no semantics"
        )
    for failure in (extra.get("failures") or [])[:5]:
        typer.echo(f"    {failure}")


@app.command("add-music")
def add_music(
    file: Path = typer.Option(..., help="audio file to upload"),
    license: str = typer.Option(
        ..., help="required: this audio ships inside a user's published video"
    ),
    project: str = typer.Option(
        None, help="attach to one project; omit to add to the shared library"
    ),
    title: str = typer.Option(None, help="display name"),
    mood: str = typer.Option("", help="comma-separated mood tags"),
) -> None:
    """Upload one track, copy it into storage and precompute its beat grid."""
    from .music import register

    init_db()
    if not file.exists():
        raise typer.BadParameter(f"no such file: {file}")

    track = register(
        file,
        license=license,
        title=title,
        mood_tags=[m.strip() for m in mood.split(",") if m.strip()],
        project_id=project,
    )
    scope = f"project {project}" if project else "shared library"
    bpm = f"{track.bpm:.1f}" if track.bpm else "unknown"
    typer.echo(f"added {track.music_id} to the {scope}")
    typer.echo(f"  stored:  {track.storage_key}")
    typer.echo(f"  licence: {track.license}")
    typer.echo(f"  tempo:   {bpm} bpm, {len(track.beat_times)} beats detected")
    if not track.beat_times:
        typer.echo(
            "  note:    no beats detected - beat alignment will be skipped for "
            "this track"
        )


@app.command("music")
def music_list(
    project: str = typer.Option(None, help="also show tracks for this project"),
) -> None:
    """List available tracks."""
    from .music import load_all

    init_db()
    tracks = load_all(project)
    if not tracks:
        typer.echo("no tracks. add one with: aive add-music --file X --license Y")
        raise typer.Exit(1)
    typer.echo(f"{len(tracks)} tracks")
    for track in tracks:
        scope = track.project_id or "shared"
        bpm = f"{track.bpm:.0f}" if track.bpm else "  ?"
        duration = f"{track.duration:.0f}s" if track.duration else "?"
        typer.echo(
            f"  {track.music_id:<18} {bpm:>4}bpm {duration:>6}"
            f"  beats={len(track.beat_times):<5} {scope:<10} {track.mood_tags}"
        )
        typer.echo(f"      {track.license}")


@app.command("check-model")
def check_model(
    role: str = typer.Option("material", help="role to test"),
    model: str = typer.Option(None, help="catalog key, defaults to the active one"),
    frame: Path = typer.Option(None, help="image to send for the material role"),
) -> None:
    """Make one real call and report what actually happened.

    This is the access smoke test: before trusting a model, confirm it can be
    reached, that its structured output works, and what a single call costs in
    tokens and latency. Documentation is not evidence for any of those.
    """
    import asyncio

    from .model_registry import card_for
    from .providers import Role, get_provider
    from .providers.base import CostRecord, set_cost_sink
    from .specs.segment import RawSegment

    role_enum = Role(role)
    card = card_for(role_enum, model)
    typer.echo(f"model:    {card.key}  ({card.vendor}/{card.model_id})")
    typer.echo(f"adapter:  {card.adapter}   structured_output={card.structured_output}")
    if card.unknowns:
        typer.echo(f"unknowns: {', '.join(card.unknowns)}")

    records: list[CostRecord] = []
    set_cost_sink(records.append)
    provider = get_provider(role_enum, model, project_id="smoke")

    try:
        if role_enum in (Role.MATERIAL, Role.REFERENCE):
            if frame is None:
                raise typer.BadParameter("--frame is required for this role")
            asyncio.run(
                provider.analyze_segment(
                    RawSegment(
                        segment_id="smoke", project_id="smoke",
                        asset_id="smoke", start=0.0, end=3.0,
                    ),
                    [frame],
                )
            )
        elif role_enum == Role.CRITIC:
            asyncio.run(provider.compare({"clips": 8}, {"clips": 20}, {}))
        else:
            asyncio.run(provider.parse_intent("剪一个45秒的旅行vlog，轻松一点"))
    except Exception as exc:
        typer.echo(f"\nFAILED: {exc}")
        if exc.__cause__:
            typer.echo(f"  cause: {exc.__cause__}")
        for message in getattr(provider, "attempt_errors", []):
            typer.echo(f"  attempt: {message}")
        raise typer.Exit(1)
    finally:
        set_cost_sink(None)

    repairs = getattr(provider, "client", None)
    record = records[-1] if records else None
    typer.echo("\nOK")
    if record:
        typer.echo(f"  latency:  {record.latency_ms} ms")
        typer.echo(f"  tokens:   in={record.tokens_in} out={record.tokens_out}")
        typer.echo(f"  retries:  {record.retries}")
    for message in getattr(provider, "attempt_errors", []):
        typer.echo(f"  retried after: {message}")
    throttled = getattr(provider, "throttled", 0)
    if throttled:
        typer.echo(f"  429s absorbed: {throttled}")
    if repairs is not None:
        typer.echo(
            f"  repairs:  {repairs.last_repairs}"
            "   (first-pass JSON validity is a selection criterion)"
        )


def _not_yet(block: str, name: str) -> None:
    typer.echo(
        f"'{name}' belongs to build block {block} and is not implemented yet.\n"
        f"See docs/TECHNICAL_PLAN.md section 13 for the checklist."
    )
    raise typer.Exit(2)


@app.command()
def ingest(
    materials: Path = typer.Option(..., help="directory of user footage"),
    project: str = typer.Option(None, help="project id, generated if omitted"),
    prompt: str = typer.Option(None, help="one-line request"),
) -> None:
    """Probe user footage and build normalised analysis proxies."""
    from .material import ingest as run_ingest

    init_db()
    result = run_ingest(materials, project, prompt)
    total = sum(a.duration for a in result.assets if a.kind == "video")
    typer.echo(f"project {result.project_id}")
    typer.echo(
        f"  {result.videos} videos ({total:.1f}s), {result.photos} photos"
    )
    for asset in result.assets:
        if asset.kind == "photo":
            typer.echo(
                f"  {asset.asset_id}  photo    "
                f"{asset.width}x{asset.height}"
            )
        else:
            typer.echo(
                f"  {asset.asset_id}  {asset.duration:6.2f}s  "
                f"{asset.width}x{asset.height} @{asset.fps:.0f}fps"
                f"  audio={'yes' if asset.has_audio else 'no'}"
            )
    for path, reason in result.skipped:
        typer.echo(f"  SKIPPED {path.name}: {reason}")


@app.command()
def analyze(
    project: str = typer.Option(..., help="project id from ingest"),
    concurrency: int = typer.Option(8, help="max concurrent model calls"),
    limit: int = typer.Option(
        None, help="only send this many segments to the model (sampling)"
    ),
) -> None:
    """Split into segments, measure quality, label semantics, embed."""
    import asyncio
    import json as _json

    from .material import analyze_project
    from .providers.cost import install_db_cost_sink

    init_db()
    install_db_cost_sink()
    def report(done: int, total: int, segment) -> None:
        typer.echo(
            f"  [{done:>3}/{total}] {segment.segment_id:<26}"
            f" {segment.shot_type:<18} {segment.semantic_role:<20}"
            f" {(segment.semantic or '')[:44]}"
        )

    result = asyncio.run(
        analyze_project(
            project, concurrency=concurrency, on_segment=report, limit=limit
        )
    )
    summary = result.summary()
    typer.echo(
        f"{summary['segment_count']} segments, "
        f"{summary['usable_count']} usable, "
        f"{summary['rejected_count']} rejected, "
        f"{summary['total_usable_duration']}s usable footage"
    )
    typer.echo(_json.dumps(summary["by_semantic_role"], ensure_ascii=False, indent=2))
    if summary["reject_reasons"]:
        typer.echo(f"reject reasons: {summary['reject_reasons']}")
    if summary.get("unlabelled_count"):
        typer.echo(
            f"unlabelled: {summary['unlabelled_count']} segments kept their "
            f"deterministic fields but have no semantics"
        )
    for segment_id, reason in result.failed:
        typer.echo(f"  FAILED {segment_id}: {reason}")


@app.command()
def plan(
    project: str = typer.Option(..., help="project id"),
    prompt: str = typer.Option(None, help="one-line request"),
    duration: float = typer.Option(None, help="target seconds, overrides the prompt"),
    candidates: int = typer.Option(None, help="how many plans to generate"),
) -> None:
    """Generate EditPlanSpec candidates from the pool summary."""
    import asyncio

    from .pipeline import load_pool, pool_summary
    from .planner import PlanningContext, generate_plans, parse_intent

    init_db()
    segments, _ = load_pool(project)
    usable = [s for s in segments if s.is_usable]
    if not usable:
        typer.echo(f"project {project} has no usable segments; run analyze first")
        raise typer.Exit(1)

    intent = asyncio.run(parse_intent(prompt, target_duration=duration))
    typer.echo(
        f"intent: {intent.target_duration:.0f}s  style={intent.style}"
        f"  constraints={intent.user_constraints or '-'}"
    )
    summary = pool_summary(project, segments)
    context = PlanningContext(project, intent, summary)
    for spec in generate_plans(context, candidates or settings.candidate_count):
        variant = spec.provenance.extra.get("variant", "?")
        typer.echo(
            f"  {variant:<14} {len(spec.slots):3d} slots"
            f"  {spec.planned_duration():5.1f}s  pace={spec.rhythm.pace:<12}"
            f" transitions={spec.rhythm.transition_density:.2f}"
            f"  [{spec.story.narrative_arc}]"
        )


@app.command()
def match(
    project: str = typer.Option(..., help="project id"),
    prompt: str = typer.Option(None, help="one-line request"),
    duration: float = typer.Option(None, help="target seconds"),
    candidates: int = typer.Option(None, help="how many plans to generate"),
) -> None:
    """Build and rank candidate timelines without rendering."""
    from .pipeline import run_sync

    init_db()
    result = run_sync(
        project_id=project, prompt=prompt, target_duration=duration,
        candidates=candidates, render=False,
    )
    for note in result.notes:
        typer.echo(note)
    for timeline, score in result.candidates:
        variant = timeline.provenance.extra.get("variant", "?")
        dims = score.dimensions
        typer.echo(
            f"  {score.overall:.3f}  {variant:<14} {len(timeline.clips):3d} clips"
            f"  {timeline.duration:5.1f}s"
            f"  cover={dims['slot_coverage']:.2f}"
            f" div={dims['shot_diversity']:.2f}"
            f" rhythm={dims['rhythm_fidelity']:.2f}"
            f" sem={dims['semantic_match']:.2f}"
            f" dur={dims['duration_error']:.2f}"
            f" rep={dims['repetition_penalty']:.2f}"
        )
        for note in score.notes[:3]:
            typer.echo(f"       - {note}")


@app.command()
def run(
    materials: Path = typer.Option(None, help="footage directory; skips ingest if unset"),
    project: str = typer.Option(None, help="existing project id"),
    prompt: str = typer.Option(None, help="one-line request"),
    duration: float = typer.Option(None, help="target seconds"),
    candidates: int = typer.Option(None, help="how many plans to generate"),
    quality: str = typer.Option("hq", help="proxy | hq"),
    out: Path = typer.Option(None, help="output file"),
    keep_sound: bool = typer.Option(
        False, "--keep-sound", help="keep the footage's own audio under the music"
    ),
) -> None:
    """Full Mode A pipeline: footage to MP4."""
    import asyncio

    from .material import analyze_project
    from .material import ingest as run_ingest
    from .pipeline import run_sync
    from .providers.cost import install_db_cost_sink

    init_db()
    install_db_cost_sink()

    if materials is not None:
        ingested = run_ingest(materials, project, prompt)
        project = ingested.project_id
        typer.echo(f"ingested {len(ingested.assets)} assets into {project}")
        analysis = asyncio.run(analyze_project(project))
        summary = analysis.summary()
        typer.echo(
            f"analysed: {summary['usable_count']}/{summary['segment_count']} usable"
        )
    if project is None:
        raise typer.BadParameter("pass --materials or --project")

    result = run_sync(
        project_id=project, prompt=prompt, target_duration=duration,
        candidates=candidates, quality=quality, out_path=out,
        keep_original_sound=keep_sound,
    )
    for note in result.notes:
        typer.echo(note)
    if result.best is not None:
        variant = result.best.provenance.extra.get("variant", "?")
        typer.echo(
            f"winner: {variant}  {len(result.best.clips)} clips"
            f"  {result.best.duration:.2f}s"
        )
    if result.render_path:
        typer.echo(f"rendered: {result.render_path}")


@app.command("import-music")
def import_music(
    directory: Path = typer.Option(Path("data/music"), help="music directory"),
) -> None:
    """Import licensed tracks and precompute their beat grids."""
    from .music import import_library

    init_db()
    tracks = import_library(directory)
    if not tracks:
        typer.echo(
            f"no tracks imported from {directory}. Each audio file needs a "
            f"sibling .json declaring at least {{\"license\": \"...\"}}."
        )
        raise typer.Exit(1)
    for track in tracks:
        bpm = f"{track.bpm:.1f}" if track.bpm else "?"
        typer.echo(
            f"  {track.music_id:<20} {track.license:<12} bpm={bpm:<7}"
            f" beats={len(track.beat_times):<5} {track.mood_tags}"
        )


if __name__ == "__main__":
    app()
