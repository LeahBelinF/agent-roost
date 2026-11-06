"""Installing and updating roost, Textual, and the plugin; roost setup."""
import io, json, os, shlex, shutil, subprocess, sys, tarfile, time
from pathlib import Path

from roost.core.config import DEFAULT_PROJECTS, HOME, REPO, TEXTUAL, version_tuple
from roost.core.plugin import install_plugin, setup_plugin
from roost.core.formatting import short
from roost.core.terminals import TERMINALS, app_installed
from roost.core import config
from roost.core import wren
from roost.core import plugin
from roost.core import github
from roost.core import terminals


# ---------- updates ----------



def update_available():
    """Newer release version if there is one, checked at most once a day; '' otherwise or when offline."""
    try:
        cached = json.loads(config.UPDATE_CHECK.read_text())
    except Exception:
        cached = {}
    latest = cached.get("latest", "")
    if time.time() - cached.get("checked", 0) > 86400:
        try:
            latest = github.latest_release()
            config.UPDATE_CHECK.parent.mkdir(parents=True, exist_ok=True)
            config.UPDATE_CHECK.write_text(json.dumps({"checked": time.time(), "latest": latest}))
        except Exception as e:
            config.log(f"update check: {e}")
    return latest if latest and version_tuple(latest) > version_tuple(config.VERSION) else ""


def extract_app(tarball, dest):
    """Unpack a GitHub release tarball's roost/ package and bin/roost launcher into dest. Only plain
    files and folders inside those are written; anything else in the archive is skipped."""
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        found = set()
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if len(parts) < 2 or ".." in parts or m.name.startswith("/") or "__pycache__" in parts:
                continue
            if parts[1] != "roost" and parts[1:] != ("bin", "roost"):
                continue
            target = dest.joinpath(*parts[1:])
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(m).read())
                found.add(parts[1])
        if found != {"roost", "bin"}:
            raise RuntimeError("that release has no roost/ package and bin/roost launcher")
    (dest / "bin" / "roost").chmod(0o755)


def swap_app(folder):
    """Point ~/.roost/app/current at folder with one rename, then drop versions that are neither the
    new one nor the one it replaced."""
    current = config.APP / "current"
    old = Path(os.path.realpath(current)) if current.is_symlink() else None
    tmp = config.APP / ".current-link"
    tmp.unlink(missing_ok=True)
    os.symlink(folder.name, tmp)
    os.replace(tmp, current)
    keep = {Path(os.path.realpath(folder)), old}
    for d in config.APP.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and Path(os.path.realpath(d)) not in keep:
            shutil.rmtree(d, ignore_errors=True)


def self_update(me=None):
    """Install the latest release: its program files in ~/.roost/app, then its launcher at me (the
    roost command). Returns a process exit code."""
    if config.BREW:  # Homebrew replaces the program; this brings the plugin up to what it brought
        print("roost came from Homebrew: brew upgrade roost installs a new release.")
        return update_plugin(config.VERSION)
    me = Path(me or os.path.abspath(sys.argv[0]))
    if me.is_symlink():
        print(f"{me} links to a development checkout ({me.resolve().parent.parent}); update it with git instead.")
        return 1
    try:
        latest = github.latest_release()
    except Exception as e:
        print(f"Couldn't check for updates: {e}\nroost reads its releases from github.com/{REPO}; check your connection.")
        return 1
    if version_tuple(latest) <= version_tuple(config.VERSION):
        print(f"roost {config.VERSION} is the latest version.")
        return update_textual(me) | update_plugin(config.VERSION)
    folder, tmp = config.APP / latest, config.APP / f".{latest}.download"
    swapped = False  # once ~/.roost/app/current is the new version, even the old launcher runs it
    try:
        config.APP.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        extract_app(github.download_tarball(latest), tmp)
        out = subprocess.run([sys.executable, str(tmp / "bin" / "roost"), "--version"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=30).stdout
        if latest not in out:
            raise RuntimeError(f"the download didn't report version {latest}")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp, folder)
        swap_app(folder)
        swapped = True
        shutil.copy2(me, me.with_name("roost.previous"))
        launcher = me.with_name(".roost-update")
        shutil.copy2(folder / "bin" / "roost", launcher)
        os.replace(launcher, me)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if not swapped:
            print(f"Update failed, nothing changed: {e}")
            return 1
        print(f"Update stopped partway: roost runs {latest} from {short(str(folder))}, but the launcher at {me} "
              f"wasn't replaced ({e}). Run roost update again to finish.")
        return update_textual(me) | update_plugin(latest) | 1
    try:
        config.UPDATE_CHECK.unlink()
    except OSError:
        pass
    print(f"Updated roost {config.VERSION} -> {latest}. The old launcher is saved as {me.with_name('roost.previous')}.")
    return update_textual(me) | update_plugin(latest)


def update_textual(me):
    """roost update's Textual step, run by the installed file, which knows the Textual it wants."""
    return subprocess.run([sys.executable, str(me), "setup", "textual"], stdin=subprocess.DEVNULL).returncode


def install_textual():
    """Make ~/.roost/venv with this Python (the system one) and bring Textual in it to TEXTUAL.
    Returns what it did; raises with pip's or venv's message when that fails."""
    py, want = config.VENV / "bin" / "python", TEXTUAL.split("==")[1]
    run = lambda argv, secs: subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=secs)
    if not py.exists():
        r = run([sys.executable, "-m", "venv", str(config.VENV)], 120)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "python3 -m venv failed")
    have = run([str(py), "-c", "import textual; print(textual.__version__)"], 60).stdout.strip()
    if have == want:
        return f"Textual {want} is in {short(str(config.VENV))}"
    r = run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", TEXTUAL], 900)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "pip install failed")
    return f"Installed Textual {want} in {short(str(config.VENV))}" + (f" (was {have})" if have else "")


def setup_textual():
    """roost setup textual: the screens' one dependency, in its own environment."""
    if config.BREW:
        print("  \033[32m✓\033[0m Textual came with Homebrew's roost")
        return 0
    print("Installing Textual, which draws roost's screens, in ~/.roost/venv…", flush=True)
    try:
        print(f"  \033[32m✓\033[0m {install_textual()}")
        return 0
    except Exception as e:
        print(f"  \033[33m!\033[0m Couldn't install Textual: {e}\n    roost --list still works; rerun roost setup textual to try again.")
        return 1


def update_plugin(version):
    """roost update's plugin step: the folder swap, then Claude Code's own copy through `claude plugin`.
    Claude Code's settings change only in roost setup."""
    try:
        print(install_plugin(version) + ".")
    except Exception as e:
        print(f"Plugin not updated: {e}.")
        return 1
    if not plugin.plugin_registered():
        print("Claude Code doesn't have the roost plugin enabled yet: run roost setup plugin.")
        return 0
    note = plugin.update_claude_copy(version)
    if note:
        print(note + ".")
    return 0


HERDR_MOD = Path(__file__).resolve().parents[1] / "herdr" / "roost-links"


def herdr_app_home():
    return Path(os.environ.get("HERDR_APP_HOME") or Path.home() / ".config" / "herdr-app")


BAR = {"id": "roost-links", "type": "block", "content": {"mod": "roost-links"}, "size": {"px": 28}}


def has_bar(node):
    return node.get("id") == BAR["id"] or any(has_bar(c) for c in node.get("children") or [])


def add_bar(layout):
    """Put the roost-links bar at the top of a layout tree: over the work column when the window is a
    sidebar and a work column side by side, else at the top of a column root. False when neither fits."""
    if has_bar(layout):
        return True
    kids = layout.get("children") or []
    if layout.get("type") == "row" and kids and kids[-1].get("type") == "column":
        kids[-1].setdefault("children", []).insert(0, dict(BAR))
    elif layout.get("type") == "column":
        layout.setdefault("children", []).insert(0, dict(BAR))
    else:
        return False
    return True


def shipped_layouts(home):
    """The layouts Herdr ships (and rewrites on every start), so a bar added to them wouldn't stay."""
    try:
        return {k.split(":", 1)[1] for k in json.loads((home / "installed.json").read_text()) if k.startswith("layout:")}
    except (OSError, ValueError, AttributeError):
        return set()


def agreed(question):
    """Ask a yes/no question; only y or yes (any case) agrees, and no answer (EOF) means no."""
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def by_hand(why):
    return True, f"{why}. To add the bar yourself, put this block at the top of a layout's work column: {json.dumps(BAR)}"


def herdr_layout(home):
    """Add the bar to the layout the Herdr app shows, after asking. A shipped layout is copied as
    <name>-roost first (Herdr's own way to change one, since it rewrites its own) and the window switches
    to the copy. Returns (ok, what it did), for setup to print."""
    status = terminals.herdr_app("status")
    name = (status or {}).get("layout")
    if not name:
        return False, "Herdr isn't running, so no layout has the bar yet: rerun roost setup herdr with the app open"
    if name in shipped_layouts(home):
        mine = f"{name}-roost"
        if (home / "layouts" / f"{mine}.json").exists():  # copied on an earlier run
            if not agreed(f"Switch the Herdr window to your {mine} layout, which has the PR and ticket bar? [y/N] "):
                return by_hand(f"Left the {name} layout as it is")
            made = terminals.herdr_app("do", "layout.switch", json.dumps({"name": mine}))
        else:
            if not agreed(f"Add the PR and ticket bar? Herdr rewrites its own {name} layout, so it will be copied as "
                          f"{mine} and the window switched to the copy. [y/N] "):
                return by_hand(f"Left the {name} layout as it is")
            made = terminals.herdr_app("do", "layout.clone", json.dumps({"name": name, "as": mine}))  # shows it too
        if made is None:
            return False, f"Couldn't switch Herdr to {mine}: see ~/.cache/roost.log"
        name, asked = mine, True
    else:
        asked = False
    path = home / "layouts" / f"{name}.json"
    try:
        layout = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return False, f"Couldn't read the {name} layout: {e}"
    if has_bar(layout):
        return True, f"The {name} layout has the bar"
    if not asked and not agreed(f"Add the PR and ticket bar to your {name} layout? [y/N] "):
        return by_hand(f"Left the {name} layout as it is")
    if not add_bar(layout):
        return False, f"The {name} layout has no top to put the bar on: add a block with mod roost-links yourself"
    path.write_text(json.dumps(layout, indent=2) + "\n")
    return True, f"Added the bar to the {name} layout" + (" (a copy of Herdr's, now shown)" if name.endswith("-roost") else "")


def setup_herdr():
    """roost setup herdr: copy the roost-links mod (a Session's PRs and tickets) into the Herdr app's mods,
    and put its bar in the layout the app shows. Herdr reloads mods and layouts when their files change,
    so rerunning it updates both in place."""
    home = herdr_app_home()
    if not home.is_dir():
        print(f"  \033[33m!\033[0m No Herdr app folder at {home}: open the Herdr app once, then rerun roost setup herdr.")
        return 1
    dest = home / "mods" / "roost-links"
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(HERDR_MOD.iterdir()):
        if f.is_file() and f.name != "jsconfig.json" and not f.name.endswith(".d.ts"):  # Herdr writes those
            shutil.copyfile(f, dest / f.name)
    print(f"  \033[32m✓\033[0m The roost-links mod is in {dest}")
    ok, said = herdr_layout(home)
    print(f"  \033[{'32m✓' if ok else '33m!'}\033[0m {said}")
    return 0 if ok else 1


def setup(only=None):
    """Interactive first-run setup: checks prerequisites, picks a terminal and the Projects folder,
    and installs the plugin. only="plugin" does just the plugin step."""
    if only == "plugin":
        print("\n\033[1mroost plugin\033[0m\n")
        return setup_plugin()
    if only == "textual":
        return setup_textual()
    if only == "herdr":
        return setup_herdr()
    ok = lambda m: print(f"  \033[32m✓\033[0m {m}")
    bad = lambda m: print(f"  \033[33m!\033[0m {m}")
    print()
    wren.print_wren()
    print("\033[1mAgent Roost setup\033[0m\n")

    claude = shutil.which("claude")
    if not claude:
        bad("Claude Code (claude) isn't on your PATH. Install it first: https://code.claude.com")
        return 1
    ver = subprocess.run(["claude", "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout.strip()
    ok(f"Claude Code {ver}")
    try:
        json.loads(subprocess.run(["claude", "agents", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15).stdout)
        ok("claude agents --json works")
    except Exception:
        bad("claude agents --json didn't return data. Update Claude Code (claude update) and rerun setup.")
        return 1
    if setup_textual():
        return 1
    here = str(Path(sys.argv[0]).resolve().parent)
    if shutil.which("roost") is None:
        bad(f"roost isn't on your PATH. Add this to ~/.zshrc:  export PATH=\"{here}:$PATH\"")
    else:
        ok(f"roost is on your PATH ({shutil.which('roost')})")

    installed = [k for k, v in TERMINALS.items() if app_installed(v["app"])]
    detected = terminals.detect_terminal()
    print("\nWhen you press enter on a session, where should it open?\n")
    options = installed + ["same"]
    for i, k in enumerate(options, 1):
        label = "This terminal (replaces roost, no new windows)" if k == "same" else f"New {TERMINALS[k]['label']} window"
        print(f"  {i}. {label}{'  (detected)' if k == detected else ''}")
    default = options.index(detected) + 1 if detected in options else 1
    try:
        raw = input(f"\nChoose 1-{len(options)} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return 1
    choice = options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else options[default - 1]

    if choice != "same":
        print(f"\nOpening a test {TERMINALS[choice]['label']} window. If macOS asks to let your terminal")
        print(f"control {TERMINALS[choice]['app']}, click Allow.")
        line = "echo 'Agent Roost can open windows here. Closing…'; sleep 2"
        res = terminals.osa(TERMINALS[choice]["new"], HOME, line + "; exit", os.environ.get("SHELL", "/bin/zsh") + " -lc " + shlex.quote(line))
        if res is None:
            bad("The test window didn't open. Allow it in System Settings → Privacy & Security → Automation, then rerun roost setup.")
            return 1
        ok("Test window opened")

    current = config.read_config().get("projects_dir") or DEFAULT_PROJECTS
    print("\nWhere do your projects live? One folder per project; roost never moves what's there.")
    try:
        raw = input(f"Projects folder [{current}]: ").strip() or current
    except (EOFError, KeyboardInterrupt):
        print(); return 1
    folder = Path(os.path.expanduser(raw))
    if not folder.is_dir():
        try:
            make = input(f"{short(str(folder))} doesn't exist. Create it? [Y/n]: ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            print(); return 1
        if make:
            folder.mkdir(parents=True, exist_ok=True)
            ok(f"Created {short(str(folder))}")
        else:
            bad(f"{short(str(folder))} doesn't exist yet; the Projects tab will say so until it does")
    else:
        ok(f"Projects folder {short(str(folder))}")

    config.save_config(terminal=choice, projects_dir=short(str(folder)))
    ok(f"Saved to {short(str(config.CONFIG))}")

    print("\nInstalling roost's Claude Code plugin (its skills, like /roost:pickup and /roost:wrap-up).")
    if setup_plugin():
        print("\nThe rest is set up. Fix the plugin, then run \033[1mroost setup plugin\033[0m.\n")
        return 1
    print("\nDone. Run \033[1mroost\033[0m to open your projects and sessions.\n")
    return 0
