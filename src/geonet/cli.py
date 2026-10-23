"""Command line: build the graph, run both access arms, write the report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from geonet import registered

DEFAULT_DATA = Path("data/osm")
DEFAULT_POP = Path("data/pop_h3.csv")
POP_R10 = "pop_h3_r10.csv"
DEFAULT_ACCESS = Path("results/access.json")
R10_ACCESS = Path("results/access_r10.json")
# 250 m spacing makes the grid 2.75 km wide, so the 1,250 m threshold actually separates cells.
SYNTHETIC_RIVER = {"spacing_m": 250.0, "river_row": 5, "bridge_col": 0}


def _load(args: argparse.Namespace):
    """Return (city, origins); origins is None when no population is needed."""
    if args.synthetic:
        from geonet.synthetic import synthetic_city

        return synthetic_city(**SYNTHETIC_RIVER, pop_seed=args.seed)
    from geonet.osm import load_extract

    city = load_extract(args.data)
    origins = None
    if getattr(args, "pop", None) is not None:
        from geonet.hexgrid import cells_to_origins, load_pop

        origins = cells_to_origins(load_pop(args.pop), city.crs)
    return city, origins


def cmd_build(args: argparse.Namespace) -> int:
    from geonet.network import build_graph

    city, _ = _load(args)
    full = build_graph(city.nodes, city.edges, largest_component=False)
    graph = build_graph(city.nodes, city.edges)
    kinds = city.facilities["kind"].value_counts().sort_index().to_dict()
    print(f"crs        {city.crs}")
    print(f"aoi        {tuple(round(v, 1) for v in city.aoi)}")
    print(f"nodes      {full.number_of_nodes():,} ({graph.number_of_nodes():,} connected)")
    print(f"edges      {full.number_of_edges():,} ({graph.number_of_edges():,} connected)")
    print(f"facilities {len(city.facilities):,} {kinds}")
    return 0


def _access_out(args: argparse.Namespace) -> Path:
    """Where the access result goes; the resolution-10 run never overwrites the primary."""
    is_r10 = not args.synthetic and Path(args.pop).name == POP_R10
    if args.out is not None:
        if is_r10 and Path(args.out) == DEFAULT_ACCESS:
            raise ValueError(f"the resolution-10 run must not overwrite {DEFAULT_ACCESS}")
        return Path(args.out)
    if args.synthetic:
        return Path("results/synthetic/access.json")
    return R10_ACCESS if is_r10 else DEFAULT_ACCESS


def _pop_resolution(origins) -> int | None:
    """H3 resolution of the origin cells, or None when there are none."""
    import h3

    if origins is None or len(origins) == 0:
        return None
    return h3.get_resolution(str(origins["cell_id"].iloc[0]))


def cmd_access(args: argparse.Namespace) -> int:
    from geonet.access import SENSITIVITY_GRID, AccessConfig, run_access, write_access_json

    out = _access_out(args)
    city, origins = _load(args)
    problems: list[str] = []
    if not args.synthetic:
        from geonet.osm import check_registered_extract, read_meta

        problems = check_registered_extract(city, read_meta(args.data))
    is_registered = registered.is_registered_run(
        synthetic=args.synthetic,
        speed_kmh=args.speed,
        minutes=args.minutes,
        n_boot=args.n_boot,
        seed=args.seed,
        extract_problems=problems,
        pop_resolution=None if args.synthetic else _pop_resolution(origins),
    )
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)

    primary = AccessConfig(args.speed, args.minutes)
    configs = list(SENSITIVITY_GRID)
    if primary not in configs:
        configs.append(primary)
    result = run_access(
        city,
        origins,
        configs,
        primary=primary,
        n_boot=args.n_boot,
        seed=args.seed,
        registered=is_registered,
    )
    write_access_json(result, out)
    s = result["summary"]
    lo, hi = s["ci_cluster"]
    print(f"threshold      {s['threshold_m']:.0f} m ({args.speed:g} km/h x {args.minutes:g} min)")
    print(f"buffer share   {100 * s['share_buffer']:.2f}%")
    print(f"network share  {100 * s['share_network']:.2f}%")
    print(f"gap            {s['gap_pp']:.2f} pp  [{lo:.2f}, {hi:.2f}] (cluster bootstrap)")
    if s.get("verdict") is None:
        print("verdict        not the registered analysis (no verdict)")
    else:
        print(f"verdict        {s['verdict']}")
    print(f"wrote {out}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from geonet.report import load_access, require_registered, write_report, write_spot_check

    access = load_access(args.access)
    if args.spot_check:
        require_registered(access)  # fail before anything is written
    access_path = Path(args.access)
    if args.out:
        out_dir = Path(args.out)
    elif access_path.name == DEFAULT_ACCESS.name:
        out_dir = access_path.parent
    else:  # access_r10.json and friends get their own folder, never the primary report
        out_dir = access_path.parent / access_path.stem
    edges = None
    synthetic = access["config"]["crs"].startswith("LOCAL")
    if args.data is not None and Path(args.data).exists() and not synthetic:
        from geonet.osm import load_extract

        edges = load_extract(args.data).edges
    path = write_report(access, out_dir, city_edges=edges)
    print(f"wrote {path}")
    if args.spot_check:
        write_spot_check(access, out_dir / "spot_check.csv", out_dir / "spot_check.md")
        print(f"wrote {out_dir / 'spot_check.csv'} and spot_check.md")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    from geonet.report import load_access, score_spot_check

    result = score_spot_check(args.csv, load_access(args.access))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        print(f"verdict {result['status']}: write up every miss in NOTES.md", file=sys.stderr)
    return 0 if result["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geonet", description="Straight-line buffer vs walking network access."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def source(p: argparse.ArgumentParser) -> None:
        p.add_argument("--synthetic", action="store_true", help="use the seeded grid with a river")
        p.add_argument("--data", type=Path, default=DEFAULT_DATA, help="OSM extract directory")
        p.add_argument("--seed", type=int, default=20261023)

    p_build = sub.add_parser("build", help="load the extract and print graph counts")
    source(p_build)
    p_build.set_defaults(func=cmd_build)

    p_access = sub.add_parser("access", help="run both arms and the sensitivity grid")
    source(p_access)
    p_access.add_argument(
        "--pop",
        type=Path,
        default=DEFAULT_POP,
        help=f"H3 population CSV; {POP_R10} writes {R10_ACCESS} (not the registered analysis)",
    )
    p_access.add_argument("--speed", type=float, default=5.0, help="walking speed, km/h")
    p_access.add_argument("--minutes", type=float, default=15.0, help="time threshold, minutes")
    p_access.add_argument("--n-boot", type=int, default=2000)
    p_access.add_argument("--out", type=Path, default=None, help="access JSON path")
    p_access.set_defaults(func=cmd_access)

    p_report = sub.add_parser("report", help="tables, maps and the spot-check sheet")
    p_report.add_argument("--access", type=Path, default=DEFAULT_ACCESS)
    p_report.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p_report.add_argument("--out", type=Path, default=None)
    p_report.add_argument(
        "--spot-check", action="store_true", help="also write the sheet (registered access only)"
    )
    p_report.set_defaults(func=cmd_report)

    p_score = sub.add_parser("score-spot-check", help="score the owner-filled spot-check CSV")
    p_score.add_argument("csv", type=Path)
    p_score.add_argument("--access", type=Path, default=DEFAULT_ACCESS)
    p_score.set_defaults(func=cmd_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
