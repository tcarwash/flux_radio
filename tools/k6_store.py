#!/usr/bin/env python3
"""Browse the Flux module catalog and load compatible bundles over the cable."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).with_name("k6_store_templates")
FLUX_ART_DIR = Path(os.environ.get("K6_FLUX_ART_DIR", ROOT / "flux-art-kit/flux-arrays"))
THEME_LOGOS = FLUX_ART_DIR / "logos.py"
THEME_BOOT_SPLASH = FLUX_ART_DIR / "splash.bin"
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)


def theme_splash_preview_logos() -> list[list[int]]:
    """Load the same artwork arrays used by the firmware splash build."""
    names = ("FACTORY_LOGO", "DIAMOND_LOGO", "ANTENNA_LOGO")
    values = {}
    for statement in ast.parse(THEME_LOGOS.read_text()).body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id in names:
                values[target.id] = ast.literal_eval(statement.value)
    logos = [values.get(name) for name in names]
    if any(not isinstance(rows, list) or len(rows) != 16
           or any(type(value) is not int or not 0 <= value <= 0xFFFF for value in rows)
           for rows in logos):
        raise ValueError(f"invalid theme preview logo artwork in {THEME_LOGOS}")
    return logos


def theme_boot_splash_bytes() -> list[int]:
    """The page-major boot bitmap used by the radio's stored-logo renderer."""
    data = THEME_BOOT_SPLASH.read_bytes()
    if len(data) != 1024:
        raise ValueError(f"invalid 128 by 64 boot artwork in {THEME_BOOT_SPLASH}")
    return list(data)


def default_config() -> Path:
    override = os.environ.get("FLUX_STORE_CONFIG") or os.environ.get("BLOWFANG_STORE_CONFIG")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    legacy = base / "blowfang" / "module-store.json"
    return legacy if legacy.exists() else base / "flux" / "module-store.json"


def default_cache() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    legacy = base / "blowfang" / "module-store" / "repos"
    return legacy if legacy.exists() else base / "flux" / "module-store" / "repos"


def slug_for(source: str) -> str:
    parsed = urlparse(source)
    raw = Path(parsed.path).name if parsed.scheme else Path(source).name
    raw = re.sub(r"\.git$", "", raw, flags=re.I)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-_").lower() or "module"
    digest = hashlib.sha256(source.encode()).hexdigest()[:8]
    return f"{slug[:36]}-{digest}"


def repo_url(source: str, commit: str) -> str | None:
    if source.startswith(("https://", "http://")):
        base = source.rstrip("/")
        if base.endswith(".git"):
            base = base[:-4]
        return f"{base}/tree/{commit}"
    return None


def readme_path(path: Path) -> Path | None:
    for name in ("README.md", "Readme.md", "readme.md", "README.rst", "README.txt"):
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def readme_info(path: Path) -> dict:
    file = readme_path(path)
    if not file:
        return {"title": path.name, "summary": "No README found.", "readme": "", "screenshots": []}
    text = file.read_text(errors="replace")
    title = next((m.group(1).strip() for line in text.splitlines()
                  if (m := re.match(r"^#\s+(.+?)\s*#*\s*$", line))), path.name)
    paragraphs = re.split(r"\n\s*\n", text)
    summary = ""
    for paragraph in paragraphs:
        candidate = paragraph.strip()
        if not candidate or candidate.startswith(("#", "![", "<", "[![", "```")):
            continue
        candidate = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", candidate)
        candidate = re.sub(r"[*_`>#]", "", candidate)
        candidate = " ".join(candidate.split())
        if candidate:
            summary = candidate[:340]
            break
    images = []
    for alt, src in IMAGE_RE.findall(text):
        images.append({"alt": alt or title, "src": src})
    for src in HTML_IMAGE_RE.findall(text):
        if not any(image["src"] == src for image in images):
            images.append({"alt": title, "src": src})
    return {"title": title, "summary": summary or "No short description in README.",
            "readme": text, "readme_file": file.name, "screenshots": images[:8]}


def find_bundle(path: Path) -> Path | None:
    candidates = [path / "manifest.json", path / "dist" / "manifest.json"]
    candidates.extend(sorted(path.glob("*/manifest.json")))
    for manifest_path in candidates:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (isinstance(manifest, dict) and manifest.get("format") == 1 and isinstance(manifest.get("build_id"), str)
                and isinstance(manifest.get("library"), list)
                and isinstance(manifest.get("sha256"), dict)):
            return manifest_path.parent
    return None


def bundle_info(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    modules = [{"name": item.get("name", item.get("file", "Module")),
                "slot": item.get("slot"), "kind": item.get("kind", "module"),
                "level": item.get("level")} for item in manifest.get("library", [])]
    build_id = manifest.get("build_id", "")
    return {"path": path, "build": manifest.get("build", ""),
            "build_id": build_id if build_id and set(build_id) != {"0"} else None,
            "modules": modules}


STATUS_LABELS = {"tested": "Hardware tested", "targeted": "Flux targeted", "experimental": "Experimental", "wip": "WIP / port required", "blocked": "Blocked", "unreviewed": "Unreviewed"}
PACK_CAPS = {"scan": "scanner", "data": "fsk_rx", "designer": "designer", "sotto": "sotto", "ota": "ota"}


def local_compatibility(info, bundle):
    """Curated local status is not a claim of hardware validation."""
    status = info.get("status", "unreviewed")
    reason = None
    if bundle and info.get("source_module"):
        try:
            manifest = json.loads((bundle["path"] / "manifest.json").read_text())
            entries = manifest["library"]
            if len(entries) != 1 or entries[0].get("kind", "module") != "module":
                raise ValueError("expected one native module")
            entry = entries[0]
            name = entry["file"]
            if Path(name).name != name:
                raise ValueError("unexpected module filename")
            binary = (bundle["path"] / name).read_bytes()
            digest = hashlib.sha256(binary).hexdigest()
            if manifest["sha256"].get(name) != digest:
                raise ValueError("bundle checksum mismatch")
            if len(binary) + 48 + 1040 > 11776:
                raise ValueError("module exceeds compact Flux arena budget")
            if entry.get("level") != info.get("expected_api"):
                raise ValueError("stale module API; rebuild the local bundle")
            if info.get("expected_sha256") and digest != info["expected_sha256"] and manifest.get("flux_source_module") != info["source_module"]:
                raise ValueError("legacy or unknown Groundwave build; rebuild the compact gw_flux bundle")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            status, reason = "blocked", str(exc)
    return {"status": status, "status_label": STATUS_LABELS.get(status, STATUS_LABELS["unreviewed"]),
            "blocked_reason": reason, "install_blocked": status in ("wip", "blocked"),
            "native_flux": info.get("native_flux") is True,
            "required_packs": info.get("required_packs", []),
            "required_caps": (["module_loader"] if status in ("tested", "targeted", "experimental") else []) + [PACK_CAPS[p] for p in info.get("required_packs", []) if p in PACK_CAPS]}


def catalog_install_flags(repo, acknowledge=False):
    if repo.get("install_blocked"):
        raise ValueError(repo.get("blocked_reason") or "This module is WIP and not cleared for the compact FD6818B host.")
    if repo.get("status") == "experimental" and not acknowledge:
        raise ValueError("Experimental module: acknowledge the hardware limitations before installing.")
    return [flag for cap in repo.get("required_caps", []) for flag in ("--require-capability", cap)]


class ModuleStore:
    def __init__(self, config: Path | None = None, cache: Path | None = None):
        self.config = (config or default_config()).expanduser()
        self.cache = (cache or default_cache()).expanduser()

    def configured_sources(self) -> list[dict]:
        try:
            data = json.loads(self.config.read_text())
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read catalog {self.config}: {exc}") from exc
        entries = data.get("sources", [])
        if not isinstance(entries, list):
            raise ValueError(f"{self.config}: 'sources' must be a list")
        return entries

    def disabled_defaults(self) -> list[str]:
        try:
            data = json.loads(self.config.read_text())
        except FileNotFoundError:
            return []
        return data.get("disabled_defaults", [])

    def default_sources(self) -> list[dict]:
        path = ROOT / "config" / "flux-catalog.json"
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return []
        if data.get("format") != 1 or not isinstance(data.get("sources"), list):
            raise ValueError(f"{path}: invalid default catalog format")
        entries = []
        for item in data["sources"]:
            source = item["source"]
            ref = item.get("ref", "")
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", source):
                raise ValueError(f"{path}: expected a GitHub HTTPS repository URL")
            entry = {"id": slug_for(source + "#" + ref), "source": source}
            if ref:
                entry["ref"] = ref
            entries.append(entry)
        return entries

    def local_policy(self) -> dict:
        path = ROOT / "config" / "flux-catalog.json"
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {}

    def sources(self) -> list[dict]:
        entries = self.configured_sources()
        seen = {(e["source"], e.get("ref", "")) for e in entries}
        disabled = set(self.disabled_defaults())
        for entry in self.default_sources():
            key = (entry["source"], entry.get("ref", ""))
            if key not in seen and entry["id"] not in disabled:
                entries.append(entry)
                seen.add(key)
        configured_paths = {str(Path(e['source']).expanduser().resolve())
                            for e in entries if Path(e.get('source', '')).expanduser().is_dir()}
        directory = ROOT / 'local-modules'
        if not directory.is_dir():
            return entries
        archived = set(self.local_policy().get('archived_local', []))
        for path in sorted(directory.iterdir()):
            if not path.is_dir() or path.name in archived:
                continue
            source = str(path.resolve())
            # Local module bundles are useful artifacts in their own right; they don't need
            # a synthetic .git directory just to appear in the catalog. Git repos continue
            # to work as before, provided they contain an installable bundle.
            if source in configured_paths or find_bundle(path) is None:
                continue
            entries.append({'id': slug_for(source + '#'), 'source': source})
            configured_paths.add(source)
        return entries

    def save(self, entries: list[dict], disabled: list[str] | None = None) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config.with_suffix(self.config.suffix + ".tmp")
        temp.write_text(json.dumps({"format": 1, "sources": entries,
                                    "disabled_defaults": self.disabled_defaults() if disabled is None else disabled}, indent=2) + "\n")
        temp.replace(self.config)

    def add(self, source: str, ref: str | None = None) -> dict:
        source = source.strip()
        local = Path(source).expanduser()
        if local.exists():
            source = str(local.resolve())
            if not local.is_dir():
                raise ValueError("a local source must be a module package or Git repository directory")
            if find_bundle(local) is None:
                try:
                    subprocess.run(["git", "-C", source, "rev-parse", "--is-inside-work-tree"],
                                   check=True, capture_output=True, text=True, timeout=10)
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    raise ValueError("a local source must contain an installable module package or be a Git checkout") from exc
        elif not source.startswith(("https://", "ssh://", "git@")):
            raise ValueError("source must be an HTTPS/SSH Git URL or an existing directory")
        entries = self.configured_sources()
        if any(e.get("source") == source and e.get("ref", "") == (ref or "") for e in self.sources()):
            raise ValueError("that source and ref are already in the catalog")
        entry = {"id": slug_for(source + "#" + (ref or "")), "source": source}
        if ref:
            entry["ref"] = ref
        entries.append(entry)
        self.sync(entry, refresh=True)
        self.save(entries, [i for i in self.disabled_defaults() if i != entry["id"]])
        return entry

    def remove(self, repo_id: str) -> bool:
        entries = self.configured_sources()
        kept = [e for e in entries if e.get("id") != repo_id]
        defaults = {e["id"] for e in self.default_sources()}
        disabled = self.disabled_defaults()
        is_default = repo_id in defaults and repo_id not in disabled
        if len(kept) == len(entries) and not is_default:
            return False
        if is_default:
            disabled.append(repo_id)
        self.save(kept, disabled)
        return True

    def location(self, entry: dict) -> Path:
        local = Path(entry["source"]).expanduser()
        if local.is_dir():
            return local.resolve()
        return self.cache / entry["id"]

    def sync(self, entry: dict, refresh: bool = False) -> Path:
        local = Path(entry["source"]).expanduser()
        if local.is_dir():
            return local.resolve()
        target = self.cache / entry["id"]
        if target.is_dir() and not refresh:
            return target
        self.cache.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        cmd = ["git", "clone", "--depth", "1"]
        if entry.get("ref"):
            cmd += ["--branch", entry["ref"]]
        cmd += [entry["source"], str(target)]
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git clone failed")
        return target

    def repo(self, entry: dict, refresh: bool = False) -> dict:
        path = self.sync(entry, refresh=refresh)
        info = readme_info(path)
        if path.parent == (ROOT / "local-modules").resolve():
            info.update(self.local_policy().get("local_metadata", {}).get(path.name, {}))
        try:
            if not (path / ".git").exists():
                raise FileNotFoundError("standalone bundle")
            commit = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"],
                                             text=True, stderr=subprocess.DEVNULL).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            commit = "local"
        bundle_path = find_bundle(path)
        bundle = bundle_info(bundle_path)
        return {**entry, **info, **local_compatibility(info, bundle), "path": path, "commit": commit,
                "source_link": repo_url(entry["source"], commit), "bundle": bundle}

    def catalog(self) -> list[dict]:
        repos = []
        for entry in self.sources():
            try:
                repos.append(self.repo(entry))
            except (OSError, RuntimeError, ValueError) as exc:
                repos.append({**entry, "title": entry.get("id", "Repository"),
                              "summary": f"Catalog refresh failed: {exc}",
                              "screenshots": [], "readme": "", "error": str(exc),
                              "bundle": None, "path": self.location(entry)})
        order = {"tested": 0, "targeted": 1, "experimental": 2, "unreviewed": 3, "wip": 4, "blocked": 5}
        return sorted(repos, key=lambda repo: order.get(repo.get("status"), 2))

    def by_id(self, repo_id: str, refresh: bool = False) -> dict:
        entry = next((e for e in self.sources() if e.get("id") == repo_id), None)
        if entry is None:
            raise KeyError(repo_id)
        return self.repo(entry, refresh=refresh)


def create_app(store: ModuleStore | None = None):
    try:
        from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for, jsonify
    except ImportError as exc:
        raise RuntimeError("Flask is required for the web UI; install tools/k6_store_requirements.txt") from exc

    app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=str(TEMPLATES / "static"))
    app.secret_key = os.environ.get("FLUX_STORE_SECRET") or os.environ.get("BLOWFANG_STORE_SECRET") or secrets.token_bytes(32)
    catalog = store or ModuleStore()
    from k6_build_jobs import BuildJobs
    from k6_theme_library import ThemeLibrary
    build_jobs = BuildJobs()
    theme_library = ThemeLibrary()
    app.config["build_jobs"] = build_jobs
    app.config["module_store"] = catalog
    app.config["theme_library"] = theme_library

    # Persist only the last port on the local server, not the signing key.
    # Restored preferences are a suggestion until explicitly confirmed.
    app.config["device_settings_path"] = Path(getattr(catalog, "config", default_config())).with_name("device-settings.json")

    def saved_port():
        try:
            data = json.loads(app.config["device_settings_path"].read_text())
            port = data.get("port", "")
            return port if isinstance(port, str) else ""
        except (OSError, ValueError, AttributeError):
            return ""

    def radio_settings() -> dict:
        return {"port": session.get("radio_port", ""),
                "saved_port": saved_port(),
                "key_file": session.get("radio_key_file", "")}

    def serial_devices():
        try:
            from serial.tools import list_ports
            return sorted(({"device": p.device, "description": p.description}
                           for p in list_ports.comports()), key=lambda p: p["device"])
        except (ImportError, OSError):
            return []


    def cable_command(*args: str) -> list[str]:
        settings = radio_settings()
        if not settings["port"]:
            raise ValueError("Set the USB serial port in Device settings first.")
        command = [sys.executable, str(ROOT / "tools" / "k6_install.py"), "--port", settings["port"]]
        if settings["key_file"]:
            command += ["--key-file", settings["key_file"]]
        return command + list(args)

    @app.context_processor
    def inject_radio_settings():
        return {"radio_settings": radio_settings()}

    @app.get("/memories")
    def memories_page():
        from k6_memories import builtins
        return render_template("memories.html", packs=builtins(), memory_token=session.setdefault("memory_token",secrets.token_urlsafe(32)))

    @app.post("/memories/radio")
    def memories_radio():
        from k6_memory_radio import operate
        from k6_install import InstallError
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return jsonify(error="Invalid request"),400
        token=session.get("memory_token","")
        if not token or not secrets.compare_digest(token,str(data.get("token",""))):abort(403)
        action=data.get("action")
        if action not in ("read","merge","replace","recover"):return jsonify(error="Unknown action"),400
        if action!="read" and data.get("confirm")!=action: return jsonify(error="Confirm radio write first"),400
        settings=radio_settings()
        if not settings["port"]:return jsonify(error="Select serial port in Device settings first"),400
        try:return jsonify(operate(settings["port"],settings["key_file"],action,data.get("channels")))
        except (ValueError,OSError,InstallError) as e:return jsonify(error=str(e)),400

    @app.post("/memories/import")
    def memories_import():
        from k6_memories import read_csv, read_pack
        try:
            f = request.files["file"]
            data = f.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Memory file exceeds 2 MB")
            return jsonify(channels=(read_csv if f.filename.lower().endswith(".csv") else read_pack)(data.decode("utf-8-sig")))
        except (ValueError, KeyError, UnicodeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/memories/export")
    def memories_export():
        from flask import Response
        from k6_memories import pack, write_csv
        try:
            data = request.get_json()
            rows = data["channels"]
            if not isinstance(rows, list) or len(rows) > 10000 or not all(isinstance(r, dict) for r in rows):
                raise ValueError("Invalid channel list")
            csv_format = data.get("format") == "csv"
            body = write_csv(rows) if csv_format else json.dumps(pack(data.get("name", "My memories"), rows), indent=2)
            ext = "csv" if csv_format else "fluxmem"
            return Response(body, mimetype="text/csv" if csv_format else "application/json", headers={"Content-Disposition": f'attachment; filename="memories.{ext}"'})
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/memories/repeaters")
    def memories_repeaters():
        from k6_memories import repeaters
        try:
            rows = repeaters(float(request.args["lat"]), float(request.args["lon"]), float(request.args["radius"]), request.args.get("band", "all"), request.args.get("open", "1") == "1")
            return jsonify(channels=rows)
        except (ValueError, KeyError) as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            return jsonify(error="HearHam is temporarily unavailable"), 502

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html", settings=radio_settings(), devices=serial_devices())

    @app.post("/settings")
    def save_settings():
        port = request.form.get("port", "").strip()
        key_file = request.form.get("key_file", "").strip()
        if not port:
            flash("Enter the serial device, such as /dev/ttyUSB0 or COM5.", "error")
            return render_template("settings.html", settings={"port": port, "key_file": key_file, "saved_port": saved_port()}, devices=serial_devices()), 400
        session["radio_port"] = port
        session["radio_key_file"] = key_file
        if request.form.get("remember_port") == "yes":
            import tempfile
            path = app.config["device_settings_path"]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
                    temporary = Path(out.name)
                    json.dump({"port": port}, out)
                try:
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            except OSError:
                flash("Port selected for this session, but could not save it for next time.", "error")
        elif request.form.get("remember_port") == "no":
            try:
                app.config["device_settings_path"].unlink(missing_ok=True)
            except OSError:
                flash("Could not clear the remembered port.", "error")
        flash("Radio port selected. Confirm the cable is still connected before writing.", "success")
        return redirect(url_for("settings_page"))

    @app.get("/")
    def index():
        try:
            repos = catalog.catalog()
        except ValueError as exc:
            repos = []
            flash(str(exc), "error")
        return render_template("index.html", repos=repos, catalog_themes=theme_library.list())

    def profile_plan(selected, theme, tx_profile, iq=None):
        from k6_feature_packs import DEFAULT, resolve, DEFAULT_TX_PROFILE
        from k6_pack_policy import POLICY, validate, gate_environment
        spec = json.loads(DEFAULT.read_text())
        policy = json.loads(POLICY.read_text())
        choices = {name: dict(item) for name, item in spec['packs'].items() if name != 'base'}
        if 'sotto' in choices:
            choices['sotto']['status'] = 'WIP — not ready for installation'
        try:
            if any(name not in choices or choices[name].get('status') for name in selected):
                raise ValueError('unknown feature pack')
            validate(spec, policy)
            plan = resolve(spec, selected or ['base'], theme, tx_profile)
            from k6_flash_estimate import estimate
            from k6_pack_policy import storage_environment, iq_supported
            if iq is None: iq=iq_supported(plan)
            plan['iq']=iq
            result = {'ok': True, 'plan': plan, 'gates': {**gate_environment(plan, policy),**storage_environment(plan,iq=iq)}, 'estimate': estimate(plan)}
        except (ValueError, KeyError) as exc:
            result = {'ok': False, 'error': str(exc)}
        return choices, result

    @app.get("/profiles")
    @app.post("/profiles")
    def profiles():
        from k6_feature_packs import THEMES, DEFAULT_THEME, TX_PROFILES, DEFAULT_TX_PROFILE
        selected = request.form.getlist('pack')
        theme = request.form.get('theme', DEFAULT_THEME)
        theme_name = request.form.get('theme_name', '')
        tx_profile = request.form.get('tx_profile', DEFAULT_TX_PROFILE)
        choices, result = profile_plan(selected, theme, tx_profile, (request.form.get('iq')=='1' if 'iq' in request.form else None))
        saved_themes = theme_library.list()
        matching_theme_index = next((i for i, item in enumerate(saved_themes)
                                     if item['token'] == theme and (not theme_name or item['name'] == theme_name)), -1)
        return render_template('profiles.html', choices=choices, result=result,
                               selected=selected, themes=THEMES, selected_theme=theme, selected_theme_name=theme_name,
                               saved_themes=saved_themes, matching_theme_index=matching_theme_index,
                               tx_profiles=TX_PROFILES, selected_tx_profile=tx_profile, selected_iq=result.get('plan',{}).get('iq',False)), (200 if result['ok'] else 400)

    @app.get("/themes")
    def themes_page():
        from k6_feature_packs import THEMES, THEME_EDITOR_PRESETS
        return render_template('themes.html', themes=THEMES, theme_editor_presets=THEME_EDITOR_PRESETS,
                               theme_splash_logos=theme_splash_preview_logos(),
                               theme_boot_splash=theme_boot_splash_bytes())

    # Preserve the existing form endpoint name for templates and callers.
    app.add_url_rule('/profiles', endpoint='plan_profile', view_func=profiles, methods=['POST'])

    @app.post("/themes/export")
    def export_theme():
        from flask import Response
        from k6_theme_package import pack, unpack
        try:
            token = request.form.get("theme", "")
            blob = pack(token, request.form.get('name', ''))
            unpack(blob)
            return Response(blob, mimetype="application/octet-stream",
                            headers={"Content-Disposition": "attachment; filename=flux-theme.fluxtheme"})
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/themes/import")
    def import_theme():
        from k6_theme_package import unpack, migrate
        try:
            file = request.files.get("theme")
            if file is None: raise ValueError("choose a theme file")
            token, name = unpack(file.stream.read(8193), include_name=True)
            return jsonify(theme=migrate(token), name=name)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/themes/library")
    def list_saved_themes():
        try:
            return jsonify(themes=theme_library.list(), path=str(theme_library.path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify(error=str(exc)), 500

    @app.post("/themes/library")
    def save_theme_to_disk():
        try:
            item = theme_library.save(request.form.get("name", ""), request.form.get("theme", ""),
                                      request.form.get("preview", ""))
            return jsonify(theme=item, path=str(theme_library.path))
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        except OSError as exc:
            return jsonify(error=str(exc)), 500

    @app.delete("/themes/library")
    def delete_saved_theme():
        try:
            if not theme_library.delete(request.form.get("name", "")):
                return jsonify(error="saved theme was not found"), 404
            return jsonify(ok=True, path=str(theme_library.path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify(error=str(exc)), 500

    @app.post("/profiles/preview")
    def preview_profile():
        from k6_feature_packs import DEFAULT_THEME, DEFAULT_TX_PROFILE
        _, result = profile_plan(request.form.getlist('pack'), request.form.get('theme', DEFAULT_THEME),
                                 request.form.get('tx_profile', DEFAULT_TX_PROFILE),(request.form.get('iq')=='1' if 'iq' in request.form else None))
        return jsonify(result), (200 if result['ok'] else 400)

    @app.post("/profiles/build")
    def build_profile():
        if os.name == "nt":
            flash("Firmware builds require Linux or WSL. Use a prebuilt starter on Windows.", "error")
            return redirect(url_for("profiles"))
        from k6_feature_packs import DEFAULT_THEME, DEFAULT_TX_PROFILE
        selected = request.form.getlist('pack')
        theme = request.form.get('theme', DEFAULT_THEME)
        theme_name = request.form.get('theme_name', '').strip()
        tx_profile = request.form.get('tx_profile', DEFAULT_TX_PROFILE)
        _, result = profile_plan(selected, theme, tx_profile, (request.form.get('iq')=='1' if 'iq' in request.form else None))
        if not result['ok']:
            abort(400)
        try:
            job_id = app.config['build_jobs'].start(selected, theme, tx_profile, theme_name, iq=result['plan']['iq'])
        except (ValueError, OSError) as exc:
            flash(str(exc), 'error')
            return redirect(url_for('profiles'))
        return redirect(url_for('build_status', job_id=job_id))

    def pending_recovery(job, port):
        if not port or not job.get('lock') or job['lock'].get('storage',{}).get('format')!=2:return []
        from k6_user_store_restore import pending_for_pack,BACKUP_DIR
        from k6_build_identity import image_build_id
        try:
            build=image_build_id((Path(job['output'])/job['lock']['firmware']['path']).read_bytes())
            return pending_for_pack(BACKUP_DIR,build,port)
        except (OSError,ValueError,KeyError):return []

    @app.get("/profiles/build/<job_id>")
    def build_status(job_id):
        try:
            job = app.config['build_jobs'].get(job_id)
        except KeyError:
            abort(404)
        token = session.setdefault('pack_radio_token', secrets.token_urlsafe(32))
        return render_template('build_status.html', job=job, radio_token=token, recoveries=pending_recovery(job,radio_settings()['port']))

    @app.post("/profiles/build/<job_id>/radio")
    def provision_profile(job_id):
        token = session.get('pack_radio_token')
        if not token or not secrets.compare_digest(token, request.form.get('token', '')):
            abort(403)
        action = request.form.get('action')
        if action not in ('flash', 'install', 'preview', 'theme', 'splash', 'flash-install'):
            abort(400)
        if action != 'preview' and request.form.get('confirm') != 'yes':
            flash('Confirm the write to the selected radio first.', 'error')
            return redirect(url_for('build_status', job_id=job_id))
        settings = radio_settings()
        if not settings['port']:
            flash('Set the USB serial port in Device settings first.', 'error')
            return redirect(url_for('settings_page'))
        try:
            recovery=None
            if request.form.get('recovery'):
                job=app.config['build_jobs'].get(job_id)
                candidates=pending_recovery(job,settings['port'])
                recovery=next((p for p in candidates if p.name==request.form['recovery']),None)
                if recovery is None or action!='flash-install':raise ValueError('No matching pending recovery for this build and serial port.')
            radio_id = app.config['build_jobs'].start_radio(
                job_id, action, settings['port'], settings['key_file'],
                already_update=action in ('flash', 'flash-install') and request.form.get('mode') == 'update',
                theme_slot=None,recovery=recovery)
        except KeyError:
            abort(404)
        except (OSError, ValueError) as exc:
            flash(str(exc), 'error')
            return redirect(url_for('build_status', job_id=job_id))
        return redirect(url_for('profile_radio_status', job_id=job_id, radio_id=radio_id))

    @app.get("/profiles/build/<job_id>/radio/<radio_id>")
    def profile_radio_status(job_id, radio_id):
        try:
            operation = app.config['build_jobs'].get_radio(job_id, radio_id)
        except KeyError:
            abort(404)
        return render_template('pack_radio_status.html', job_id=job_id, operation=operation)

    @app.get("/radio")
    def radio():
        return render_template("radio.html", inventory=None, action_result=None)

    @app.post("/radio")
    def inspect_radio():
        try:
            command = cable_command("status")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings_page"))
        settings = radio_settings()
        port, key_file = settings["port"], settings["key_file"]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + "\nRadio status timed out after 60 seconds."
            return render_template("radio.html", inventory={"ok": False, "output": output,
                                     "slots": [], "protocol": 0}, port=port, key_file=key_file,
                                   action_result=None), 504
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        protocol_match = re.search(r"cable session (\d+)", result.stdout)
        protocol = int(protocol_match.group(1)) if protocol_match else 0
        slots = []
        pattern = re.compile(r"^slot\s+(\d+)\s+(page|cards|module|theme)\s+(.+?)\s+(\d+)\s+B code, serial (\d+)$")
        for line in result.stdout.splitlines():
            match = pattern.search(line.strip())
            if not match:
                continue
            slot, kind, name, size, serial = match.groups()
            slots.append({"slot": int(slot), "kind": kind, "name": name.strip(),
                          "size": int(size), "serial": int(serial)})
        repos = catalog.catalog() if result.returncode == 0 else []
        for slot in slots:
            slot["source"] = None
            if slot["kind"] != "module":
                continue
            for repo in repos:
                if not repo.get("bundle"):
                    continue
                if any(m["name"].casefold() == slot["name"].casefold()
                       for m in repo["bundle"]["modules"]):
                    slot["source"] = {"id": repo["id"], "title": repo["title"]}
                    break
        return render_template("radio.html", inventory={"ok": result.returncode == 0,
                               "output": output, "slots": slots, "protocol": protocol}, port=port,
                               key_file=key_file, action_result=None)

    @app.post("/radio/remove-theme")
    def erase_radio_theme():
        try:
            slot = int(request.form.get("slot", ""))
            if not (0 <= slot <= 8 or 16 <= slot <= 31):
                abort(400)
            command = cable_command("remove-theme", str(slot), "--yes")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("radio"))
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            return render_template("radio.html", inventory=None, action_result={
                "ok": False, "output": "Theme removal timed out; inspect the radio before retrying."}), 504
        return render_template("radio.html", inventory=None, action_result={
            "ok": result.returncode == 0, "output": result.stdout + "\n" + result.stderr}), (200 if result.returncode == 0 else 500)

    @app.post("/themes/catalog/add")
    def add_catalog_theme():
        from k6_theme_package import unpack
        try:
            file = request.files.get("theme")
            if file is None:
                raise ValueError("Choose a .fluxtheme package")
            token, name = unpack(file.stream.read(8193), include_name=True)
            theme_library.save(request.form.get("name", "").strip() or name or "Imported theme", token)
            flash("Added theme to the catalog.", "success")
        except (ValueError, OSError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("index") + "#themes")

    @app.post("/themes/catalog/install")
    def install_catalog_theme():
        import tempfile
        from k6_theme_package import pack
        name = request.form.get("name", "")
        item = next((item for item in theme_library.list() if item["name"] == name), None)
        if item is None:
            abort(404)
        try:
            with tempfile.NamedTemporaryFile(suffix=".fluxtheme") as file:
                file.write(pack(item["token"], name))
                file.flush()
                command = cable_command("install-theme", file.name, "--yes")
                result = subprocess.run(command, text=True, capture_output=True, timeout=300)
        except (ValueError, OSError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("index") + "#themes")
        except subprocess.TimeoutExpired:
            flash("Theme install timed out; inspect My Radio before retrying.", "error")
            return redirect(url_for("radio"))
        return render_template("radio.html", inventory=None, action_result={
            "ok": result.returncode == 0, "output": result.stdout + "\n" + result.stderr}), (200 if result.returncode == 0 else 500)

    @app.post("/radio/remove")
    def erase_radio_slot():
        action = request.form.get("action", "single")
        if action not in ("single", "selected", "all"):
            abort(400)
        if action == "all":
            args = ("remove-slots", "--all-modules", "--yes")
        else:
            raw_slots = request.form.getlist("slot")
            if action == "single" and len(raw_slots) != 1:
                abort(400)
            if action == "selected" and not raw_slots:
                abort(400)
            try:
                slots = [int(value) for value in raw_slots]
            except ValueError:
                abort(400)
            if len(set(slots)) != len(slots) or any(not (0 <= slot <= 8 or 16 <= slot <= 31) for slot in slots):
                abort(400)
            args = ("remove-slots", ",".join(map(str, slots)), "--yes")
        try:
            command = cable_command(*args)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings_page"))
        settings = radio_settings()
        port, key_file = settings["port"], settings["key_file"]
        try:
            result = subprocess.run(command, text=True, capture_output=True,
                                    timeout=900 if action == "all" else 300)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + "\nModule removal timed out. Reconnect and inspect the radio before retrying."
            return render_template("radio.html", inventory=None, port=port, key_file=key_file,
                                   action_result={"ok": False, "output": output}), 504
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return render_template("radio.html", inventory=None, port=port, key_file=key_file,
                               action_result={"ok": result.returncode == 0, "output": output}), (
                                   200 if result.returncode == 0 else 500)

    @app.post("/sources")
    def add_source():
        source = request.form.get("source", "").strip()
        ref = request.form.get("ref", "").strip() or None
        try:
            entry = catalog.add(source, ref)
            flash(f"Added {entry['id']}.", "success")
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            flash(str(exc), "error")
        return redirect(url_for("index"))

    @app.post("/refresh")
    def refresh_all():
        for entry in catalog.sources():
            try:
                catalog.repo(entry, refresh=True)
            except (OSError, RuntimeError, ValueError) as exc:
                flash(f"{entry.get('id')}: {exc}", "error")
        flash("Catalog refresh finished.", "success")
        return redirect(url_for("index"))

    @app.get("/repo/<repo_id>")
    def detail(repo_id):
        try:
            repo = catalog.by_id(repo_id)
        except KeyError:
            abort(404)
        return render_template("detail.html", repo=repo, result=None)

    @app.post("/repo/<repo_id>/install")
    def install(repo_id):
        try:
            repo = catalog.by_id(repo_id)
        except KeyError:
            abort(404)
        if not repo.get("bundle"):
            flash("This project has no installable module package.", "error")
            return redirect(url_for("detail", repo_id=repo_id))
        try:
            flags = catalog_install_flags(repo, request.form.get("experimental") == "yes")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("detail", repo_id=repo_id))
        try:
            command = cable_command("load", str(repo["bundle"]["path"]), *flags)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings_page"))
        port = radio_settings()["port"]
        if request.form.get("verify") == "yes":
            command.append("--verify")
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=240)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + "\nInstaller timed out after 240 seconds."
            return render_template("detail.html", repo=repo,
                                   result={"ok": False, "output": output,
                                           "returncode": "timeout"}), 504
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return render_template("detail.html", repo=repo,
                               result={"ok": result.returncode == 0, "output": output,
                                       "returncode": result.returncode}), (500 if result.returncode else 200)

    @app.get("/repo/<repo_id>/file/<path:filename>")
    def repo_file(repo_id, filename):
        try:
            repo = catalog.by_id(repo_id)
        except KeyError:
            abort(404)
        root = Path(repo["path"]).resolve()
        target = (root / filename).resolve()
        allowed = set()
        for image in repo['screenshots']:
            src = image['src']
            parsed = urlparse(src)
            if not parsed.scheme and not parsed.netloc and not src.startswith('/'):
                allowed.add((root / Path(repo.get('readme_file', '')).parent / src).resolve())
        if (root not in target.parents or not target.is_file() or target not in allowed
                or any(part.startswith('.') for part in Path(filename).parts)
                or any(part.startswith('.') for part in target.relative_to(root).parts)
                or target.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}):
            abort(404)
        return send_from_directory(root, filename)

    @app.context_processor
    def helpers():
        def screenshot_url(repo, src):
            parsed = urlparse(src)
            if parsed.scheme == "https":
                return src
            if parsed.scheme or src.startswith("/"):
                return ""
            clean = (Path(repo["readme_file"]).parent / src).as_posix()
            return url_for("repo_file", repo_id=repo["id"], filename=clean)
        return {"screenshot_url": screenshot_url}

    return app


def print_repo(repo: dict) -> None:
    print(f"{repo['id']}  {repo['title']}")
    print(f"  {repo['summary']}")
    print(f"  status: {repo.get('status_label', 'Unreviewed')}")
    if repo.get("native_flux"):
        print("  origin: Flux native")
    if repo.get("blocked_reason"):
        print(f"  blocked: {repo['blocked_reason']}")
    if repo.get("required_packs"):
        print("  requires: " + ", ".join(repo["required_packs"]))
    print(f"  source: {repo['source']}  commit: {repo['commit'][:12]}")
    if repo.get("bundle"):
        for module in repo["bundle"]["modules"]:
            print(f"  {module['kind']} slot {module['slot']}: {module['name']} (API {module['level']})")
    elif not repo.get("error"):
        print("  no installable bundle found")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config())
    parser.add_argument("--cache", type=Path, default=default_cache())
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a Git URL or local repo")
    add.add_argument("source")
    add.add_argument("--ref", help="branch or tag to follow")
    remove = sub.add_parser("remove", help="remove a repository from the catalog")
    remove.add_argument("repo_id")
    listing = sub.add_parser("list", help="list catalog repos and installable modules")
    listing.add_argument("--refresh", action="store_true")
    show = sub.add_parser("show", help="show one repository's README metadata and screenshots")
    show.add_argument("repo_id")
    show.add_argument("--refresh", action="store_true")
    refresh = sub.add_parser("refresh", help="fetch the latest commits")
    refresh.add_argument("repo_id", nargs="?")
    serve = sub.add_parser("serve", help="start the local Flask catalog")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5055)
    serve.add_argument("--debug", action="store_true")
    install = sub.add_parser("install", help="install a repository's format-1 bundle")
    install.add_argument("repo_id")
    install.add_argument("--port", required=True)
    install.add_argument("--key-file")
    install.add_argument("--verify", action="store_true")
    install.add_argument("--experimental", action="store_true", help="acknowledge experimental module limitations")
    args = parser.parse_args(argv)
    store = ModuleStore(args.config, args.cache)
    try:
        if args.command == "add":
            entry = store.add(args.source, args.ref)
            print(f"Added {entry['id']}: {entry['source']}")
        elif args.command == "remove":
            if not store.remove(args.repo_id):
                parser.error(f"no repository with id {args.repo_id!r}")
            print(f"Removed {args.repo_id} from the catalog (cached checkout retained).")
        elif args.command == "list":
            for entry in store.sources():
                repo = store.repo(entry, refresh=args.refresh)
                print_repo(repo)
        elif args.command == "show":
            repo = store.by_id(args.repo_id, refresh=args.refresh)
            print_repo(repo)
            print("\nREADME preview:\n")
            print("\n".join(repo["readme"].splitlines()[:80]))
            if repo["screenshots"]:
                print("\nScreenshots:")
                for image in repo["screenshots"]:
                    print(f"  {image['alt']}: {image['src']}")
        elif args.command == "refresh":
            entries = store.sources()
            chosen = [e for e in entries if args.repo_id is None or e["id"] == args.repo_id]
            if not chosen:
                parser.error(f"no repository with id {args.repo_id!r}")
            for entry in chosen:
                print_repo(store.repo(entry, refresh=True))
        elif args.command == "serve":
            app = create_app(store)
            app.run(host=args.host, port=args.port, debug=args.debug)
        elif args.command == "install":
            repo = store.by_id(args.repo_id)
            if not repo.get("bundle"):
                raise ValueError("repository has no installable format-1 module bundle")
            command = [sys.executable, str(ROOT / "tools" / "k6_install.py"),
                       "--port", args.port]
            if args.key_file:
                command += ["--key-file", args.key_file]
            command += ["load", str(repo["bundle"]["path"]), *catalog_install_flags(repo, args.experimental)]
            if args.verify:
                command.append("--verify")
            return subprocess.run(command).returncode
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"k6-store: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
