from dataclasses import dataclass, field

CUMPLE = "CUMPLE"
PARCIAL = "PARCIAL"
NO_CUMPLE = "NO CUMPLE"
NO_APLICA = "NO APLICA"
REVISION = "REQUIERE REVISION MANUAL"

ESTADOS_PUNTUABLES = (CUMPLE, PARCIAL, NO_CUMPLE)
ORDEN_SEVERIDAD = {"critica": 0, "alta": 1, "media": 2, "baja": 3}


@dataclass
class Resultado:
    estado: str
    porcentaje: float | None = None
    obs: str = ""
    archivos: list = field(default_factory=list)
    donde: str = ""


def ratio(ok, total, fallos=(), ok_msg="OK", archivos=()):
    if total == 0:
        return Resultado(NO_APLICA, None, "No hay elementos a evaluar")
    pct = round(100 * ok / total, 1)
    estado = CUMPLE if ok == total else (PARCIAL if pct >= 50 else NO_CUMPLE)
    fallos = list(fallos)
    if fallos:
        extra = f" (+{len(fallos) - 5} mas)" if len(fallos) > 5 else ""
        obs = f"{total - ok} de {total} incumplen: " + ", ".join(fallos[:5]) + extra
    else:
        obs = ok_msg
    return Resultado(estado, pct, obs, list(archivos)[:200])


def binario(cumple, obs_ok="OK", obs_fail="No cumple", archivos=()):
    if cumple:
        return Resultado(CUMPLE, 100.0, obs_ok, list(archivos)[:200])
    return Resultado(NO_CUMPLE, 0.0, obs_fail, list(archivos)[:200])


def umbral(cantidad, obs_ok, obs_fail, limite_parcial=3, archivos=()):
    if cantidad == 0:
        return Resultado(CUMPLE, 100.0, obs_ok)
    estado, pct = (PARCIAL, 50.0) if cantidad <= limite_parcial else (NO_CUMPLE, 0.0)
    return Resultado(estado, pct, obs_fail.format(n=cantidad), list(archivos)[:200])


def calcular_score(resultados):
    """resultados: lista de (criterio, Resultado). Devuelve 0-100 o None si no hay nada puntuable."""
    num = den = 0.0
    for c, r in resultados:
        if r.estado in ESTADOS_PUNTUABLES and r.porcentaje is not None:
            peso = c.get("peso", 1)
            num += peso * r.porcentaje / 100.0
            den += peso
    return round(100 * num / den, 1) if den else None


@dataclass
class MicroResultado:
    nombre: str
    prs: str = ""
    rama: str = ""
    commit: str = ""
    ruta: str = ""
    resultados: list = field(default_factory=list)
    error: str = ""

    def score(self):
        return calcular_score(self.resultados)

    def conteo(self):
        cuenta = {CUMPLE: 0, PARCIAL: 0, NO_CUMPLE: 0, REVISION: 0, NO_APLICA: 0}
        for _, r in self.resultados:
            cuenta[r.estado] = cuenta.get(r.estado, 0) + 1
        return cuenta
