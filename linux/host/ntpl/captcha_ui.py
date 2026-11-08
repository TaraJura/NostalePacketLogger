"""GTK4 dialog for Gameforge's image-drop captcha.

Shows the instruction image, the icon strip, the target scene and a number
box; the user reads the instruction, finds the matching icon (1 = leftmost)
and submits. The caller posts the number and retries the login.

Falls back to the terminal prompt in the headless CLI when GTK or a display
is unavailable.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk


def ask_captcha(paths: dict, title: str = "Gameforge captcha") -> int | None:
    """Return the 1-based icon number, or None when the user cancels."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("no display for the captcha window")
    result: dict[str, int | None] = {"value": None}
    app = Gtk.Application(
        application_id="cz.tarajura.ntpl.captcha",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def activate(application: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=application, title=title)
        window.set_default_size(720, 760)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        window.set_child(box)

        hint = Gtk.Label(
            label=(
                "Read the instruction image, find the matching icon in the "
                "strip, then enter its number (1 = leftmost)."
            )
        )
        hint.set_wrap(True)
        hint.set_xalign(0)
        box.append(hint)

        for key, heading, width, height in (
            ("text", "Instruction", 660, 38),
            ("icons", "Icons", 480, 120),
            ("target", "Target", 400, 400),
        ):
            path = paths.get(key)
            if path is None:
                continue
            label = Gtk.Label(label=heading)
            label.set_xalign(0)
            box.append(label)
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_size_request(width, height)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            box.append(picture)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Icon number:"))
        spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        spin.set_value(1)
        row.append(spin)
        submit = Gtk.Button(label="Submit")
        cancel = Gtk.Button(label="Cancel")
        row.append(submit)
        row.append(cancel)
        box.append(row)

        def on_submit(_button: Gtk.Button) -> None:
            result["value"] = int(spin.get_value())
            window.close()

        submit.connect("clicked", on_submit)
        cancel.connect("clicked", lambda _button: window.close())
        window.present()

    app.connect("activate", activate)
    app.run([])
    return result["value"]
