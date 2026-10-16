# -*- coding: utf-8 -*-
"""
Helper script to package the PyInstaller build deliverables into distribution ZIP files.
"""
import os
import pathlib
import shutil
import zipfile

def create_zips():
    base = pathlib.Path(__file__).parent.resolve()
    dist_dir = base / "dist"
    
    standalone_exe = dist_dir / "SEO Audit Pro (Standalone).exe"
    standalone_zip = dist_dir / "SEO Audit Pro-Standalone.zip"
    
    if standalone_exe.exists():
        print(f"[*] Packaging standalone ZIP: {standalone_zip.name}")
        if standalone_zip.exists():
            standalone_zip.unlink()
        with zipfile.ZipFile(standalone_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(standalone_exe, "SEO Audit Pro.exe")
            print(f"    Added {standalone_exe.name} -> SEO Audit Pro.exe ({standalone_exe.stat().st_size:,} bytes)")
    else:
        print(f"[!] Warning: {standalone_exe} not found!")

    portable_dir = dist_dir / "SEO Audit Pro"
    portable_zip = dist_dir / "SEO Audit Pro-Portable.zip"
    
    if portable_dir.exists() and portable_dir.is_dir():
        print(f"[*] Packaging portable folder ZIP: {portable_zip.name}")
        if portable_zip.exists():
            portable_zip.unlink()
        file_count = 0
        with zipfile.ZipFile(portable_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(portable_dir)):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, str(dist_dir))
                    zf.write(full, rel)
                    file_count += 1
        print(f"    Packed {file_count} files into {portable_zip.name} ({portable_zip.stat().st_size:,} bytes)")
    else:
        print(f"[!] Warning: {portable_dir} not found!")

    pkg = base.parent / "packaging" / "dist" / "SEO Audit Pro"
    if pkg.exists():
        print(f"[*] Copying outputs to packaging destination: {pkg}")
        if standalone_zip.exists():
            shutil.copy2(standalone_zip, pkg / "SEO Audit Pro.zip")
            shutil.copy2(standalone_zip, pkg / "SEO Audit Pro-Standalone.zip")
        if portable_zip.exists():
            shutil.copy2(portable_zip, pkg / "SEO Audit Pro-Portable.zip")
        if standalone_exe.exists():
            shutil.copy2(standalone_exe, pkg / "SEO Audit Pro (Standalone).exe")

    print("[+] Packaging completed successfully.")

if __name__ == "__main__":
    create_zips()
