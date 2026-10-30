#!/usr/bin/env python3
"""Exporta imagens de jogos RPG Maker MV/MZ para uma pasta organizada.

Requer rpgmaker_browser.py na mesma pasta; usa apenas a biblioteca padrão.

    python3 rpgmaker_export_images.py "/caminho/do/jogo/Game.exe"
    python3 rpgmaker_export_images.py "/caminho/do/jogo.zip" -o "/caminho/export"

A saída padrão é ./export. Mantém a estrutura original, por exemplo:
export/img/characters/Actor1.png e export/img/faces/Actor1.png.
Imagens MV .rpgmvp e MZ .png_ são convertidas em PNG usando a chave de
data/System.json do próprio jogo. Não altera os arquivos originais.
"""

import argparse
import codecs
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import rpgmaker_browser as launcher
except ImportError:
    sys.exit("Erro: coloque rpgmaker_browser.py na mesma pasta deste script.")


IMAGE_EXTENSIONS = {".png", ".apng", ".jpg", ".jpeg", ".jfif", ".gif", ".webp",
                    ".bmp", ".svg", ".svgz", ".tif", ".tiff", ".ico", ".cur", ".avif"}
ENCRYPTED_EXTENSIONS = {".rpgmvp", ".png_"}
RPG_HEADER = bytes.fromhex("5250474d560000000003010000000000")
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def image_names(game, output, target, encoding):
    """Recursos abaixo da raiz web, incluindo fontes e ícones fora de img/."""
    if isinstance(game.source, launcher.ZipSource):
        prefix = game.prefix.rstrip("/") + "/" if game.prefix else ""
        records = []
        for info in game.source.archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode(encoding)
                except UnicodeError:
                    pass
            try:
                name = launcher.clean_path(name)
            except ValueError:
                continue
            records.append((name, info))
        for name, info in records:
            if name.startswith(prefix):
                relative = name[len(prefix):]
                if Path(relative).suffix.lower() in IMAGE_EXTENSIONS | ENCRYPTED_EXTENSIONS:
                    yield game.source, info, relative
        # Extras distribuídos ao lado de www/ também pertencem ao pacote do jogo.
        if game.prefix and Path(game.prefix).name.lower() == "www":
            outer = str(Path(game.prefix).parent).replace("\\", "/")
            outer = "" if outer == "." else outer + "/"
            for name, info in records:
                if name.startswith(outer) and not name.startswith(prefix):
                    relative = name[len(outer):]
                    if Path(relative).suffix.lower() in IMAGE_EXTENSIONS | ENCRYPTED_EXTENSIONS:
                        yield game.source, info, "outros/" + relative
    else:
        root = game.source.root
        for folder, dirs, files in os.walk(root, followlinks=False):
            # Não segue links de diretório e evita reexportar a própria saída.
            dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink()
                             and (Path(folder) / d).resolve() != output)
            for name in sorted(files):
                path = Path(folder) / name
                if path.suffix.lower() in IMAGE_EXTENSIONS | ENCRYPTED_EXTENSIONS:
                    relative = path.relative_to(root).as_posix()
                    yield game.source, relative, relative
        target = Path(target).expanduser().resolve()
        outer = target if target.is_dir() else target.parent
        if outer != root and root.parent == outer:
            source = launcher.DiskSource(outer, game.source.encoding)
            for folder, dirs, files in os.walk(outer, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink()
                                 and (Path(folder) / d).resolve() not in (root, output))
                for name in sorted(files):
                    path = Path(folder) / name
                    if path.suffix.lower() in IMAGE_EXTENSIONS | ENCRYPTED_EXTENSIONS:
                        relative = path.relative_to(outer).as_posix()
                        yield source, relative, "outros/" + relative


def encryption_key(game):
    try:
        data = json.loads(launcher.decode_text(launcher.read_small(game.source, game.path("data/System.json"))))
        value = data.get("encryptionKey", "")
        if not isinstance(value, str) or len(value) != 32:
            return None
        return bytes.fromhex(value)
    except (OSError, ValueError, UnicodeError, AttributeError, launcher.GameError):
        return None


def copy_image(stream, out, encrypted, key):
    digest = hashlib.sha256()
    if encrypted:
        if key is None:
            raise ValueError("chave de criptografia ausente ou inválida em data/System.json")
        header = stream.read(16)
        first = stream.read(16)
        if header != RPG_HEADER or len(first) != 16:
            raise ValueError("cabeçalho RPG Maker inválido ou arquivo incompleto")
        decoded = bytes(a ^ b for a, b in zip(first, key))
        if not decoded.startswith(PNG_HEADER):
            raise ValueError("a chave não produziu uma imagem PNG válida")
        out.write(decoded)
        digest.update(decoded)
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        out.write(chunk)
        digest.update(chunk)
    return digest.digest()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def export_one(source, name, relative_name, output, key):
    relative = Path(launcher.clean_path(relative_name))
    encrypted = relative.suffix.lower() in ENCRYPTED_EXTENSIONS
    if encrypted:
        relative = relative.with_suffix(".png")
    destination = output / relative
    try:
        destination.parent.resolve().relative_to(output)
    except ValueError:
        raise ValueError("a pasta de destino contém um link para fora de export") from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".rpgmaker-", dir=str(destination.parent), delete=False) as out:
            temp_name = Path(out.name)
            if isinstance(name, zipfile.ZipInfo):
                if name.flag_bits & 1:
                    raise ValueError("arquivo ZIP com senha; extraia o pacote antes")
                stream = source.archive.open(name)
            else:
                stream, _ = source.open(name)
            with stream:
                digest = copy_image(stream, out, encrypted, key)
        # Mantém o nome original sempre que possível e nunca sobrescreve arquivos.
        index = 0
        while True:
            if index == 0:
                candidate = destination
            else:
                tag = "__criptografado" if encrypted else "__original"
                candidate = destination.with_name(destination.stem + tag
                                                  + ("_" + str(index) if index > 1 else "")
                                                  + destination.suffix)
            if candidate.is_symlink():
                raise ValueError("o arquivo de destino é um link simbólico: " + str(candidate))
            if candidate.exists():
                if candidate.is_file() and file_digest(candidate) == digest:
                    return "duplicada", candidate
                index += 1
                continue
            try:
                with candidate.open("xb") as out, temp_name.open("rb") as stream:
                    try:
                        shutil.copyfileobj(stream, out, 1024 * 1024)
                    except Exception:
                        candidate.unlink(missing_ok=True)
                        raise
                return "exportada", candidate
            except FileExistsError:
                continue
    finally:
        if temp_name is not None:
            temp_name.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jogo", help="Game.exe, pasta do jogo, index.html, .zip ou .nw")
    parser.add_argument("-o", "--output", default="export", help="Pasta de saída (padrão: ./export)")
    parser.add_argument("--zip-encoding", default="cp932", help="Codificação legada dos nomes (padrão: cp932)")
    args = parser.parse_args()
    try:
        codecs.lookup(args.zip_encoding)
    except LookupError:
        parser.error("Encoding desconhecido: " + args.zip_encoding)
    game = None
    try:
        game = launcher.discover(args.jogo, args.zip_encoding)
        output = Path(args.output).expanduser().resolve()
        if isinstance(game.source, launcher.DiskSource):
            try:
                game.source.root.relative_to(output)
            except ValueError:
                pass
            else:
                raise ValueError("a pasta de saída não pode conter a raiz dos arquivos do jogo")
        output.mkdir(parents=True, exist_ok=True)
        names = sorted(image_names(game, output, args.jogo, args.zip_encoding),
                       key=lambda entry: (entry[2].lower().endswith(tuple(ENCRYPTED_EXTENSIONS)), entry[2]))
        key = encryption_key(game)
        exported = duplicates = failed = 0
        print("RPG Maker %s | %s" % (game.engine, game.source.label))
        print("Saída: %s" % output)
        for source, name, relative in names:
            try:
                status, result = export_one(source, name, relative, output, key)
                if status == "exportada":
                    exported += 1
                    print("  + " + str(result.relative_to(output)))
                else:
                    duplicates += 1
            except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, launcher.GameError) as error:
                failed += 1
                print("  ! %s: %s" % (name.filename if isinstance(name, zipfile.ZipInfo) else name, error), file=sys.stderr)
        print("\n%d imagem(ns) exportada(s); %d duplicada(s); %d falha(s)." %
              (exported, duplicates, failed))
        if not names:
            print("Nenhuma imagem encontrada na raiz web do jogo.")
        return 1 if failed else 0
    except (OSError, ValueError, zipfile.BadZipFile, launcher.GameError) as error:
        print("Erro: " + str(error), file=sys.stderr)
        return 1
    finally:
        if game:
            game.source.close()


if __name__ == "__main__":
    sys.exit(main())
