import os

import yaml

from dam.config.schema import StackfileConfig

path = ".dam_stackfile.yaml"
if os.path.exists(path):
    with open(path) as f:
        raw = yaml.safe_load(f)
    print(f"Testing validation for {path}...")
    try:
        config = StackfileConfig(**raw)
        print("Success! Config is valid.")
        print(f"Guards: {config.guards}")
    except Exception as e:
        print(f"Validation failed: {e}")
else:
    print(f"{path} not found.")
