"""Reglas deterministicas (fase 1). Cada funcion recibe un Proyecto y devuelve un Resultado.
El id del criterio (criterios.yml, campo 'regla') selecciona la funcion."""
import re

from .modelo import NO_APLICA, NO_CUMPLE, PARCIAL, Resultado, binario, ratio, umbral
from .scan import LAYERS

REGISTRO = {}


def regla(nombre):
    def deco(fn):
        REGISTRO[nombre] = fn
        return fn
    return deco


def _sin_capa(capa):
    return Resultado(NO_APLICA, None, f"No existe la capa {capa}")


def _sin_estructura():
    return binario(False, obs_fail="No existe la estructura de capas domain/application/infrastructure (ver ARQ-01)")


def _nombres(files):
    return [f.name or f.rel for f in files]


# ---------------------------------------------------------------- estructura
@regla("arq_01")
def arq_01(p):
    if p.base is None:
        return binario(False, obs_fail="No se encontro un paquete con domain/application/infrastructure")
    faltan = [c for c in LAYERS if not p.dir_existe(c)]
    return ratio(3 - len(faltan), 3, [f"falta {c}" for c in faltan], ok_msg=f"Paquete base: {p.base}")


@regla("arq_02")
def arq_02(p):
    files = p.archivos_capa("domain")
    if not files:
        return _sin_capa("domain")
    malos = [f for f in files if any(
        i.startswith("org.springframework")
        or i.startswith(f"{p.base}.infrastructure")
        or i.startswith(f"{p.base}.application") for i in f.imports)]
    return ratio(len(files) - len(malos), len(files), _nombres(malos), archivos=[f.rel for f in malos])


PERSISTENCIA = {"Table", "Column", "Document", "Entity", "Field", "Indexed", "MappedCollection",
                "MappedSuperclass", "Embeddable"}


@regla("arq_03")
def arq_03(p):
    files = p.archivos_capa("domain")
    if not files:
        return _sin_capa("domain")
    malos = [f for f in files if f.all_annotations & PERSISTENCIA]
    return ratio(len(files) - len(malos), len(files), _nombres(malos), archivos=[f.rel for f in malos])


def _dir_binario(*partes, alt=None):
    def fn(p):
        if not p.base:
            return _sin_estructura()
        ok = p.dir_existe(*partes)
        existentes = []
        if not ok:
            capa = p.main.joinpath(*p.base.split("."), partes[0])
            existentes = sorted(d.name for d in capa.iterdir() if d.is_dir()) if capa.is_dir() else []
        return binario(ok, f"Existe {'/'.join(partes)}",
                       f"Falta {'/'.join(partes)} (subpaquetes de {partes[0]}: {', '.join(existentes) or 'ninguno'})")
    return fn


REGISTRO["arq_04"] = _dir_binario("domain", "model")
REGISTRO["arq_06"] = _dir_binario("application", "usecase")
REGISTRO["arq_07"] = _dir_binario("infrastructure", "adapter", "in", "rest")


def _varios_dirs(rutas, ok_msg):
    def fn(p):
        if not p.base:
            return _sin_estructura()
        faltan = ["/".join(r) for r in rutas if not p.dir_existe(*r)]
        return ratio(len(rutas) - len(faltan), len(rutas), [f"falta {x}" for x in faltan], ok_msg=ok_msg)
    return fn


REGISTRO["arq_05"] = _varios_dirs([("domain", "port", "in"), ("domain", "port", "out")],
                                  "domain/port/in y domain/port/out presentes")
REGISTRO["arq_08"] = _varios_dirs([("infrastructure", "adapter", "in", "rest", "dto", "request"),
                                   ("infrastructure", "adapter", "in", "rest", "dto", "response")],
                                  "dto/request y dto/response del adaptador REST presentes")


def _subdirs_persistencia(p):
    """Carpetas {tecnologia} bajo infrastructure/adapter/out/persistence (r2dbc, mongo, ...)."""
    raiz = p.main.joinpath(*p.base.split("."), "infrastructure", "adapter", "out", "persistence")
    return sorted(d for d in raiz.iterdir() if d.is_dir()) if raiz.is_dir() else []


@regla("arq_09")
def arq_09(p):
    """Un mapper por frontera: REST y persistencia."""
    if not p.base:
        return _sin_estructura()
    faltan = []
    if not p.dir_existe("infrastructure", "adapter", "in", "rest", "mapper"):
        faltan.append("falta infrastructure/adapter/in/rest/mapper")
    tecnologias = _subdirs_persistencia(p)
    if not any((d / "mapper").is_dir() for d in tecnologias):
        faltan.append("falta mapper dentro de infrastructure/adapter/out/persistence/{tecnologia}")
    return ratio(2 - len(faltan), 2, faltan, ok_msg="Mapper en la frontera REST y en la de persistencia")


@regla("arq_10")
def arq_10(p):
    """Persistencia dentro del adaptador de salida: entity|document + repository."""
    if not p.base:
        return _sin_estructura()
    tecnologias = _subdirs_persistencia(p)
    if not tecnologias:
        return binario(False, obs_fail="Falta infrastructure/adapter/out/persistence/{tecnologia}")
    ok_modelo = any((d / "entity").is_dir() or (d / "document").is_dir() for d in tecnologias)
    ok_repo = any((d / "repository").is_dir() for d in tecnologias)
    faltan = ([] if ok_modelo else ["falta entity o document"]) + ([] if ok_repo else ["falta repository"])
    return ratio(2 - len(faltan), 2, faltan, ok_msg="entity/document y repository dentro del adaptador de persistencia")


def _con_donde(nombre, selector, destino):
    """Envuelve una regla de estructura: si no cumple, indica donde viven hoy las clases y donde deben quedar."""
    base = REGISTRO[nombre]

    def fn(p):
        res = base(p)
        if res.estado in (NO_CUMPLE, PARCIAL):
            files = selector(p)
            if files:
                res.donde = f"Hoy en: {p.ubicaciones(files)}. Debe quedar en: {destino}"
                res.archivos = [f.rel for f in files]
            else:
                res.donde = f"No se encontraron clases de este tipo. Crear: {destino}"
        return res
    REGISTRO[nombre] = fn


def _dominio(p):
    if p.base:
        return p.archivos_capa("domain")
    return [f for f in p.files if re.search(r"/(model|models|entity|entities|domain)/", f.rel)]


def _puertos(p):
    return [f for f in p.files if f.kind == "interface" and f.layer in ("domain", "application")]


def _casos_de_uso(p):
    base = p.archivos_capa("application") if p.base else p.files
    return [f for f in base
            if f.annotations & {"Service"} or (f.name or "").endswith(("Service", "UseCase", "ServiceImpl"))]


def _controllers(p):
    return [f for f in p.files if f.kind == "class" and f.annotations & {"RestController", "Controller"}]


def _dtos(p):
    return [f for f in p.files if f.name and f.name.endswith(("Request", "Response", "Dto"))
            and (f.layer in ("infrastructure", "application") or not p.base)]


def _mappers(p):
    return [f for f in p.files if f.name and f.name.endswith("Mapper")]


def _persistencia(p):
    return [f for f in p.files if f.kind == "class" and f.annotations & {"Table", "Document", "Entity"}]


_con_donde("arq_04", _dominio, "domain/model/")
_con_donde("arq_05", _puertos, "domain/port/in/ (casos de uso) y domain/port/out/ (repositorio, cliente, storage)")
_con_donde("arq_06", _casos_de_uso, "application/usecase/")
_con_donde("arq_07", _controllers, "infrastructure/adapter/in/rest/controller/")
_con_donde("arq_08", _dtos, "infrastructure/adapter/in/rest/dto/request/ y .../response/")
_con_donde("arq_09", _mappers, "infrastructure/adapter/in/rest/mapper/ y infrastructure/adapter/out/persistence/{tecnologia}/mapper/")
_con_donde("arq_10", _persistencia,
           "infrastructure/adapter/out/persistence/{tecnologia}/entity/ (SQL) o .../document/ (Mongo)")


@regla("arq_11")
def arq_11(p):
    """application depende solo de domain: ningun import de infrastructure (los puertos invierten la dependencia)."""
    files = p.archivos_capa("application")
    if not files:
        return _sin_capa("application")
    infra = f"{p.base}.infrastructure"
    malos = [f for f in files if any(i.startswith(infra) for i in f.imports)]
    return ratio(len(files) - len(malos), len(files), _nombres(malos), archivos=[f.rel for f in malos])


# ---------------------------------------------------------------- nombres
def _por_anotacion(p, anotaciones, sufijo):
    objetivo = [f for f in p.files if f.kind == "class" and f.annotations & anotaciones]
    malos = [f for f in objetivo if not f.name.endswith(sufijo)]
    return ratio(len(objetivo) - len(malos), len(objetivo), _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_01")
def nom_01(p):
    return _por_anotacion(p, {"RestController", "Controller"}, "Rest")


@regla("nom_02")
def nom_02(p):
    """Casos de uso: clases @Service terminan en Service o UseCase."""
    objetivo = [f for f in p.files if f.kind == "class" and f.annotations & {"Service"}
                and f.layer in ("application", None, "")]
    malos = [f for f in objetivo if not f.name.endswith(("Service", "UseCase"))]
    return ratio(len(objetivo) - len(malos), len(objetivo), _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_08")
def nom_08(p):
    """Puertos: los de entrada terminan en UseCase; los de salida, en Port."""
    entrada = [f for f in p.files if f.kind == "interface" and f.layer == "domain" and f.sub[:2] == ("port", "in")]
    salida = [f for f in p.files if f.kind == "interface" and f.layer == "domain" and f.sub[:2] == ("port", "out")]
    malos = [f for f in entrada if not f.name.endswith("UseCase")] + [f for f in salida if not f.name.endswith("Port")]
    total = len(entrada) + len(salida)
    if total == 0:
        return Resultado(NO_APLICA, None, "No hay puertos en domain/port")
    return ratio(total - len(malos), total, _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_03")
def nom_03(p):
    interfaces = [f for f in p.files if f.kind == "interface"]
    malos = [f for f in interfaces if re.match(r"^I[A-Z]", f.name)]
    return ratio(len(interfaces) - len(malos), len(interfaces), _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_04")
def nom_04(p):
    pat = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$")
    paquetes = sorted({f.package for f in p.files if f.package})
    malos = [x for x in paquetes if not pat.match(x)]
    return ratio(len(paquetes) - len(malos), len(paquetes), malos)


@regla("nom_05")
def nom_05(p):
    objetivo, malos = [], []
    for f in p.files:
        if f.kind != "class":
            continue
        if "Document" in f.annotations:
            objetivo.append(f)
            if not f.name.endswith("Document"):
                malos.append(f)
        elif f.annotations & {"Table", "Entity"}:
            objetivo.append(f)
            if not f.name.endswith("Entity"):
                malos.append(f)
    return ratio(len(objetivo) - len(malos), len(objetivo), _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_06")
def nom_06(p):
    repos = [f for f in p.files if f.kind == "interface" and re.search(r"Repository\s*<", f.supertypes)]
    malos = [f for f in repos if not f.name.endswith("Repository")]
    return ratio(len(repos) - len(malos), len(repos), _nombres(malos), archivos=[f.rel for f in malos])


@regla("nom_07")
def nom_07(p):
    aid = p.artifact_id()
    if aid is None:
        return Resultado(NO_APLICA, None, "No se encontro pom.xml")
    return binario(aid.startswith("vg-ms-"), f"artifactId = {aid}", f"artifactId = {aid} (debe empezar con vg-ms-)")


# ---------------------------------------------------------------- DTOs / excepciones
@regla("dto_01")
def dto_01(p):
    req = ["ResponseDto", "ErrorMessage", "ValidationError"]
    existentes = p.nombres_clases()
    faltan = [n for n in req if n not in existentes]
    return ratio(3 - len(faltan), 3, [f"falta {n}" for n in faltan], ok_msg="Los tres DTOs comunes existen")


@regla("exc_01")
def exc_01(p):
    cand = [f for f in p.files if f.name == "GlobalExceptionHandler"]
    if not cand:
        return binario(False, obs_fail="No existe GlobalExceptionHandler")
    f = cand[0]
    if f.annotations & {"RestControllerAdvice", "ControllerAdvice"}:
        return binario(True, "GlobalExceptionHandler con @RestControllerAdvice", archivos=[f.rel])
    return Resultado(PARCIAL, 50.0, "Existe pero no tiene @RestControllerAdvice", [f.rel])


@regla("exc_02")
def exc_02(p):
    """Las excepciones de negocio viven en domain/exception (las lanza el caso de uso)."""
    if not p.base:
        return _sin_estructura()
    propias = [f for f in p.files if f.name and f.name.endswith("Exception") and f.layer == "domain"]
    return binario(bool(propias), f"{len(propias)} excepcion(es) de negocio en domain/exception",
                   "No hay excepciones de negocio en domain/exception", archivos=[f.rel for f in propias])


def _contar(p, patron, excluir=None):
    archivos, total = [], 0
    for f in p.files:
        if excluir and excluir(f):
            continue
        n = len(re.findall(patron, f.code))
        if n:
            total += n
            archivos.append(f.rel)
    return total, archivos


@regla("exc_03")
def exc_03(p):
    n, archivos = _contar(p, r"throw\s+new\s+RuntimeException\s*\(")
    return umbral(n, "Sin RuntimeException generica", "{n} usos de 'throw new RuntimeException('", archivos=archivos)


@regla("exc_04")
def exc_04(p):
    handlers = [f for f in p.files if f.name and f.name.endswith("ExceptionHandler")]
    if not handlers:
        return binario(False, obs_fail="No hay handlers de excepcion")
    ok = any(re.search(r"DataIntegrityViolationException|DuplicateKeyException", f.code) for f in handlers)
    return binario(ok, "Maneja errores de integridad", "Ningun handler cubre DataIntegrityViolationException/DuplicateKeyException",
                   archivos=[f.rel for f in handlers])


@regla("exc_05")
def exc_05(p):
    n, archivos = _contar(p, r"getMessage\(\)\s*\.\s*(?:contains|equals|equalsIgnoreCase|startsWith|endsWith|matches)\s*\(",
                          excluir=lambda f: bool(f.name and f.name.endswith("ExceptionHandler")))
    return umbral(n, "Sin comparaciones sobre getMessage()", "{n} comparaciones sobre getMessage()", limite_parcial=0, archivos=archivos)


# ---------------------------------------------------------------- persistencia
@regla("per_01")
def per_01(p):
    if p.pom is None:
        return Resultado(NO_APLICA, None, "No se encontro pom.xml")
    webflux = "spring-boot-starter-webflux" in p.pom
    bloqueantes = [a for a in ("spring-boot-starter-web</", "spring-boot-starter-data-jpa", "spring-boot-starter-jdbc")
                   if a in p.pom]
    if webflux and not bloqueantes:
        return binario(True, "WebFlux sin dependencias bloqueantes")
    if webflux:
        return Resultado(PARCIAL, 50.0, f"WebFlux presente pero tambien: {', '.join(b.rstrip('</') for b in bloqueantes)}")
    return binario(False, obs_fail="No usa spring-boot-starter-webflux")


@regla("per_02")
def per_02(p):
    existentes = p.nombres_clases()
    return binario(bool(existentes & {"BaseDocument", "BaseEntity"}), "Existe clase base de auditoria",
                   "No existe BaseDocument/BaseEntity")


# ---------------------------------------------------------------- configuracion
_HOST_FIJO = re.compile(r"https?://[A-Za-z0-9.:-]*vallegrande\.edu\.pe[^\s\"']*")


@regla("cfg_01")
def cfg_01(p):
    """Las URLs de lab.vallegrande.edu.pe llegan por variable de entorno (${VAR:valor}), no escritas fijas."""
    archivos, total = [], 0
    destinos = list((p.root / "src" / "main" / "resources").glob("application*.y*ml")) + \
        list((p.root / "src" / "main" / "resources").glob("application*.properties"))
    for ruta in destinos:
        for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
            s = linea.strip()
            if s.startswith("#") or "${" in s:
                continue
            if _HOST_FIJO.search(s):
                total += 1
                rel = ruta.relative_to(p.root).as_posix()
                if rel not in archivos:
                    archivos.append(rel)
    for f in p.files:
        hallazgos = [m for m in _HOST_FIJO.findall(f.code)]
        if hallazgos:
            total += len(hallazgos)
            archivos.append(f.rel)
    return umbral(total, "Sin URLs del laboratorio escritas fijas", "{n} URL(s) de vallegrande.edu.pe escritas fijas",
                  limite_parcial=0, archivos=archivos)
