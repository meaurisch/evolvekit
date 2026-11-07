"""Regenerate the demo-tour samples: small tour requests, stops in kilometres.

    python harnesses/demo-tour/samples/make_samples.py

Seeded, so the files it writes are always the same.
"""

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = {"north-south-30": 30, "town-40": 40, "county-50": 50, "region-60": 60}


def main() -> None:
    for index, (name, count) in enumerate(SIZES.items()):
        rng = random.Random(4000 + index)
        # Two clusters and a scatter: tours with a structure worth finding.
        centres = [(rng.uniform(1, 4), rng.uniform(6, 9)), (rng.uniform(6, 9), rng.uniform(1, 4))]
        stops = []
        for i in range(count):
            if i % 3 == 2:
                x, y = rng.uniform(0, 10), rng.uniform(0, 10)
            else:
                cx, cy = centres[i % 2]
                x, y = cx + rng.gauss(0, 0.9), cy + rng.gauss(0, 0.9)
            stops.append([round(min(10, max(0, x)), 3), round(min(10, max(0, y)), 3), 1.0])
        tags = {str(i): ("north" if s[1] >= 5 else "south") for i, s in enumerate(stops)}
        (HERE / f"{name}.json").write_text(json.dumps({"name": name, "stops": stops, "tags": tags}) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
