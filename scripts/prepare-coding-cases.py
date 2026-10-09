"""Generate new fixed Git repositories and locally validate authored acceptance contracts."""
import argparse
import json
from pathlib import Path

from forgeagent.coding_cases import prepare

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path, help="New output directory; existing directories are refused")
args = parser.parse_args()
print(json.dumps(prepare(args.destination), indent=2))
