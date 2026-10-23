"""Command line: build the graph, run both access arms, write the report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_DATA = Path("data/osm")
DEFAULT_POP = Path("data/pop_h3.csv")
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


def cmd_access(args: argparse.Namespace) -> int:
    from geonet.access import SENSITIVITY_GRID, AccessConfig, run_access, write_access_json

    city, origins = _load(args)
    primary = AccessConfig(args.speed, args.minutes)
    configs = list(SENSITIVITY_GRID)
    if primary not in configs:
        configs.append(primary)
    result = run_access(city, origins, configs, primary=primary, n_boot=args.n_boot, seed=args.seed)
    out = args.out or (Path("results/synthetic/access.json") if args.synthetic else None)
    if out is None:
        out = Path("results/access.json")
    write_access_json(result, out)
    s = result["summary"]
    lo, hi = s["ci_cluster"]
    print(f"threshold      {s['threshold_m']:.0f} m ({args.speed:g} km/h x {args.minutes:g} min)")
    print(f"buffer share   {100 * s['share_buffer']:.1f}%")
    print(f"network share  {100 * s['share_network']:.1f}%")
    print(f"gap            {s['gap_pp']:.1f} pp  [{lo:.1f}, {hi:.1f}] (cluster bootstrap)")
    print(f"verdict        {s['verdict']}")
    print(f"wrote {out}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from geonet.report import load_access, write_report, write_spot_check

    access = load_access(args.access)
    out_dir = Path(args.out) if args.out else Path(args.access).parent
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
    from geonet.report import score_spot_check

    result = score_spot_check(args.csv)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("passed") else 1


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
    p_access.add_argument("--pop", type=Path, default=DEFAULT_POP, help="H3 population CSV")
    p_access.add_argument("--speed", type=float, default=5.0, help="walking speed, km/h")
    p_access.add_argument("--minutes", type=float, default=15.0, help="time threshold, minutes")
    p_access.add_argument("--n-boot", type=int, default=2000)
    p_access.add_argument("--out", type=Path, default=None)
    p_access.set_defaults(func=cmd_access)

    p_report = sub.add_parser("report", help="tables, maps and the spot-check sheet")
    p_report.add_argument("--access", type=Path, default=Path("results/access.json"))
    p_report.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p_report.add_argument("--out", type=Path, default=None)
    p_report.add_argument("--spot-check", action="store_true", help="also write the sheet")
    p_report.set_defaults(func=cmd_report)

    p_score = sub.add_parser("score-spot-check", help="score the owner-filled spot-check CSV")
    p_score.add_argument("csv", type=Path)
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
