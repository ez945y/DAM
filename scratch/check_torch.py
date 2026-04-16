import os
import sys

print(f"Python version: {sys.version}")
print(f"CWD: {os.getcwd()}")

try:
    import torch

    print(f"Torch __path__: {getattr(torch, '__path__', 'N/A')}")
    print(f"Torch __file__: {getattr(torch, '__file__', 'N/A')}")
    import torch.nn as nn

    print(f"Torch.nn __path__: {getattr(nn, '__path__', 'N/A')}")
    print(f"Torch.nn available: {hasattr(nn, 'Module')}")
    print(f"Torch.nn attributes: {dir(nn)[:20]}...")
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    import traceback

    traceback.print_exc()
