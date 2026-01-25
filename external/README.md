# Minimal IDE Launcher (GTK)

Single-folder minimal app: file explorer + embedded terminal + browser + split mode.

## Dependencies (Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-webkit2-4.1
```

Optional (for "Open in Files" and file opening):

```bash
sudo apt-get install -y nautilus
```

## Run

```bash
./app.py
```

Optional: pass a root path:

```bash
./app.py /path/to/project
```

## Desktop entry

Edit the `Exec=` path if you move the folder, then install:

```bash
mkdir -p ~/.local/share/applications
cp minimal-ide-launcher.desktop ~/.local/share/applications/
```

## Notes
- Left nav uses system app icons (terminal, file manager, browser).
- Right-click in the file tree for “Open terminal here”.
- Double-click a file to open with `xdg-open`.
- Remembers window size and last root in `~/.config/minimal-ide-launcher/config.json`.
- If no display is available, it prints a hint about `xvfb-run`/X11 forwarding.
