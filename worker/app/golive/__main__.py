"""python -m app.golive quick|full|probe  (run any check by hand; results also go to Telegram)"""

import json
import sys

from app.golive import checks
from app.golive.probe import probe_all


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else "quick"
    if kind == "probe":
        for r in probe_all():
            print(json.dumps(r.as_dict()))
        return 0
    status, result = checks.run(kind)
    print(checks.render(kind, result, __import__("datetime").datetime.now()))
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
