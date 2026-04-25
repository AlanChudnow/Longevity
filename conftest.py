import sys
from pathlib import Path

# Ensure project root is importable so `import src.health_models` works in tests
sys.path.insert(0, str(Path(__file__).parent))
