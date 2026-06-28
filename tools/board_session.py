"""
Board Session — CLI wrapper
Run: python tools/board_session.py "Your question here"
Or use the dashboard: python app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from board import run_board, BOARD


def print_session(data):
    print("\n" + "="*60)
    print("  BOARD SESSION")
    print("="*60)
    print(f"\n{data['question']}\n")

    for r in data['responses']:
        print(f"\n{'='*60}")
        print(f"  {r['name'].upper()}")
        print(f"  {r['role']}")
        print(f"{'='*60}")
        print(f"\n{r['response']}\n")

    print("\n" + "="*60)
    print("  BOARD SYNTHESIS")
    print("="*60)
    print(f"\n{data['synthesis']}\n")
    print("="*60)
    print(f"\n  Session saved: {data['session_id']}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/board_session.py \"Your question here\"")
        print("Or run the dashboard: python app.py")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result = run_board(question)
    print_session(result)
