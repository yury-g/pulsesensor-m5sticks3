#!/usr/bin/python3
"""Run the PulseLink startup-timing matrix n times per scenario and aggregate.

`timeit.py` measures ONE start and prints a full annotated log, which is what
you want while debugging. It is the wrong tool for deciding whether a change
actually helped: at n=2 the medians in the previous handoff were inside the
noise, and the honest note there said so.

This drives timeit's own primitives n times per scenario, keeps only the
metrics, and reports min/median/max plus the spread. Every run is appended to a
JSONL file as it completes, so a run that dies at scenario 4 does not cost the
first three.

  ./tools/timing_matrix.py <out.jsonl> <label> [n] [scenarios...]

The metric that means anything is **AP-up -> first valid packet**: the window
the sender actually controls. Boot time before the AP exists is the Tab5
firmware's, and no amount of sender tuning touches it.

Two scenarios cannot report that metric, by construction, and are NOT failures:
  * stick_only - the Tab5 is never reset, so it already knows this device id
    and never prints "joined" again. Use `assoc_stickclock`.
  * tab5_only  - the stick is never reset, so it never reprints its own boot
    lines; its clock origin is unrecoverable for that run.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeit as T                                    # noqa: E402

ALL_SCENARIOS = ("both", "stick_first", "tab5_first", "tab5_only",
                 "stick_only")
DURATION = 25.0          # seconds of passive reading per run
GAP = 3.0                # stagger for the *_first scenarios
SETTLE = 6.0             # let both boards go quiet before the next reset


def metrics(stick, tab5):
    t_ap, _ = T.first(tab5.lines, "LINK: SoftAP")
    t_join, _ = T.first(tab5.lines, "joined")
    t_up, _ = T.first(stick.lines, "LINK: up")
    t_fw, _ = T.first(tab5.lines, "Skip sync")
    t_app = T.stick_boot_origin(stick.lines)

    m = {"ap": t_ap, "fw": t_fw, "assoc": t_up, "join": t_join, "app": t_app}
    if t_ap is not None and t_join is not None:
        m["ap_to_pkt"] = t_join - t_ap
    if t_ap is not None and t_up is not None:
        m["ap_to_assoc"] = t_up - t_ap
    if t_app is not None and t_up is not None:
        m["assoc_stickclock"] = t_up - t_app

    # Health: did the link actually carry traffic, and did anything get
    # rejected? A fast "first packet" from a link that then died is not a win.
    rx = bad = rate = 0
    for _t, s in tab5.lines:
        if s.startswith("rx=") or " rx=" in s:
            for tok in s.split():
                try:
                    if tok.startswith("rx="):
                        rx = max(rx, int(tok[3:]))
                    elif tok.startswith("bad="):
                        bad = max(bad, int(tok[4:]))
                    elif tok.startswith("rate="):
                        rate = max(rate, int(tok[5:].rstrip("/s")))
                except ValueError:
                    pass
    m["rx"] = rx
    m["bad"] = bad
    m["rate"] = rate
    return m


def stats(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0
    return {"n": n, "min": v[0], "med": med, "max": v[-1]}


def fmt(s):
    if s is None:
        return "%-22s" % "-"
    return "%5.2f / %5.2f / %5.2f  (n=%2d)" % (s["min"], s["med"], s["max"],
                                               s["n"])


def main():
    out_path, label = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    scenarios = tuple(sys.argv[4:]) or ALL_SCENARIOS

    results = {sc: [] for sc in scenarios}
    t_started = time.time()
    total = len(scenarios) * n
    done = 0

    with open(out_path, "a") as fh:
        for sc in scenarios:
            for i in range(n):
                done += 1
                el = time.time() - t_started
                eta = (el / done) * (total - done) if done else 0
                print("[%5.1fm] %-12s run %2d/%d   (eta %.0fm)"
                      % (el / 60.0, sc, i + 1, n, eta / 60.0), flush=True)
                r = T.run(sc, GAP, DURATION)
                if r is None:
                    print("  RUN FAILED: could not open a port", flush=True)
                    time.sleep(SETTLE)
                    continue
                _t0, fired, stick, tab5 = r
                m = metrics(stick, tab5)
                m["scenario"] = sc
                m["label"] = label
                m["run"] = i + 1
                m["fired"] = [[f[0], round(f[1], 3)] for f in fired]
                fh.write(json.dumps(m) + "\n")
                fh.flush()
                results[sc].append(m)
                print("   ap_to_pkt=%s assoc=%s rx=%d bad=%d rate=%d"
                      % (("%.2f" % m["ap_to_pkt"]) if "ap_to_pkt" in m
                         else "-",
                         ("%.2f" % m["ap_to_assoc"]) if "ap_to_assoc" in m
                         else "-",
                         m["rx"], m["bad"], m["rate"]), flush=True)
                time.sleep(SETTLE)

    print("\n=== %s  (min / median / max, seconds)" % label)
    print("%-13s %-30s %-30s %s"
          % ("scenario", "AP-up -> first packet", "AP-up -> assoc",
             "rx / bad"))
    for sc in scenarios:
        rs = results[sc]
        print("%-13s %-30s %-30s %s"
              % (sc,
                 fmt(stats([r.get("ap_to_pkt") for r in rs])),
                 fmt(stats([r.get("ap_to_assoc") for r in rs])),
                 "%d runs, bad=%d, min rx=%d"
                 % (len(rs), max([r["bad"] for r in rs] or [0]),
                    min([r["rx"] for r in rs] or [0]))))


if __name__ == "__main__":
    main()
