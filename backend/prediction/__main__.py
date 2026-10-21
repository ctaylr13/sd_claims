import argparse
import json
from pathlib import Path

from . import CandidateFact, build_prediction_bundle, load_snapshot


def main():
    parser = argparse.ArgumentParser(description='Build historical inputs from a frozen approval snapshot')
    parser.add_argument('snapshot_root', type=Path)
    parser.add_argument('snapshot_id')
    parser.add_argument('candidates', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshot = load_snapshot(args.snapshot_root, args.snapshot_id)
    candidates = tuple(CandidateFact.model_validate(item) for item in json.loads(args.candidates.read_text()))
    bundle = build_prediction_bundle(snapshot, candidates)
    with args.output.open('x') as stream:
        stream.write(bundle.model_dump_json(indent=2)+'\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
