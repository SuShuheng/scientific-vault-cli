"""Source entry point; works without installation."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from svault.cli import main

if __name__ == '__main__':
    main()
