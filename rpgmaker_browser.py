#!/usr/bin/env python3
"""Abre jogos RPG Maker MV/MZ no navegador ou em NW.js nativo no Linux.

Uso (Python 3.8+):
    python3 rpgmaker_browser.py "/caminho/do/jogo/Game.exe"
    python3 rpgmaker_browser.py "/caminho/do/jogo"
    python3 rpgmaker_browser.py "/caminho/do/jogo.zip" --no-browser
    python3 rpgmaker_browser.py "/caminho/do/jogo/Game.exe" --native
    python3 rpgmaker_browser.py "/caminho/do/jogo" --native --nw-version 0.92.0

Aceita MV (www/), MZ (raiz), package.nw e ZIP anexado ao executável NW.js.
Lê ZIPs diretamente, sem extrair nem executar o EXE. Não altera o jogo.
Corrige diferenças de maiúsculas e nomes japoneses CP932/Shift-JIS comuns.
Inclui adaptação web para o plugin TDS Bitmap Fonts 1.6.

--native baixa o NW.js Linux oficial no cache, verificando seu SHA-256.
Não precisa de pip, npm, Wine, Proton, sudo ou instalação global. O Ubuntu
precisa ter uma sessão gráfica e as bibliotecas de sistema exigidas pelo NW.js.
Prepara uma cópia dos arquivos em ~/.local/share/rpgmaker-launcher e mantém
os saves separados nessa pasta. O runtime fica em ~/.cache/rpgmaker-launcher.
Respeita XDG_DATA_HOME e XDG_CACHE_HOME. --nw-path reutiliza um NW.js existente.
O modo nativo usa as APIs reais de Node/NW.js e não aplica o patch web de fontes.

Os saves ficam no navegador: use o mesmo navegador, pasta do jogo e porta.
Não use navegação privada nem limpe os dados do site se quiser mantê-los.
Saves do Windows não são importados. Ctrl+C encerra o servidor.

Limites: RPG Maker XP/VX/VX Ace/2000/2003, Enigma Virtual Box, scripts .bin
compilados pelo nwjc e plugins exclusivos de Node/NW.js/Windows precisam de
tratamento específico; um navegador não executa esses componentes nativos.

Referências dos formatos:
https://rpgmakerofficial.com/product/MV_Help/page/01_11_04.html
https://rpgmakerofficial.com/product/MZ_help-en/01_11_03.html
https://docs.nwjs.io/For%20Users/Package%20and%20Distribute/
"""

import argparse
import codecs
import errno
import hashlib
import io
import json
import mimetypes
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unicodedata
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit


class GameError(Exception):
    pass


def clean_path(name):
    """Caminho virtual relativo; nunca aceita saída da raiz do jogo."""
    name = name.replace("\\", "/")
    parts = name.split("/")
    if "\x00" in name or any(p == ".." or ":" in p for p in parts):
        raise ValueError("Caminho fora da pasta do jogo")
    return "/".join(p for p in parts if p not in ("", "."))


def key(name):
    return unicodedata.normalize("NFC", name).casefold()


def aliases(name, encoding="cp932"):
    """Inclui nomes estragados por extração de ZIP japonês como CP437/1252."""
    result = {key(name)}
    if not name.isascii():
        for wrong in ("cp437", "cp1252"):
            try:
                result.add(key(name.encode(wrong).decode(encoding)))
            except (UnicodeError, LookupError):
                pass
        if any(0xDC80 <= ord(c) <= 0xDCFF for c in name):
            try:
                result.add(key(os.fsencode(name).decode(encoding)))
            except UnicodeError:
                pass
    return result


def decode_text(data):
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp932")


class DiskSource:
    def __init__(self, root, encoding="cp932"):
        self.root = Path(root).resolve()
        self.encoding = encoding
        self.label = str(self.root)

    @lru_cache(maxsize=512)
    def children(self, folder):
        return tuple(folder.iterdir())

    def resolve(self, name, directory=False):
        path = self.root
        for part in clean_path(name).split("/"):
            if not part:
                continue
            direct = path / part
            if direct.exists():
                path = direct
            else:
                wanted = aliases(part, self.encoding)
                matches = [p for p in self.children(path)
                           if wanted & aliases(p.name, self.encoding)]
                if len(matches) != 1:
                    raise FileNotFoundError(name)
                path = matches[0]
            path = path.resolve()
            try:
                path.relative_to(self.root)
            except ValueError:
                raise FileNotFoundError(name) from None
        if not (path.is_dir() if directory else path.is_file()):
            raise FileNotFoundError(name)
        return path

    def listdir(self, name):
        return sorted(p.name for p in self.children(self.resolve(name, directory=True)))

    def open(self, name):
        path = self.resolve(name)
        return path.open("rb"), path.stat().st_size

    def candidates(self):
        # Busca limitada; não percorre imagens, áudio ou dependências.
        skip = {"js", "data", "img", "audio", "movies", "fonts", "effects",
                "node_modules", "locales", "swiftshader", "save", "saves"}
        folders = [""]
        for folder in folders:
            depth = folder.count("/") + bool(folder)
            try:
                children = self.children(self.root / folder)
            except OSError:
                continue
            for child in children:
                rel = posixpath.join(folder, child.name)
                if child.is_file() and child.suffix.lower() == ".html":
                    yield rel
                if child.is_file() and child.name.lower() == "package.json":
                    yield rel
                if (depth < 3 and len(folders) < 300 and child.is_dir()
                        and not child.is_symlink() and key(child.name) not in skip
                        and not child.name.startswith(".")):
                    folders.append(rel)

    def close(self):
        pass


class ZipSource:
    def __init__(self, path, encoding="cp932"):
        self.root = Path(path).resolve()
        self.label = str(self.root) + " [ZIP, sem extrair]"
        self.archive = zipfile.ZipFile(self.root)
        self.names = {}
        self.entries = []
        for info in self.archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # Decodifique ANTES de tratar barras: CP932 usa 0x5c em caracteres.
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode(encoding)
                except UnicodeError:
                    pass
            try:
                name = clean_path(name)
            except ValueError:
                continue
            self.entries.append(name)
            for variant in {name, info.filename}:
                try:
                    normalized = key(clean_path(variant))
                    # Ambiguidade de case em ZIP é erro, não escolha arbitrária.
                    previous = self.names.get(normalized)
                    self.names[normalized] = (info if previous is None
                                              or previous is info else False)
                except ValueError:
                    pass

    def resolve(self, name):
        info = self.names.get(key(clean_path(name)))
        if not info:
            raise FileNotFoundError(name)
        return info

    def open(self, name):
        info = self.resolve(name)
        if info.flag_bits & 1:
            raise GameError("O ZIP tem senha. Extraia-o antes de usar o script.")
        return self.archive.open(info), info.file_size

    def listdir(self, name):
        prefix = clean_path(name).rstrip("/") + "/"
        result = set()
        for entry in self.entries:
            if key(entry).startswith(key(prefix)):
                result.add(entry[len(prefix):].split("/")[0])
        if not result:
            raise FileNotFoundError(name)
        return sorted(result)

    def candidates(self):
        return (n for n in self.entries if n.lower().endswith(".html")
                or posixpath.basename(n).lower() == "package.json")

    def close(self):
        self.archive.close()


def read_small(source, name, limit=8 * 1024 * 1024):
    stream, size = source.open(name)
    with stream:
        if size > limit:
            raise GameError("Arquivo de configuração grande demais: " + name)
        return stream.read(limit + 1)


def exists(source, name):
    try:
        source.resolve(name)
        return True
    except (OSError, ValueError):
        return False


@dataclass
class Game:
    source: object
    prefix: str
    entry: str
    engine: str

    def path(self, name):
        return posixpath.join(self.prefix, clean_path(name))


def detect_game(source):
    candidates = list(source.candidates())
    entries = []
    # Respeita o main do NW.js, incluindo nomes de HTML personalizados.
    for name in candidates:
        if posixpath.basename(name).lower() != "package.json":
            continue
        try:
            manifest = json.loads(decode_text(read_small(source, name)))
            main = urlsplit(manifest.get("main", ""))
            if not main.scheme and not main.netloc and main.path:
                entries.append(posixpath.join(posixpath.dirname(name),
                                               clean_path(main.path)))
        except (ValueError, OSError, UnicodeError, AttributeError):
            pass
    entries += sorted((n for n in candidates if n.lower().endswith(".html")),
                      key=lambda n: (n.count("/"), n.lower()))
    for entry in entries:
        prefix = posixpath.dirname(entry)
        for core, engine in (("rmmz_core.js", "MZ"), ("rpg_core.js", "MV")):
            if (exists(source, entry)
                    and exists(source, posixpath.join(prefix, "js", core))):
                if isinstance(source, DiskSource):
                    source = DiskSource(source.resolve(entry).parent, source.encoding)
                    prefix = ""
                return Game(source, prefix, posixpath.basename(entry), engine)
    return None


def discover(target, encoding):
    target = Path(target).expanduser().resolve()
    if not target.exists():
        raise GameError("Caminho não encontrado: " + str(target))
    if target.is_file() and target.suffix.lower() in (".zip", ".nw"):
        sources = [target]
    else:
        root = target if target.is_dir() else target.parent
        game = detect_game(DiskSource(root, encoding))
        if game:
            return game
        sources = ([target] if target.is_file() else [])
        sources += sorted(p for p in root.iterdir()
                          if p.is_file() and p.suffix.lower() in (".nw", ".zip"))
    for path in sources:
        if zipfile.is_zipfile(path):
            source = ZipSource(path, encoding)
            game = detect_game(source)
            if game:
                return game
            source.close()
    raise GameError(
        "Não achei um jogo MV/MZ com HTML e js/rpg_core.js ou js/rmmz_core.js.\n"
        "Passe o Game.exe, index.html, a pasta extraída completa ou o ZIP do jogo.\n"
        "Um EXE isolado pode usar Enigma Virtual Box ou ser apenas um instalador;\n"
        "nesse caso, é preciso obter/extrair os arquivos web primeiro.\n"
        "Jogos XP/VX/VX Ace/2000/2003 (RGSS/RPG_RT) não rodam com este script.")


# Injetado apenas na resposta HTTP. Não finge que o navegador tem Node/NW.js:
# a própria engine seleciona seu modo web e os saves próprios do navegador.
BROWSER_HELPER = r"""
<style>
#__rpg_browser_sound, #__rpg_browser_error { z-index: 2147483647 !important; }
</style>
<script>
(function () {
    'use strict';
    var latestError = '';
    function showError(message) {
        latestError = String(message);
        if (!document.body) return;
        var box = document.getElementById('__rpg_browser_error');
        if (!box) {
            box = document.createElement('div');
            box.id = '__rpg_browser_error';
            box.style.cssText = 'position:fixed;bottom:12px;left:12px;right:12px;'
                + 'z-index:2147483647;background:#241a20;color:#fff;padding:16px;'
                + 'border:1px solid #fa8;border-radius:8px;font:14px sans-serif;'
                + 'white-space:pre-wrap;max-height:35vh;overflow:auto';
            document.body.appendChild(box);
        }
        box.textContent = 'Erro ao carregar o jogo:\n' + latestError;
        if (/require|process|\bnw\b|module is not defined|\.bin\b/i.test(latestError)) {
            box.textContent += '\nEste plugin parece depender de Node/NW.js ou '
                + 'código compilado. Ele pode precisar de uma adaptação específica.';
        }
        var close = document.createElement('button');
        close.textContent = 'Fechar aviso';
        close.style.cssText = 'display:block;margin-top:10px';
        close.onclick = function () { box.remove(); };
        box.appendChild(close);
    }
    window.addEventListener('error', function (e) {
        if (e.message) {
            showError(e.message + (e.filename ? '\n' + e.filename + ':' + e.lineno : ''));
        } else if (e.target && (e.target.src || e.target.href)) {
            console.error('[RPG Browser] Recurso não carregado:', e.target.src || e.target.href);
        }
    }, true);
    window.addEventListener('unhandledrejection', function (e) {
        showError(e.reason && e.reason.message || e.reason || 'Promise rejeitada');
    });
    function unlockSound() {
        var audio = window.WebAudio;
        var context = audio && (audio._context || audio._audioContext);
        if (context && context.state === 'suspended') context.resume().catch(console.warn);
    }
    document.addEventListener('pointerdown', unlockSound, true);
    document.addEventListener('keydown', unlockSound, true);
    document.addEventListener('DOMContentLoaded', function () {
        if (latestError) showError(latestError);
        var note = document.createElement('button');
        note.id = '__rpg_browser_sound';
        note.textContent = 'Clique para jogar e ativar o som';
        note.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);'
            + 'z-index:2147483646;padding:12px 20px;background:#18232e;color:white;'
            + 'border:1px solid #899;border-radius:8px;cursor:pointer;font:14px sans-serif';
        note.addEventListener('click', function () { unlockSound(); note.remove(); });
        document.body.appendChild(note);
    });
})();
</script>
"""


def prepare_html(data):
    text = decode_text(data)
    # A resposta passa a UTF-8, inclusive se o HTML original era Shift-JIS.
    text = re.sub(r'(?i)(charset\s*=\s*[\"\x27]?)[\w-]+', r'\1utf-8', text)
    head = re.search(r"<head\b[^>]*>", text, re.I)
    if head:
        text = text[:head.end()] + BROWSER_HELPER + text[head.end():]
    else:
        first = re.search(r"<script\b", text, re.I)
        index = first.start() if first else len(text)
        text = text[:index] + BROWSER_HELPER + text[index:]
    return text.encode("utf-8")


def adapt_bitmap_fonts(game, data):
    """Adaptação restrita à assinatura conhecida do TDS Bitmap Fonts 1.6.

    O plugin original continua responsável pela renderização. Substituímos só
    a descoberta e leitura de fontes, que dependem de fs/path/process no NW.js.
    Os JSONs ficam embutidos na resposta JS; não expomos APIs nativas ao jogo.
    """
    if b"TDS_BitmapFonts" not in data:
        return data
    text = decode_text(data)
    signatures = ("Imported.TDS_BitmapFonts", "BitmapFontManager.createFontObject",
                  "BitmapFontManager.loadAllBitmapFonts", "BitmapFontManager.processAtlasData")
    if not all(s in text for s in signatures):
        return data
    if not re.search(r"Version:\s*1\.6(?![\d.])", text):
        print("[Plugin] TDS Bitmap Fonts: versão sem adaptação validada.", file=sys.stderr)
        return data
    baseline = "62841dab71873378a179156d8c0d50bb91be4cc8dbd1c4409a01eae3bb2377cf"
    actual = hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    if actual != baseline:
        print("[Plugin] TDS Bitmap Fonts: conteúdo diferente da versão validada; preservado.", file=sys.stderr)
        return data
    base = "fonts/Bitmap Fonts"
    fonts = {}
    total = 0
    for name in game.source.listdir(game.path(base)):
        folder = posixpath.join(base, name)
        try:
            files = game.source.listdir(game.path(folder))
        except (FileNotFoundError, NotADirectoryError):
            continue
        bundle = {"settings": None, "atlases": {}, "bitmaps": []}
        for filename in files:
            stem, ext = posixpath.splitext(filename)
            if ext.lower() == ".json":
                content = decode_text(read_small(game.source, game.path(posixpath.join(folder, filename))))
                total += len(content)
                if total > 32 * 1024 * 1024:
                    raise GameError("Os JSONs de fontes excedem 32 MiB")
                if stem.lower() == "settings":
                    bundle["settings"] = content
                else:
                    bundle["atlases"][stem] = content
            elif ext.lower() == ".png":
                bundle["bitmaps"].append(stem)
        if bundle["settings"] is None:
            raise GameError("Falta Settings.json na fonte bitmap: " + name)
        fonts[name] = bundle
    patch = r"""
;(function (fonts) {
    BitmapFontManager.createFontObject = function (name) {
        var bundle = fonts[name];
        if (!bundle) throw new Error('Fonte bitmap não encontrada: ' + name);
        var font = {settings: JsonEx.parse(bundle.settings), atlases: {}};
        this._fonts[name] = font;
        Object.keys(bundle.atlases).forEach(function (atlas) {
            font.atlases[atlas] = BitmapFontManager.processAtlasData(JsonEx.parse(bundle.atlases[atlas]));
        });
        bundle.bitmaps.forEach(function (bitmap) {
            ImageManager.loadBitmapFontImage(name, bitmap, 0);
        });
    };
    BitmapFontManager.loadAllBitmapFonts = function () {
        Object.keys(fonts).forEach(function (name) {
            BitmapFontManager.createFontObject(name);
        });
    };
})(__FONT_DATA__);
""".replace("__FONT_DATA__", json.dumps(fonts, ensure_ascii=True))
    print("[Plugin] TDS Bitmap Fonts 1.6: carregamento web de %d fonte(s)." % len(fonts), flush=True)
    return (text + "\n// RPG Maker Browser: adaptação web de fontes bitmap.\n" + patch).encode("utf-8")


MIME = {".js": "application/javascript", ".json": "application/json",
        ".wasm": "application/wasm", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
        ".woff": "font/woff", ".woff2": "font/woff2"}


def handler_for(game, verbose=False, native=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RPGMakerBrowser/1.0"

        def log_message(self, fmt, *args):
            if verbose or (len(args) > 1 and str(args[1]).startswith(("4", "5"))):
                super().log_message(fmt, *args)

        def do_GET(self):
            self.serve(False)

        def do_HEAD(self):
            self.serve(True)

        def serve(self, head_only):
            port = self.server.server_port
            allowed = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
            if self.headers.get("Host", "") not in allowed:
                self.send_error(403, "Host local invalido")
                return
            stream = None
            try:
                raw = unquote_to_bytes(urlsplit(self.path).path)
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("cp932")
                name = clean_path(name) or game.entry
                stream, size = game.source.open(game.path(name))
                suffix = posixpath.splitext(name)[1].lower()
                mime = MIME.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
                if not native and key(name) == key(game.entry):
                    with stream:
                        if size > 16 * 1024 * 1024:
                            raise GameError("HTML de entrada grande demais")
                        data = prepare_html(stream.read())
                    stream = io.BytesIO(data)
                    size = len(data)
                    mime = "text/html; charset=utf-8"
                elif not native and suffix == ".js" and key(name).startswith("js/plugins/"):
                    if size <= 4 * 1024 * 1024:
                        with stream:
                            data = adapt_bitmap_fonts(game, stream.read())
                        stream = io.BytesIO(data)
                        size = len(data)
                start, end, status = 0, size - 1, 200
                byte_range = self.headers.get("Range")
                if byte_range:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range.strip())
                    if not match or not any(match.groups()) or not size:
                        self.range_error(size)
                        return
                    left, right = match.groups()
                    if left:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    else:
                        start = max(0, size - int(right))
                    if start > end or start >= size:
                        self.range_error(size)
                        return
                    status = 206
                    stream.seek(start)
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                if status == 206:
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.end_headers()
                if not head_only:
                    while length:
                        chunk = stream.read(min(length, 256 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        length -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
                if name != "favicon.ico":
                    print("[Arquivo não encontrado] " + ascii(name), file=sys.stderr)
                self.send_error(404, "Arquivo nao encontrado")
            except (ValueError, UnicodeError):
                self.send_error(400, "Caminho invalido")
            except (OSError, GameError, RuntimeError, zipfile.BadZipFile) as e:
                print("[Erro] " + str(e), file=sys.stderr)
                self.send_error(500, "Erro ao ler arquivo do jogo; veja o terminal")
            finally:
                if stream:
                    stream.close()

        def range_error(self, size):
            self.send_response(416)
            self.send_header("Content-Range", "bytes */" + str(size))
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def game_identity(game):
    return hashlib.sha256(os.fsencode(str(game.source.root) + "/" + game.prefix)).hexdigest()[:20]


def native_files(game):
    """Enumera somente arquivos da raiz web, sem os executáveis Windows."""
    if isinstance(game.source, ZipSource):
        prefix = game.prefix.rstrip("/") + "/" if game.prefix else ""
        for name in game.source.entries:
            if key(name).startswith(key(prefix)):
                rel = name[len(prefix):]
                if (rel and rel.split("/")[0].lower() not in ("save", "saves")
                        and posixpath.splitext(rel)[1].lower() not in (".exe", ".dll")):
                    yield rel
    else:
        for folder, dirs, files in os.walk(game.source.root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d.lower() not in ("save", "saves")
                       and not (Path(folder) / d).is_symlink()]
            for name in sorted(files):
                if Path(name).suffix.lower() not in (".exe", ".dll"):
                    rel = (Path(folder) / name).relative_to(game.source.root).as_posix()
                    if exists(game.source, rel):
                        yield rel


def prepare_native(game, profile):
    files = list(native_files(game))
    if isinstance(game.source, ZipSource):
        stamp = [(n, game.source.resolve(game.path(n)).CRC,
                  game.source.resolve(game.path(n)).file_size) for n in files]
    else:
        stamp = [(n, game.source.resolve(n).stat().st_mtime_ns,
                  game.source.resolve(n).stat().st_size) for n in files]
    fingerprint = hashlib.sha256(json.dumps(stamp).encode()).hexdigest()[:20]
    app = profile / "apps" / fingerprint
    savedir = profile / "save"
    savedir.mkdir(parents=True, exist_ok=True)
    app.parent.mkdir(parents=True, exist_ok=True)
    if (app / ".launcher-ready").is_file():
        return app, savedir
    print("Preparando cópia nativa de %d arquivos..." % len(files), flush=True)
    stage = Path(tempfile.mkdtemp(prefix=".preparing-", dir=str(app.parent)))
    try:
        for name in files:
            destination = stage / clean_path(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream, _ = game.source.open(game.path(name))
            with stream, destination.open("wb") as out:
                shutil.copyfileobj(stream, out, 1024 * 1024)
        (stage / "save").symlink_to(savedir, target_is_directory=True)
        (stage / ".launcher-ready").write_text(fingerprint, encoding="utf-8")
        try:
            stage.rename(app)
        except FileExistsError:
            if not (app / ".launcher-ready").is_file():
                raise GameError("Preparação incompleta em " + str(app))
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return app, savedir


def official_request(url):
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 RPGMakerLauncher/1.0"}), timeout=45)


def official_text(url, limit=4 * 1024 * 1024):
    with official_request(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise GameError("Resposta grande demais do servidor oficial")
    return data.decode("utf-8-sig")


def unpack_runtime(archive, destination):
    # Extração explícita, compatível com Python 3.8, sem links ou arquivos especiais.
    with tarfile.open(archive, "r:gz") as package:
        total = 0
        for member in package:
            if member.name.startswith("/"):
                raise GameError("Caminho absoluto no pacote do NW.js")
            name = clean_path(member.name)
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                total += member.size
                if total > 2 * 1024 ** 3:
                    raise GameError("Pacote NW.js excede 2 GiB descompactado")
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as stream, target.open("wb") as out:
                    shutil.copyfileobj(stream, out, 1024 * 1024)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            else:
                raise GameError("Tipo inesperado no pacote do NW.js: " + member.name)


def get_runtime(profile, cache, version=None, existing=None):
    if existing:
        path = Path(existing).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise GameError("--nw-path deve apontar para um binário nw executável")
        return path
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64",
            "i386": "ia32", "i686": "ia32"}.get(machine)
    if not arch:
        raise GameError("Arquitetura sem download automático: " + machine + "; use --nw-path")
    pin = profile / "nw-version.txt"
    if not version:
        version = pin.read_text().strip() if pin.is_file() else json.loads(
            official_text("https://nwjs.io/versions.json"))["stable"]
    version = "v" + version.lstrip("v")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise GameError("Versão de NW.js inválida: " + version)
    stem = "nwjs-%s-linux-%s" % (version, arch)
    root = cache / "runtimes" / stem
    binary = root / stem / "nw"
    if binary.is_file() and (root / ".verified-sha256").is_file():
        pin.write_text(version, encoding="utf-8")
        return binary
    base = "https://dl.nwjs.io/%s/" % version
    filename = stem + ".tar.gz"
    sums = official_text(base + "SHASUMS256.txt")
    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            expected = parts[0].lower()
            break
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise GameError("Não há checksum oficial para %s. Escolha outra versão/arquitetura "
                        "com --nw-version ou forneça --nw-path." % filename)
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".download-", dir=str(root.parent)))
    try:
        archive = stage / filename
        digest = hashlib.sha256()
        total = mark = 0
        print("Baixando NW.js %s (%s) do site oficial..." % (version, arch), flush=True)
        with official_request(base + filename) as response, archive.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise GameError("Download de NW.js excede 512 MiB")
                digest.update(chunk)
                out.write(chunk)
                if total - mark >= 32 * 1024 * 1024:
                    mark = total
                    print("  %d MiB recebidos" % (total // (1024 * 1024)), flush=True)
        if digest.hexdigest() != expected:
            raise GameError("SHA-256 do NW.js não confere; o pacote não será executado")
        unpack_runtime(archive, stage)
        archive.unlink()
        if not (stage / stem / "nw").is_file():
            raise GameError("O pacote oficial não contém o binário nw esperado")
        (stage / ".verified-sha256").write_text(expected, encoding="ascii")
        try:
            stage.rename(root)
        except FileExistsError:
            if not (root / ".verified-sha256").is_file():
                raise GameError("Cache de runtime incompleto: " + str(root))
        pin.write_text(version, encoding="utf-8")
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return binary


def check_runtime_libraries(binary):
    if not shutil.which("ldd"):
        return
    missing = set()
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(
        [str(binary.parent), str(binary.parent / "lib")]
        + ([environment["LD_LIBRARY_PATH"]] if environment.get("LD_LIBRARY_PATH") else []))
    for path in (binary, binary.parent / "lib/libnode.so", binary.parent / "lib/libnw.so"):
        if path.is_file():
            result = subprocess.run(["ldd", str(path)], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=environment)
            missing.update(re.findall(r"(\S+)\s+=>\s+not found", result.stdout))
    if missing:
        packages = {"libatomic.so.1": "libatomic1", "libnss3.so": "libnss3",
                    "libgtk-3.so.0": "libgtk-3-0", "libgbm.so.1": "libgbm1",
                    "libXss.so.1": "libxss1", "libxshmfence.so.1": "libxshmfence1"}
        hint = sorted({packages[n] for n in missing if n in packages})
        raise GameError("Faltam bibliotecas de sistema: %s.%s" % (", ".join(sorted(missing)),
                        "\nPacotes correspondentes no Ubuntu: " + " ".join(hint) if hint else ""))


def launch_native(game, args, port):
    if sys.platform != "linux":
        raise GameError("O modo --native automático é para Linux. Neste sistema, use o modo navegador.")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise GameError("O modo nativo precisa de uma sessão gráfica Linux (DISPLAY ou WAYLAND_DISPLAY)")
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))).expanduser()
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser()
    profile = (data / "rpgmaker-launcher" / game_identity(game)).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    binary = get_runtime(profile, cache / "rpgmaker-launcher", args.nw_version, args.nw_path)
    check_runtime_libraries(binary)
    app, savedir = prepare_native(game, profile)
    staged = Game(DiskSource(app, args.zip_encoding), "", game.entry, game.engine)
    with ThreadingHTTPServer(("127.0.0.1", port), handler_for(staged, args.verbose, native=True)) as server:
        manifest_path = app / "package.json"
        try:
            manifest = json.loads(decode_text(read_small(game.source, game.path("package.json"))))
        except (FileNotFoundError, ValueError, UnicodeError):
            manifest = {}
        manifest.update({"name": "rpgmaker-" + game_identity(game),
                         "main": "http://127.0.0.1:%d/" % server.server_port,
                         "nodejs": True, "node-remote": ["http://127.0.0.1/*"]})
        if not manifest.get("node-main"):
            # Preserva a convenção usada por MV/MZ para localizar fontes e saves.
            manifest["node-main"] = "__rpgmaker_node.cjs"
            (app / manifest["node-main"]).write_text(
                "if (!process.mainModule) process.mainModule = module;\n", encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print("RPG Maker %s | NW.js nativo Linux" % game.engine)
        print("Runtime: " + str(binary))
        print("Cópia do jogo: " + str(app))
        print("Saves: " + str(savedir), flush=True)
        try:
            with subprocess.Popen([str(binary), "--user-data-dir=" + str(profile / "chromium"), str(app)],
                                  cwd=str(app)) as child:
                try:
                    code = child.wait()
                except KeyboardInterrupt:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    raise
            if code:
                print("NW.js encerrou com código %d. Confira o erro acima; jogos antigos podem "
                      "precisar de --nw-version. Bibliotecas ausentes precisam estar instaladas "
                      "no sistema Linux." % code, file=sys.stderr)
            return code if code >= 0 else 1
        finally:
            server.shutdown()
            thread.join()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jogo", help="Game.exe, pasta, index.html, .nw ou .zip")
    parser.add_argument("--port", type=int, help="Porta fixa; padrão calculado pelo caminho do jogo")
    parser.add_argument("--no-browser", action="store_true", help="Só imprime a URL, sem abrir o navegador")
    parser.add_argument("--zip-encoding", default="cp932", help="Encoding legado dos nomes do ZIP (padrão: cp932)")
    parser.add_argument("--verbose", action="store_true", help="Mostra todas as requisições HTTP")
    parser.add_argument("--native", action="store_true", help="Abre em NW.js Linux; baixa o runtime oficial no cache")
    parser.add_argument("--nw-version", help="Versão do NW.js, ex.: 0.92.0; exige --native")
    parser.add_argument("--nw-path", help="Caminho de um binário nw já instalado; exige --native")
    args = parser.parse_args(argv)
    if (args.nw_version or args.nw_path) and not args.native:
        parser.error("--nw-version e --nw-path exigem --native")
    if args.native and args.no_browser:
        parser.error("--no-browser é apenas para o modo navegador")
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port deve estar entre 1 e 65535")
    try:
        codecs.lookup(args.zip_encoding)
    except LookupError:
        parser.error("Encoding desconhecido: " + args.zip_encoding)
    game = None
    try:
        game = discover(args.jogo, args.zip_encoding)
        identity = str(game.source.root) + "/" + game.prefix
        digest = hashlib.sha256(os.fsencode(identity)).digest()
        port = args.port or 18000 + int.from_bytes(digest[:4], "big") % 40000
        if args.native:
            return launch_native(game, args, port)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(game, args.verbose))
        except OSError as e:
            hint = ("\nEncerre a outra instância ou use --port OUTRA_PORTA.\n"
                    "Mudar a porta muda o endereço dos saves no navegador."
                    if e.errno == errno.EADDRINUSE else "")
            raise GameError("Não consegui abrir a porta %d: %s%s" % (port, e, hint)) from e
        url = "http://127.0.0.1:%d/" % port
        print("RPG Maker %s | %s" % (game.engine, game.source.label))
        print("Entrada: " + game.path(game.entry))
        print("Jogar: " + url)
        print("Saves no navegador. Preserve navegador, pasta e porta para acessá-los.")
        print("Ctrl+C para encerrar. O executável não será iniciado.", flush=True)
        if not args.no_browser:
            try:
                if not webbrowser.open(url, new=2):
                    print("Abra manualmente a URL acima no navegador.")
            except webbrowser.Error:
                print("Abra manualmente a URL acima no navegador.")
        with server:
            server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    except (GameError, OSError, zipfile.BadZipFile, tarfile.TarError, UnicodeError) as e:
        print("Erro: " + str(e), file=sys.stderr)
        return 1
    finally:
        if game:
            game.source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
