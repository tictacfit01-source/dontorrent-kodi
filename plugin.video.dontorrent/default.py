import sys
from resources.lib import main

if __name__ == "__main__":
    qs = sys.argv[2][1:] if len(sys.argv) > 2 and sys.argv[2].startswith("?") else ""
    main.router(qs)
