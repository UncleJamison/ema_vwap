import os

if os.path.exists("src/app.py"):
    print("src/app.py exists")
    # Check if it's been modified
    with open("src/app.py", "r") as f:
        content = f.read()
        if "# Tear Sheet Export Endpoints" in content:
            print("Tear sheet block found")
        # Count occurrences of StrategyParams import
        import_count = content.count("from src.config import")
        print(f"StrategyParams import count: {import_count}")

        # Check for valid imports in tear-sheet section
        lines = content.split("\n")
        in_tear_section = False
        import_lines = []
        for i, line in enumerate(lines):
            if "Tear Sheet Export Endpoints" in line:
                in_tear_section = True
            elif in_tear_section and line.startswith("# ") and "Paper Trading" in line:
                break
            if in_tear_section and "from src.config import" in line:
                import_lines.append((i + 1, line))
        print(f"Imports in tear section: {len(import_lines)}")
        for lineno, imp in import_lines[:5]:
            print(f"  Line {lineno}: {imp}")
