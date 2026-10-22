"""Build an explicit source ZIP at a new external location; never copy runtime data."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    from app.v2_source_package import build
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    print(json.dumps(build(ROOT,args.output),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
