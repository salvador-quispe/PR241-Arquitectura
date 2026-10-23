import re
from dataclasses import dataclass, field
from pathlib import Path

LAYERS = ("domain", "application", "infrastructure")

_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", re.M)
_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed|strictfp)\s+)*"
    r"(@interface|class|interface|enum|record)\s+(\w+)([^{;]*)",
    re.M,
)
_ANN_RE = re.compile(r"@(\w+)")


def strip_comments(src):
    """Quita comentarios respetando strings, chars y text blocks; conserva los saltos de linea."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j == -1 else j + 3
            out.append(src[i:j])
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(src[i:j])
            i = j
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("\n" * src.count("\n", i, j))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


@dataclass
class JavaFile:
    rel: str
    package: str
    imports: list
    kind: str | None
    name: str | None
    supertypes: str
    annotations: set  # anotaciones a nivel de clase
    all_annotations: set  # todas las anotaciones del archivo
    code: str
    layer: str | None = None
    sub: tuple = ()


def parse_java(path, rel):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    code = strip_comments(raw)
    pkg = _PKG_RE.search(code)
    imports = _IMPORT_RE.findall(code)
    start = 0
    for m in list(_IMPORT_RE.finditer(code)) + ([pkg] if pkg else []):
        start = max(start, m.end())
    decl = _DECL_RE.search(code, start)
    kind = name = None
    supertypes, class_ann = "", set()
    if decl:
        kind, name, supertypes = decl.group(1), decl.group(2), decl.group(3).strip()
        class_ann = set(_ANN_RE.findall(code[start:decl.start()]))
    return JavaFile(
        rel=rel,
        package=pkg.group(1) if pkg else "",
        imports=imports,
        kind=kind,
        name=name,
        supertypes=supertypes,
        annotations=class_ann,
        all_annotations=set(_ANN_RE.findall(code)),
        code=code,
    )


class Proyecto:
    def __init__(self, root, nombre=None, rama="", commit=""):
        self.root = Path(root).resolve()
        self.nombre = nombre or self.root.name
        self.rama, self.commit = rama, commit
        self.main = self.root / "src" / "main" / "java"
        self.files = []
        if self.main.is_dir():
            for p in sorted(self.main.rglob("*.java")):
                self.files.append(parse_java(p, p.relative_to(self.root).as_posix()))
        self.base = self._find_base()
        self._asignar_capas()
        self.pom = self._leer("pom.xml")

    # ---------- helpers ----------
    def _leer(self, rel):
        p = self.root / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None

    def _find_base(self):
        if not self.main.is_dir():
            return None
        grupos = {}
        for d in self.main.rglob("*"):
            if d.is_dir() and d.name in LAYERS:
                grupos.setdefault(d.parent, set()).add(d.name)
        if not grupos:
            return None
        mejor = max(grupos.items(), key=lambda kv: (len(kv[1]), -len(kv[0].parts)))[0]
        return ".".join(mejor.relative_to(self.main).parts)

    def _asignar_capas(self):
        if not self.base:
            return
        base_parts = tuple(self.base.split("."))
        for f in self.files:
            parts = Path(f.rel).parts[3:-1]  # src/main/java/ quitado
            if parts[: len(base_parts)] == base_parts and len(parts) > len(base_parts):
                resto = parts[len(base_parts):]
                if resto[0] in LAYERS:
                    f.layer, f.sub = resto[0], resto[1:]

    def dir_existe(self, *partes):
        if not self.base:
            return False
        return self.main.joinpath(*self.base.split("."), *partes).is_dir()

    def corto(self, rel):
        """Ruta relativa al paquete base (si aplica) para mostrarla al desarrollador."""
        if self.base:
            prefijo = "src/main/java/" + self.base.replace(".", "/") + "/"
            if rel.startswith(prefijo):
                return rel[len(prefijo):]
        return rel[len("src/main/java/"):] if rel.startswith("src/main/java/") else rel

    def listar(self, rels, maximo=8):
        cortos = [self.corto(r) for r in rels]
        extra = f" (+{len(cortos) - maximo} mas)" if len(cortos) > maximo else ""
        return ", ".join(cortos[:maximo]) + extra

    def ubicaciones(self, files):
        """Directorios (relativos al paquete base) donde viven hoy estas clases, con su cantidad."""
        cuenta = {}
        for f in files:
            d = self.corto(f.rel).rsplit("/", 1)[0] if "/" in self.corto(f.rel) else "(raiz)"
            cuenta[d] = cuenta.get(d, 0) + 1
        return "; ".join(f"{d} ({n})" for d, n in sorted(cuenta.items()))

    def archivos_capa(self, capa):
        return [f for f in self.files if f.layer == capa]

    def nombres_clases(self):
        return {f.name for f in self.files if f.name}

    def artifact_id(self):
        if not self.pom:
            return None
        sin_parent = re.sub(r"<parent>.*?</parent>", "", self.pom, flags=re.S)
        m = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", sin_parent)
        return m.group(1) if m else None
