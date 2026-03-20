from pathlib import Path

namespace = {}
code = Path("../version.py").read_text(encoding="utf-8")
exec(code, namespace)

version = namespace.get("__version__")
app_name = namespace.get("APP_NAME", "Qt5PythonApp")

if not version:
    raise SystemExit("Keine __version__ in version.py gefunden.")

print(version)
print(app_name)
