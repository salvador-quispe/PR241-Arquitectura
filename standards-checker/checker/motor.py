import yaml

from .ia import cargar_modelo
from .modelo import NO_CUMPLE, PARCIAL, REVISION, Resultado
from .reglas import REGISTRO


def cargar_criterios(ruta):
    with open(ruta, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("version"), data.get("estandar", ""), data["criterios"]


def evaluar_micro(proyecto, criterios, modelo=None):
    """Evalua todos los criterios. Un error tecnico nunca detiene la ejecucion: queda como revision manual."""
    modelo = modelo or cargar_modelo()
    resultados = []
    for c in criterios:
        try:
            if c["tipo"] == "ia":
                r = modelo.evaluar(c, proyecto)
            else:
                r = REGISTRO[c["regla"]](proyecto)
        except Exception as e:  # noqa: BLE001 - politica: nunca detenerse por un fallo
            r = Resultado(REVISION, None, f"Error tecnico: {type(e).__name__}: {e}")
        if not r.donde and r.archivos and r.estado in (NO_CUMPLE, PARCIAL):
            r.donde = proyecto.listar(r.archivos)
        resultados.append((c, r))
    return resultados
