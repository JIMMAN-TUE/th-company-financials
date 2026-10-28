"""Build on Windows: python scripts/build_windows_app.py. Never reads the user workspace."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows (see windows-app workflow).")
    root = Path(__file__).resolve().parent.parent
    browser_dir = root / "build" / "browser-runtime"
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browser_dir)}
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, cwd=root, env=env)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
                    "--name", "thfin", "--collect-all", "thfin", "--collect-all", "playwright",
                    "--add-data", f"{browser_dir}{os.pathsep}browsers", "scripts/launch_app.py"],
                   check=True, cwd=root, env=env)
    app_dir = root / "dist" / "thfin"
    shutil.copyfile(root / "docs" / "WINDOWS_APP.md", app_dir / "START-HERE.md")
    subprocess.run([str(app_dir / "thfin.exe"), "--self-test"], check=True, cwd=root)
    archive = shutil.make_archive(str(root / "dist" / "thfin-windows"), "zip", root_dir=root / "dist", base_dir="thfin")
    print(f"Ready to share: {archive}")


if __name__ == "__main__":
    main()
