"""Build and check the complete portable Windows folder, then zip it for delivery."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED


def validate_windows_executable(path):
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Not a Windows executable")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0" or struct.unpack("<H", stream.read(2))[0] != 0x8664:
            raise ValueError("Expected a Windows x64 PE executable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-environment", choices=["native-windows", "linux-wine"], default="native-windows")
    parser.add_argument("--package-existing", action="store_true", help="Recheck and zip an already built folder")
    parser.add_argument('--speech-test-dir', type=Path, help='Optional developer check: verified tiny model and official public-jfk.wav; never a microphone')
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Run with Windows x64 Python; Linux PyInstaller cannot build this exe.")
    root = Path(__file__).resolve().parents[1]
    source_hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (root / 'src/our_little_days').rglob('*')
                     if path.is_file() and path.suffix in {'.py', '.json', '.txt'}}
    source_hashes['scripts/windows_entry.py'] = hashlib.sha256((root/'scripts/windows_entry.py').read_bytes()).hexdigest()
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    if not args.package_existing:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
            "--name", "OurLittleDays", "--paths", str(root / "src"), "--collect-data", "our_little_days", "--collect-data", "opencc",
            "--additional-hooks-dir", str(root / 'packaging/hooks'),
            "--workpath", str(root / "build/windows"), "--specpath", str(root / "build/windows"),
            "--distpath", str(root / "dist"), str(root / "scripts/windows_entry.py"),
        ], cwd=root, check=True)
    if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != digest for name, digest in source_hashes.items()):
        raise ValueError('Runtime source changed during build; rebuild before delivery')
    folder = root / "dist/OurLittleDays"
    speech_source = root / 'packaging/speech-runtime'
    speech_manifest = json.loads((speech_source / 'SOURCE.json').read_text(encoding='utf-8'))
    if speech_manifest['version'] != '1.8.2' or speech_manifest['archive_sha256'] != 'b1514ebc099765e39fa37eb780b92a140a94c86bb0b3b3d98226b38825979732':
        raise ValueError('Unexpected official Whisper runtime release')
    for name, digest in speech_manifest['files_sha256'].items():
        path = speech_source / name
        validate_windows_executable(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Whisper runtime checksum mismatch: {name}')
    shutil.copytree(speech_source, folder / 'speech-runtime', dirs_exist_ok=True)
    executable = folder / "OurLittleDays.exe"
    validate_windows_executable(executable)
    # Only redistribute actual Python/wheel binaries. In particular, never bundle
    # Wine system DLLs or compatibility forwarders used by the build environment.
    official_hashes = set(speech_manifest['files_sha256'].values())
    for prefix in {Path(sys.prefix), Path(sys.base_prefix)}:
        for path in prefix.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".dll", ".pyd"}:
                official_hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
    checked_binaries = 0
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".dll", ".pyd"}:
            validate_windows_executable(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() not in official_hashes:
                raise ValueError(f"Unexpected binary origin: {path}")
            checked_binaries += 1
    packaged = folder / "_internal/our_little_days/data/characters"
    for source in (root / "src/our_little_days/data/characters").glob("*.json"):
        if (packaged / source.name).read_bytes() != source.read_bytes():
            raise ValueError(f"Packaged profile differs: {source.name}")
    report_file = artifacts / "windows-package-check.json"
    report_file.unlink(missing_ok=True)
    check_command = [str(executable), "--package-check", str(report_file)]
    if args.speech_test_dir:
        check_command += ['--speech-test-dir', str(args.speech_test_dir)]
    subprocess.run(check_command, cwd=folder, timeout=240 if args.speech_test_dir else 120, check=True)
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if report["status"] != "passed" or report["frozen"] is not True:
        raise ValueError("Frozen package check failed")
    report["build_environment"] = args.build_environment
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ["WINDOWS_USER_GUIDE.md", "WINDOWS_TEST_CHECKLIST.md", "VOICE_INPUT.md", "QUIET_CHAT.md"]:
        shutil.copyfile(root / "docs" / name, folder / name)
    (folder / "START_HERE.txt").write_text((root / "docs/WINDOWS_USER_GUIDE.md").read_text(encoding="utf-8"), encoding="utf-8-sig")
    shutil.copytree(root / "packaging/licenses", folder / "LICENSES", dirs_exist_ok=True)
    shutil.copyfile(speech_source / 'LICENSE', folder / 'LICENSES/whisper.cpp-MIT.txt')
    # Retain license files shipped by dependencies as well as the LGPL text.
    from importlib.metadata import distribution
    for name in ["PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller", "webrtcvad-wheels", "opencc-python-reimplemented"]:
        package = distribution(name)
        for entry in package.files or []:
            if "/licenses/" in entry.as_posix() or entry.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                destination = folder / "LICENSES" / name / entry.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package.locate_file(entry), destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, folder / "LICENSES/Python-LICENSE.txt")
    info = {
        "version": "0.4.7",
        "runtime_source_sha256": source_hashes,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "build_environment": args.build_environment,
        "python": sys.version,
        "target": "Windows 10/11 x64",
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "package_check": "passed (offscreen and temporary data only)",
        "official_python_wheel_binaries_verified": checked_binaries,
        "whisper_runtime": speech_manifest,
        "windows_manual_acceptance": "not_run",
        "signing": "unsigned",
        "scope": "Current independent voice table and pinned latest input after full context; erroneous historical voice annotated without editing saved chat. Non-blocking local Shogo nickname/keigo and unattached-image claim advisories, persisted reported provider model/image evidence. One request per batch, no paid repair/retry. Full 13 personas, SQLite/AU/peer continuity, latest-user roster, translation display and all-request budget retained. Native Windows/live model quality acceptance pending.",
    }
    (folder / "BUILD_INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(report_file, folder / "PACKAGE_CHECK.json")
    (folder / "開啟安裝說明.cmd").write_bytes(b'@echo off\r\ncd /d "%~dp0"\r\nnotepad.exe START_HERE.txt\r\n')
    (folder / "開啟資料資料夾.cmd").write_bytes(b'@echo off\r\nif not exist "%LOCALAPPDATA%\\OurLittleDays" mkdir "%LOCALAPPDATA%\\OurLittleDays"\r\nstart "" explorer.exe "%LOCALAPPDATA%\\OurLittleDays"\r\n')
    archive = artifacts / "OurLittleDays-Windows-x64-Companion-0.4.7-preview.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                output.write(path, "OurLittleDays/" + path.relative_to(folder).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Windows preview ready: {archive}")
    print("Windows manual acceptance: NOT RUN")


if __name__ == "__main__":
    main()
